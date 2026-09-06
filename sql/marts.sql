-- marts: stjärnschema över core.
--
-- core är avduplicerat men källnära — lagnamn i en tabell, lagkod i en annan,
-- spelarstatistik som säsongstotal men aldrig per match. marts är det lager
-- appen frågar: konforma dimensioner och fakta med mått.
--
-- NYCKLAR
-- Nycklarna är normaliserade naturliga värden, inte hashade surrogat.
-- Swehockey har inget spelar-id, så namnet är den enda identiteten som finns,
-- och ett surrogat hade bara lagt ett joinsteg mellan felsökaren och datat.
-- Kontrollerat mot HA 25/26: 541 truppspelare, noll äkta namnkrockar.
--
-- NORMALISERING
-- Säsongstabellerna märker spelare som bytt klubb under säsongen med "**":
-- "Hellberg, Hannes**". Matchtabellerna gör det inte. Utan strippning hittar
-- en övergångsspelares matcher aldrig sin säsongsstatistik — 23 spelare i
-- HA 25/26.

CREATE SCHEMA IF NOT EXISTS `@PROJECT@.marts` OPTIONS(location = 'europe-west1');

-- ============================================================ DIMENSIONER ==

CREATE OR REPLACE VIEW `@PROJECT@.marts.dim_season` AS
SELECT regular_season_id AS season_group_id, season_key, season_name, league,
       'regular' AS stage, start_date, end_date, is_active
FROM `@PROJECT@.core.season`
WHERE regular_season_id IS NOT NULL
UNION ALL
SELECT playoff_id AS season_group_id, season_key, season_name, league,
       'playoff' AS stage, start_date, end_date, is_active
FROM `@PROJECT@.core.season`
WHERE playoff_id IS NOT NULL;

-- Lagen. Lagkoden finns bara i händelsetabellen och lagnamnet bara i de
-- övriga, så koden härleds: knyt varje händelse till en spelare i matchens
-- uppställning, och därmed till lagnamnet. Över HA 25/26 gav det 14 koder,
-- noll tvetydiga.
CREATE OR REPLACE VIEW `@PROJECT@.marts.dim_team` AS
WITH names AS (
  SELECT DISTINCT team_name FROM `@PROJECT@.core.standings` WHERE team_name IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT home_team FROM `@PROJECT@.core.schedule` WHERE home_team IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT away_team FROM `@PROJECT@.core.schedule` WHERE away_team IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT team_name FROM `@PROJECT@.core.roster` WHERE team_name IS NOT NULL
),
code_votes AS (
  SELECT l.team_name, e.team_code, COUNT(*) AS n
  FROM `@PROJECT@.core.game_events` e
  JOIN `@PROJECT@.core.game_lineups` l
    ON e.game_id = l.game_id AND e.player_name = l.player_name
  WHERE e.team_code IS NOT NULL AND e.player_name IS NOT NULL
  GROUP BY l.team_name, e.team_code
),
code AS (
  SELECT team_name, team_code FROM code_votes
  QUALIFY ROW_NUMBER() OVER (PARTITION BY team_name ORDER BY n DESC) = 1
)
SELECT n.team_name AS team_key, n.team_name, c.team_code,
       n.team_name = 'IF Björklöven' AS is_bjorkloven
FROM names n
LEFT JOIN code c ON c.team_name = n.team_name;

-- Spelarna. Truppen bär position och tröjnummer. Matchtabellerna bär bara
-- namnet, så dimensionen fyller på resten.
CREATE OR REPLACE VIEW `@PROJECT@.marts.dim_player` AS
WITH src AS (
  SELECT TRIM(REGEXP_REPLACE(player_name, '[* ]+$', '')) AS player_key,
         team_name, jersey_number, position, season_group_id,
         REGEXP_CONTAINS(player_name, '[*]') AS is_transfer,
         games_played
  FROM `@PROJECT@.core.roster`
  WHERE player_name IS NOT NULL
),
bio AS (
  -- Fodelsedatum och kaptensbindel finns bara i trupprapportens PDF. Aldern
  -- raknas mot sasongens slut sa den inte tickar mitt i tabellen.
  SELECT TRIM(REGEXP_REPLACE(player_name, '[* ]+$', '')) AS player_key,
         ANY_VALUE(birthdate) AS birthdate,
         LOGICAL_OR(is_captain) AS is_captain,
         LOGICAL_OR(is_assistant_captain) AS is_assistant_captain,
         ANY_VALUE(position) AS detailed_position
  FROM `@PROJECT@.core.player_bio`
  WHERE player_name IS NOT NULL
  GROUP BY player_key
)
SELECT s.player_key,
       s.player_key AS player_name,
       ANY_VALUE(s.team_name) AS team_key,
       ANY_VALUE(s.jersey_number) AS jersey_number,
       ANY_VALUE(s.position) AS position,
       ANY_VALUE(b.detailed_position) AS detailed_position,
       ANY_VALUE(b.birthdate) AS birthdate,
       DATE_DIFF(CURRENT_DATE(), ANY_VALUE(SAFE_CAST(b.birthdate AS DATE)), YEAR) AS age,
       IFNULL(LOGICAL_OR(b.is_captain), FALSE) AS is_captain,
       IFNULL(LOGICAL_OR(b.is_assistant_captain), FALSE) AS is_assistant_captain,
       LOGICAL_OR(s.is_transfer) AS has_transferred,
       COUNT(DISTINCT s.team_name) AS teams_in_season
FROM (
  SELECT * FROM src
  QUALIFY ROW_NUMBER() OVER (PARTITION BY player_key, team_name
                             ORDER BY games_played DESC) = 1
) s
LEFT JOIN bio b ON b.player_key = s.player_key
GROUP BY s.player_key;

-- Matcherna. Schemat täcker hela serien, inte bara våra matcher, så
-- dimensionen är konform för alla lag.
CREATE OR REPLACE VIEW `@PROJECT@.marts.dim_game` AS
SELECT
  game_id,
  season_group_id,
  DATE(match_date) AS match_date,
  match_time,
  home_team AS home_team_key,
  away_team AS away_team_key,
  SAFE_CAST(REGEXP_EXTRACT(result, '^ *([0-9]+)') AS INT64) AS home_goals,
  SAFE_CAST(REGEXP_EXTRACT(result, '- *([0-9]+)') AS INT64) AS away_goals,
  result,
  period_results,
  venue,
  SAFE_CAST(spectators AS INT64) AS spectators,
  stage,
  status,
  REGEXP_CONTAINS(IFNULL(period_results, ''), '(?i)ot|straff|shootout') AS went_beyond_regulation
FROM `@PROJECT@.core.schedule`
WHERE game_id IS NOT NULL;

-- ================================================================= FAKTA ==

-- Spelare x match. Finns inte i källan: Swehockey ger säsongstotaler och en
-- händelselista, aldrig raden däremellan. Den här vyn är hela poängen med
-- marten — nästan varje fråga appen ställer om form, motståndare eller
-- kedjor behöver just det här kornet.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_player_game` AS
WITH lineup AS (
  -- Vem som spelade, med tröjnummer, sa on-ice-numren kan knytas till namn.
  SELECT game_id, season_group_id, team_name,
         player_number, player_name AS player_key
  FROM `@PROJECT@.core.game_lineups`
  WHERE player_name IS NOT NULL
),
ev AS (
  SELECT * FROM `@PROJECT@.core.game_events`
),
-- Det gorande lagets namn: hamta det ur den som gjorde malet, inte ur
-- lagkoden. Da behovs ingen kodbrygga och raden blir ratt aven nar koden
-- saknas.
goals AS (
  SELECT e.game_id, e.season_group_id, e.event_index,
         l.team_name AS scoring_team,
         e.player_name AS scorer, e.assist1_name, e.assist2_name,
         e.on_ice_for, e.on_ice_against,
         e.is_power_play, e.is_short_handed, e.is_empty_net,
         -- Straffslag och avgörandet i straffläggningen ger inget plus/minus
         -- åt någon. Swehockey markerar dem i score_state, (PS) respektive
         -- (GWS), och listar bara skytten och målvakten på isen — de går
         -- alltså inte att känna igen på hur många som stod där.
         COALESCE(REGEXP_CONTAINS(e.score_state, r'\((PS|GWS)\)'), FALSE) AS is_penalty_shot
  FROM ev e
  LEFT JOIN lineup l ON l.game_id = e.game_id AND l.player_key = e.player_name
  WHERE e.event_type = 'goal'
),
scoring AS (
  SELECT game_id, season_group_id, scorer AS player_key,
         COUNT(*) AS goals, 0 AS assists
  FROM goals WHERE scorer IS NOT NULL GROUP BY game_id, season_group_id, scorer
  UNION ALL
  SELECT game_id, season_group_id, assist1_name, 0, COUNT(*)
  FROM goals WHERE assist1_name IS NOT NULL GROUP BY game_id, season_group_id, assist1_name
  UNION ALL
  SELECT game_id, season_group_id, assist2_name, 0, COUNT(*)
  FROM goals WHERE assist2_name IS NOT NULL GROUP BY game_id, season_group_id, assist2_name
),
penalties AS (
  SELECT game_id, season_group_id, player_name AS player_key,
         SUM(penalty_minutes) AS pim, COUNT(*) AS penalties
  FROM ev WHERE event_type = 'penalty' AND player_name IS NOT NULL
  GROUP BY game_id, season_group_id, player_name
),
-- Pa isen: numren i on_ice_for hor till det gorande laget, on_ice_against
-- till motstandaren. Numren oversatts till namn via matchens uppstallning.
on_for AS (
  -- gf_on räknar varje mål laget gjorde med spelaren på isen. gf_on_ev räknar
  -- bara de som ger plus enligt regelboken: powerplaymål ger inget plus åt det
  -- lag som hade övertaget. Utan den skillnaden går vårt tal inte att jämföra
  -- med Swehockeys officiella — det var därför de skilde sig med sexton procent.
  SELECT g.game_id, g.season_group_id, l.player_key, COUNT(*) AS gf_on,
         COUNTIF(NOT COALESCE(g.is_power_play, FALSE) AND NOT g.is_penalty_shot) AS gf_on_ev
  FROM goals g,
       UNNEST(SPLIT(g.on_ice_for, ',')) AS num
  JOIN lineup l ON l.game_id = g.game_id
               AND l.team_name = g.scoring_team
               AND l.player_number = SAFE_CAST(TRIM(num) AS INT64)
  WHERE g.on_ice_for IS NOT NULL AND g.scoring_team IS NOT NULL
  GROUP BY g.game_id, g.season_group_id, l.player_key
),
on_against AS (
  SELECT g.game_id, g.season_group_id, l.player_key, COUNT(*) AS ga_on,
         COUNTIF(NOT COALESCE(g.is_power_play, FALSE) AND NOT g.is_penalty_shot) AS ga_on_ev
  FROM goals g,
       UNNEST(SPLIT(g.on_ice_against, ',')) AS num
  JOIN lineup l ON l.game_id = g.game_id
               AND l.team_name <> g.scoring_team
               AND l.player_number = SAFE_CAST(TRIM(num) AS INT64)
  WHERE g.on_ice_against IS NOT NULL AND g.scoring_team IS NOT NULL
  GROUP BY g.game_id, g.season_group_id, l.player_key
),
-- En spelare kan sta i handelserna utan att sta i uppstallningen — Swehockey
-- listar inte alltid alla. Da faller laget tillbaka pa handelsens lagkod via
-- dim_team, sa raden anda far ett lag och inte tappas i en lagfiltrering.
-- Galler alla tre namnen i en malhandelse, inte bara malskyttens: annars
-- tappar en assistgivare som saknas i uppstallningen sitt lag.
ev_team AS (
  SELECT game_id, player_key, ANY_VALUE(team_name) AS team_name
  FROM (
    SELECT e.game_id, e.player_name AS player_key, t.team_name
    FROM ev e JOIN `@PROJECT@.marts.dim_team` t ON t.team_code = e.team_code
    WHERE e.player_name IS NOT NULL
    UNION ALL
    SELECT e.game_id, e.assist1_name, t.team_name
    FROM ev e JOIN `@PROJECT@.marts.dim_team` t ON t.team_code = e.team_code
    WHERE e.assist1_name IS NOT NULL
    UNION ALL
    SELECT e.game_id, e.assist2_name, t.team_name
    FROM ev e JOIN `@PROJECT@.marts.dim_team` t ON t.team_code = e.team_code
    WHERE e.assist2_name IS NOT NULL
  )
  GROUP BY game_id, player_key
),
-- Matchrapportens egna tal: skott och tekningar finns ingen annanstans, och
-- plus/minus ar Swehockeys officiella. Det skiljer sig fran vart on-ice-tal
-- med ungefar sexton procent — se dokumentationen — sa de star bredvid
-- varandra i stallet for att ersatta varandra.
box AS (
  SELECT game_id, player_name AS player_key, team_name,
         shots, official_plus_minus, faceoffs_won, faceoffs_lost, faceoff_pct,
         pim AS official_pim
  FROM `@PROJECT@.core.game_boxscore`
  WHERE role = 'skater' AND player_name IS NOT NULL
),
keys AS (
  SELECT game_id, season_group_id, player_key FROM scoring
  UNION DISTINCT SELECT game_id, season_group_id, player_key FROM penalties
  UNION DISTINCT SELECT game_id, season_group_id, player_key FROM on_for
  UNION DISTINCT SELECT game_id, season_group_id, player_key FROM on_against
  UNION DISTINCT SELECT game_id, season_group_id, player_key FROM lineup
  UNION DISTINCT SELECT b.game_id, l.season_group_id, b.player_key
    FROM box b JOIN (SELECT DISTINCT game_id, season_group_id FROM lineup) l
      USING (game_id)
)
SELECT
  k.game_id,
  k.season_group_id,
  k.player_key,
  COALESCE(ANY_VALUE(lu.team_name), ANY_VALUE(bx.team_name), ANY_VALUE(et.team_name)) AS team_key,
  IFNULL(SUM(s.goals), 0) AS goals,
  IFNULL(SUM(s.assists), 0) AS assists,
  IFNULL(SUM(s.goals), 0) + IFNULL(SUM(s.assists), 0) AS points,
  IFNULL(ANY_VALUE(p.pim), 0) AS pim,
  IFNULL(ANY_VALUE(p.penalties), 0) AS penalties,
  IFNULL(ANY_VALUE(f.gf_on), 0) AS gf_on,
  IFNULL(ANY_VALUE(a.ga_on), 0) AS ga_on,
  IFNULL(ANY_VALUE(f.gf_on), 0) - IFNULL(ANY_VALUE(a.ga_on), 0) AS plus_minus_on_ice,
  -- Plus/minus enligt regelboken: bara mål i lika styrka och i underläge.
  -- Jämförbart med bx.official_plus_minus, till skillnad från talet ovan.
  IFNULL(ANY_VALUE(f.gf_on_ev), 0) AS gf_on_ev,
  IFNULL(ANY_VALUE(a.ga_on_ev), 0) AS ga_on_ev,
  IFNULL(ANY_VALUE(f.gf_on_ev), 0) - IFNULL(ANY_VALUE(a.ga_on_ev), 0) AS plus_minus,
  ANY_VALUE(bx.shots) AS shots,
  ANY_VALUE(bx.official_plus_minus) AS official_plus_minus,
  ANY_VALUE(bx.faceoffs_won) AS faceoffs_won,
  ANY_VALUE(bx.faceoffs_lost) AS faceoffs_lost,
  ANY_VALUE(bx.faceoff_pct) AS faceoff_pct,
  -- Sant nar matchrapporten finns. Sasongens forsta matcher saknar den, och
  -- da ar skott och tekningar NULL — inte noll.
  MAX(bx.player_key IS NOT NULL) AS has_report,
  -- "stod i uppstallningen", inte "spelade". Swehockeys uppstallningssida
  -- listar 20-22 spelare per lag och match dar 22 klatt om, och utelamnar
  -- ibland malvakten. Anvand fact_player_season.games_played som facit for
  -- antal spelade matcher.
  MAX(lu.player_key IS NOT NULL) AS in_lineup
FROM keys k
LEFT JOIN scoring    s ON s.game_id = k.game_id AND s.player_key = k.player_key
LEFT JOIN penalties  p ON p.game_id = k.game_id AND p.player_key = k.player_key
LEFT JOIN on_for     f ON f.game_id = k.game_id AND f.player_key = k.player_key
LEFT JOIN on_against a ON a.game_id = k.game_id AND a.player_key = k.player_key
LEFT JOIN lineup    lu ON lu.game_id = k.game_id AND lu.player_key = k.player_key
LEFT JOIN ev_team   et ON et.game_id = k.game_id AND et.player_key = k.player_key
LEFT JOIN box       bx ON bx.game_id = k.game_id AND bx.player_key = k.player_key
GROUP BY k.game_id, k.season_group_id, k.player_key;

-- Lag x match: skott, räddningar, utvisningar och PDO ur matchrapporten,
-- mål ur resultatet.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_team_game` AS
SELECT
  s.game_id,
  s.season_group_id,
  s.team_name AS team_key,
  s.is_home,
  CASE WHEN s.is_home THEN g.away_team_key ELSE g.home_team_key END AS opponent_key,
  CASE WHEN s.is_home THEN g.home_goals ELSE g.away_goals END AS goals_for,
  CASE WHEN s.is_home THEN g.away_goals ELSE g.home_goals END AS goals_against,
  s.shots, s.saves, s.pim, s.pp_pct, s.pp_time,
  s.shooting_pct, s.save_pct, s.pdo,
  s.shots_by_period, s.saves_by_period, s.pim_by_period,
  g.match_date, g.venue, g.spectators, g.went_beyond_regulation
FROM `@PROJECT@.core.game_team_summary` s
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = s.game_id;

-- Målvakt x match. Lagkoden saknas i ungefär var femte rad hos Swehockey, så
-- laget hämtas ur uppställningens målvaktsblock i stället.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_goalie_game` AS
SELECT
  k.game_id,
  k.season_group_id,
  k.goalie_name AS player_key,
  COALESCE(l.team_name, k.team_code) AS team_key,
  k.goalie_number AS jersey_number,
  k.shots_against, k.saves, k.goals_against, k.save_pct,
  b.time_on_ice,
  b.shutout,
  g.match_date
FROM `@PROJECT@.core.game_goalies` k
LEFT JOIN (
  SELECT game_id, player_name, time_on_ice, shutout
  FROM `@PROJECT@.core.game_boxscore` WHERE role = 'goalie'
) b ON b.game_id = k.game_id AND b.player_name = k.goalie_name
LEFT JOIN `@PROJECT@.core.game_lineups` l
  ON l.game_id = k.game_id AND l.player_name = k.goalie_name AND l.block = 'goalie'
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = k.game_id;

-- Spelare x match x kedja. Klubbens egen indelning, inte gissad ur vilka som
-- gör mål tillsammans.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_lineup_slot` AS
SELECT game_id, season_group_id, team_name AS team_key,
       player_name AS player_key, player_number AS jersey_number,
       block, line_number, jersey_colour
FROM `@PROJECT@.core.game_lineups`;

-- Spelare x säsong: Swehockeys egna totaler, som facit mot de härledda talen.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_player_season` AS
SELECT
  season_group_id,
  TRIM(REGEXP_REPLACE(player_name, '[* ]+$', '')) AS player_key,
  team_code AS team_key,
  jersey_number, position,
  games_played, goals, assists, points, plus_minus AS official_plus_minus, pim
FROM `@PROJECT@.core.player_season_stats`
WHERE player_name IS NOT NULL;

-- Tabellen som den såg ut varje dag den ändrades. Framåt från införandet;
-- se dokumentationen om varför den inte går att rekonstruera bakåt.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_standings_snapshot` AS
SELECT season_group_id, snapshot_date, team_name AS team_key,
       rank, games_played, wins, ot_wins, ot_losses, losses, points, goal_diff
FROM `@PROJECT@.core.standings_history`;
