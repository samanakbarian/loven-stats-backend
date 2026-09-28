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
-- Rapporten kortar langa namn ("EKESTÅHL-JONSSON Luka") och handelserna
-- skiljer namnar at med position ("Forsberg, Fredrik (RW)"). Namnet anvands
-- nar det finns i uppstallningen, annars troja och lag.
box AS (
  SELECT b.game_id,
         COALESCE(ln.player_key, lnr.player_key, b.player_name) AS player_key,
         b.team_name,
         b.shots, b.official_plus_minus, b.faceoffs_won, b.faceoffs_lost, b.faceoff_pct,
         b.pim AS official_pim
  FROM `@PROJECT@.core.game_boxscore` b
  LEFT JOIN (SELECT DISTINCT game_id, player_key FROM lineup) ln
    ON ln.game_id = b.game_id AND ln.player_key = b.player_name
  LEFT JOIN (
    SELECT game_id, team_name, player_number, ANY_VALUE(player_key) AS player_key
    FROM lineup GROUP BY game_id, team_name, player_number
  ) lnr
    ON lnr.game_id = b.game_id AND lnr.team_name = b.team_name
   AND lnr.player_number = b.player_number
  WHERE b.role = 'skater' AND b.player_name IS NOT NULL
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
),
vara AS (
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
GROUP BY k.game_id, k.season_group_id, k.player_key
),
-- Seriens övriga matcher (backlogg 38). Mål, assist och utvisningar står
-- med namn i händelserna och går att räkna direkt. Laget kommer ur
-- händelsens lagkod via core.match_team_code. På isen kräver uppställningen,
-- som ännu bara hämtas för våra matcher: de fälten är NULL, inte noll.
-- Räknas som raderna ovan, avgörande straffar inräknade, så att hela serien
-- mäts lika. Avstämningen i check_player_scoring visar skillnaden mot
-- Swehockeys säsongstotaler.
serie_ev AS (
  SELECT e.*, c.team_name AS lag
  FROM `@PROJECT@.core.match_events` e
  LEFT JOIN `@PROJECT@.core.match_team_code` c
    ON c.game_id = e.game_id AND c.team_code = e.team_code
  WHERE NOT e.is_ours
),
serie_rader AS (
  SELECT game_id, season_group_id, player_name AS player_key, lag, 1 AS goals, 0 AS assists, 0 AS pim, 0 AS penalties
  FROM serie_ev WHERE event_type = 'goal' AND player_name IS NOT NULL
  UNION ALL
  SELECT game_id, season_group_id, assist1_name, lag, 0, 1, 0, 0
  FROM serie_ev WHERE event_type = 'goal' AND assist1_name IS NOT NULL
  UNION ALL
  SELECT game_id, season_group_id, assist2_name, lag, 0, 1, 0, 0
  FROM serie_ev WHERE event_type = 'goal' AND assist2_name IS NOT NULL
  UNION ALL
  SELECT game_id, season_group_id, player_name, lag, 0, 0, IFNULL(penalty_minutes, 0), 1
  FROM serie_ev WHERE event_type = 'penalty' AND player_name IS NOT NULL
),
serie AS (
  SELECT game_id, season_group_id, player_key, MAX(lag) AS team_key,
         SUM(goals) AS goals, SUM(assists) AS assists,
         SUM(pim) AS pim, SUM(penalties) AS penalties
  FROM serie_rader
  GROUP BY game_id, season_group_id, player_key
)
SELECT v.* REPLACE (SAFE_CAST(v.game_id AS INT64) AS game_id,
                   SAFE_CAST(v.season_group_id AS INT64) AS season_group_id),
       TRUE AS is_ours_game
FROM vara v
UNION ALL
SELECT
  game_id, season_group_id, player_key, team_key,
  goals, assists, goals + assists AS points, pim, penalties,
  CAST(NULL AS INT64) AS gf_on, CAST(NULL AS INT64) AS ga_on, CAST(NULL AS INT64) AS plus_minus_on_ice,
  CAST(NULL AS INT64) AS gf_on_ev, CAST(NULL AS INT64) AS ga_on_ev, CAST(NULL AS INT64) AS plus_minus,
  CAST(NULL AS INT64) AS shots, CAST(NULL AS INT64) AS official_plus_minus,
  CAST(NULL AS INT64) AS faceoffs_won, CAST(NULL AS INT64) AS faceoffs_lost,
  CAST(NULL AS FLOAT64) AS faceoff_pct,
  FALSE AS has_report, CAST(NULL AS BOOL) AS in_lineup,
  FALSE AS is_ours_game
FROM serie;

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
  g.match_date, g.venue, g.spectators, g.went_beyond_regulation,
  s.is_ours AS is_ours_game
-- Hela serien sedan backlogg 38. API:t läser vyn per game_id.
FROM `@PROJECT@.core.match_team_summary` s
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = s.game_id;

-- Målvakt x match. Lagkoden saknas i ungefär var femte rad hos Swehockey, så
-- laget hämtas ur uppställningens målvaktsblock i stället.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_goalie_game` AS
SELECT
  SAFE_CAST(k.game_id AS INT64) AS game_id,
  SAFE_CAST(k.season_group_id AS INT64) AS season_group_id,
  CAST(k.goalie_name AS STRING) AS player_key,
  CAST(COALESCE(l.team_name, k.team_code) AS STRING) AS team_key,
  SAFE_CAST(k.goalie_number AS INT64) AS jersey_number,
  SAFE_CAST(k.shots_against AS INT64) AS shots_against,
  SAFE_CAST(k.saves AS INT64) AS saves,
  SAFE_CAST(k.goals_against AS INT64) AS goals_against,
  SAFE_CAST(k.save_pct AS FLOAT64) AS save_pct,
  CAST(b.time_on_ice AS STRING) AS time_on_ice,
  SAFE_CAST(b.shutout AS INT64) AS shutout,
  g.match_date,
  TRUE AS is_ours_game
FROM `@PROJECT@.core.game_goalies` k
LEFT JOIN (
  SELECT game_id, player_name, time_on_ice, shutout
  FROM `@PROJECT@.core.game_boxscore` WHERE role = 'goalie'
) b ON b.game_id = k.game_id AND b.player_name = k.goalie_name
LEFT JOIN `@PROJECT@.core.game_lineups` l
  ON l.game_id = k.game_id AND l.player_name = k.goalie_name AND l.block = 'goalie'
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = k.game_id
-- Seriens övriga matcher: laget ur lagkoden, speltid och nolla saknas
-- (de står i matchrapporten, som bara hämtas för våra).
UNION ALL
SELECT
  k.game_id, k.season_group_id, k.goalie_name, c.team_name, k.goalie_number,
  k.shots_against, k.saves, k.goals_against, k.save_pct,
  CAST(NULL AS STRING), CAST(NULL AS INT64),
  g.match_date,
  FALSE
FROM `@PROJECT@.core.match_goalies` k
LEFT JOIN `@PROJECT@.core.match_team_code` c
  ON c.game_id = k.game_id AND c.team_code = k.team_code
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = k.game_id
WHERE NOT k.is_ours;

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


-- ============================================== HELA SERIEN (backlogg 38) ==

-- Mål. En rad per mål i hela serien, med laget ur lagkoden. Straffslag och
-- avgörande straffar markeras: de ger inget plus/minus och avgörandet räknas
-- inte som mål i Swehockeys spelarstatistik.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_goal` AS
SELECT
  e.game_id,
  e.season_group_id,
  e.event_index,
  e.period,
  e.time,
  c.team_name AS team_key,
  CASE WHEN c.team_name = e.home_team THEN e.away_team
       WHEN c.team_name = e.away_team THEN e.home_team END AS opponent_key,
  e.player_name AS scorer_key,
  e.assist1_name AS assist1_key,
  e.assist2_name AS assist2_key,
  e.home_goals,
  e.away_goals,
  e.score_state,
  IFNULL(e.is_power_play, FALSE) AS is_power_play,
  IFNULL(e.is_short_handed, FALSE) AS is_short_handed,
  IFNULL(e.is_empty_net, FALSE) AS is_empty_net,
  COALESCE(REGEXP_CONTAINS(e.score_state, r'\(PS\)'), FALSE) AS is_penalty_shot,
  COALESCE(REGEXP_CONTAINS(e.score_state, r'\(GWS\)'), FALSE) AS is_shootout_winner,
  e.on_ice_for,
  e.on_ice_against,
  g.match_date,
  e.is_ours AS is_ours_game
FROM `@PROJECT@.core.match_events` e
LEFT JOIN `@PROJECT@.core.match_team_code` c
  ON c.game_id = e.game_id AND c.team_code = e.team_code
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = e.game_id
WHERE e.event_type = 'goal';

-- Utvisningar. En rad per utvisning i hela serien.
CREATE OR REPLACE VIEW `@PROJECT@.marts.fact_penalty` AS
SELECT
  e.game_id,
  e.season_group_id,
  e.event_index,
  e.period,
  e.time,
  c.team_name AS team_key,
  e.player_name AS player_key,
  e.penalty_minutes,
  e.detail AS reason,
  g.match_date,
  e.is_ours AS is_ours_game
FROM `@PROJECT@.core.match_events` e
LEFT JOIN `@PROJECT@.core.match_team_code` c
  ON c.game_id = e.game_id AND c.team_code = e.team_code
LEFT JOIN `@PROJECT@.marts.dim_game` g ON g.game_id = e.game_id
WHERE e.event_type = 'penalty';

-- Täckning per säsong: spelade matcher mot matcher med händelser,
-- sammanfattning och målvakter. Avstämningen nedan gäller bara säsonger där
-- alla spelade matcher har händelser; annars blir varje spelare fel.
CREATE OR REPLACE VIEW `@PROJECT@.marts.check_coverage` AS
WITH spelade AS (
  SELECT SAFE_CAST(season_group_id AS INT64) AS season_group_id,
         SAFE_CAST(game_id AS INT64) AS game_id
  FROM `@PROJECT@.marts.dim_game`
  WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
),
ev AS (SELECT DISTINCT SAFE_CAST(game_id AS INT64) AS game_id FROM `@PROJECT@.core.match_events`),
sam AS (SELECT DISTINCT SAFE_CAST(game_id AS INT64) AS game_id FROM `@PROJECT@.core.match_team_summary`),
mv AS (SELECT DISTINCT SAFE_CAST(game_id AS INT64) AS game_id FROM `@PROJECT@.core.match_goalies`)
SELECT
  s.season_group_id,
  COUNT(*) AS spelade,
  COUNTIF(ev.game_id IS NOT NULL) AS med_handelser,
  COUNTIF(sam.game_id IS NOT NULL) AS med_sammanfattning,
  COUNTIF(mv.game_id IS NOT NULL) AS med_malvakter,
  COUNTIF(ev.game_id IS NOT NULL) = COUNT(*) AS komplett
FROM spelade s
LEFT JOIN ev USING (game_id)
LEFT JOIN sam USING (game_id)
LEFT JOIN mv USING (game_id)
GROUP BY s.season_group_id;

-- Avstämning: varje spelares mål och assist räknade ur matcherna mot
-- Swehockeys egna säsongstotaler. En rad per spelare där något skiljer.
-- Prövar hela kedjan händelse → spelarnyckel → spelare, och ger listan över
-- namnvarianter och saknade matcher.
CREATE OR REPLACE VIEW `@PROJECT@.marts.check_player_scoring` AS
WITH matcher AS (
  SELECT SAFE_CAST(season_group_id AS INT64) AS season_group_id, player_key,
         SUM(goals) AS mal, SUM(assists) AS assist,
         COUNT(DISTINCT game_id) AS matcher_med_handelse
  FROM `@PROJECT@.marts.fact_player_game`
  WHERE player_key != 'Team penalty'
    AND season_group_id IN (
    SELECT season_group_id FROM `@PROJECT@.marts.check_coverage` WHERE komplett)
  GROUP BY 1, player_key
),
straffar AS (
  SELECT season_group_id, scorer_key AS player_key, COUNT(*) AS avgorande
  FROM `@PROJECT@.marts.fact_goal`
  WHERE is_shootout_winner AND scorer_key IS NOT NULL
  GROUP BY season_group_id, scorer_key
),
facit AS (
  SELECT SAFE_CAST(season_group_id AS INT64) AS season_group_id, player_key,
         ANY_VALUE(team_key) AS team_key,
         SUM(SAFE_CAST(goals AS INT64)) AS mal,
         SUM(SAFE_CAST(assists AS INT64)) AS assist,
         SUM(SAFE_CAST(games_played AS INT64)) AS matcher
  FROM `@PROJECT@.marts.fact_player_season`
  WHERE SAFE_CAST(season_group_id AS INT64) IN (
    SELECT season_group_id FROM `@PROJECT@.marts.check_coverage` WHERE komplett)
  GROUP BY 1, player_key
)
SELECT
  COALESCE(f.season_group_id, m.season_group_id) AS season_group_id,
  COALESCE(f.player_key, m.player_key) AS player_key,
  f.team_key,
  f.matcher AS matcher_officiella,
  f.mal AS mal_officiella,
  m.mal AS mal_matcher,
  -- Straffläggningens avgörande mål räknas i Swehockeys spelarstatistik,
  -- som i våra matcher. Kolumnen visar hur många av målen som var sådana.
  IFNULL(s.avgorande, 0) AS avgorande_straffar,
  f.assist AS assist_officiella,
  m.assist AS assist_matcher,
  CASE
    WHEN f.player_key IS NULL THEN 'saknas i säsongsstatistiken'
    WHEN m.player_key IS NULL THEN 'saknas i matcherna'
    ELSE 'skiljer'
  END AS typ
FROM facit f
FULL OUTER JOIN matcher m
  ON m.season_group_id = f.season_group_id AND m.player_key = f.player_key
LEFT JOIN straffar s
  ON s.season_group_id = COALESCE(f.season_group_id, m.season_group_id)
 AND s.player_key = COALESCE(f.player_key, m.player_key)
WHERE f.player_key IS NULL
   OR (m.player_key IS NULL AND IFNULL(f.mal, 0) + IFNULL(f.assist, 0) > 0)
   OR (m.player_key IS NOT NULL AND (
         IFNULL(m.mal, 0) != IFNULL(f.mal, 0)
         OR IFNULL(m.assist, 0) != IFNULL(f.assist, 0)));
