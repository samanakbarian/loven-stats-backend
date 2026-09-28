-- core: aktuellt tillstånd, avduplicerat en gång.
--
-- raw_sports är append-only och bär historiken. Varje läsning måste därför
-- välja senaste generationen, och det har gått fel två gånger: utvisningarna
-- tredubblades när analysfrågan summerade tre skörningar, och spelarnas
-- matchlogg multiplicerade poäng på samma sätt. Talen blev fel utan att en
-- enda rad var trasig.
--
-- Vyerna här gör avdupliceringen på ett ställe. API:t läser core, aldrig
-- raw_sports, och då går felet inte att göra.
--
-- Avdupliceringsnyckeln följer hur scrapern skriver:
--   matchtabellerna  skrivs en hel match i taget  -> senaste per game_id
--   ögonblicksbilder skrivs en hel bild i taget   -> senaste per season_group_id
--
-- QUALIFY ... = MAX(scraped_at) OVER (...) behåller hela den senaste
-- generationen. ROW_NUMBER hade behållit en rad per match, vilket är fel:
-- en match har många händelser.

CREATE SCHEMA IF NOT EXISTS `@PROJECT@.core` OPTIONS(location = 'europe-west1');


-- De tva rapporttabellerna skapas har med explicit schema. En vy kan inte
-- byggas over en tabell som inte finns, och de skrivs forst nar en match
-- faktiskt spelats — sa en fardig deploy fore seriestart foll pa
-- "Table raw_sports.swehockey_game_boxscore was not found".
CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_game_boxscore` (
  game_id INT64, season_group_id INT64, match_date STRING,
  team_side INT64, team_name STRING, role STRING,
  player_number INT64, player_name STRING,
  shots INT64, goals INT64, assists INT64, points INT64,
  official_plus_minus INT64, pim INT64,
  faceoffs_won INT64, faceoffs_lost INT64, faceoff_pct FLOAT64,
  saves INT64, goals_against INT64, shots_against INT64,
  save_pct FLOAT64, shutout INT64, time_on_ice STRING,
  source_etag STRING, report_url STRING,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_player_bio` (
  game_id INT64, season_group_id INT64,
  team_side INT64, team_name STRING,
  position STRING, player_number INT64, player_name STRING,
  birthdate STRING, is_captain BOOL, is_assistant_captain BOOL,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

-- Seriens ovriga matcher (feature 26) och Swehockeys lagstatistik. Egna
-- tabeller: vara matchtabeller lases utan lagfilter pa ett tiotal stallen och
-- hade fatt 300 frammande matcher i sina summor. Schemat ar detsamma som
-- scraperns rader ger, sa laddningen och vyn ar overens fran start.
CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_league_game_events` (
  game_id INT64, season_group_id INT64, match_date STRING,
  event_index INT64, event_type STRING, period INT64, time STRING,
  team_code STRING, player_number INT64, player_name STRING,
  assist1_number INT64, assist1_name STRING, assist2_number INT64, assist2_name STRING,
  score_state STRING, home_goals INT64, away_goals INT64,
  is_power_play BOOL, is_short_handed BOOL, is_empty_net BOOL, is_game_winning_shot BOOL,
  penalty_minutes INT64, detail STRING, on_ice_for STRING, on_ice_against STRING,
  home_team STRING, away_team STRING,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_league_game_summary` (
  game_id INT64, season_group_id INT64, match_date STRING,
  is_home BOOL, team_name STRING, home_team STRING, away_team STRING,
  shots INT64, shots_by_period STRING, shooting_pct FLOAT64,
  saves INT64, saves_by_period STRING, save_pct FLOAT64, pdo FLOAT64,
  pim INT64, pim_by_period STRING, pp_pct FLOAT64, pp_time STRING,
  spectators INT64,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_league_game_goalies` (
  game_id INT64, season_group_id INT64, match_date STRING,
  team_code STRING, goalie_number INT64, goalie_name STRING,
  save_pct FLOAT64, saves INT64, shots_against INT64, goals_against INT64,
  home_team STRING, away_team STRING,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

-- Uppställningen för seriens övriga matcher (backlogg 38, steg 2). Samma
-- fält som vår swehockey_game_lineups.
CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_league_game_lineups` (
  game_id INT64, season_group_id INT64, match_date STRING,
  team_name STRING, jersey_colour STRING, home_team STRING, away_team STRING,
  block STRING, line_number INT64, player_number INT64, player_name STRING,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.swehockey_team_stats` (
  season_group_id INT64, page STRING, section STRING, grp STRING,
  team_code STRING, team_name STRING, rank INT64, games_played INT64,
  metric STRING, value FLOAT64, value_text STRING,
  source STRING, content_hash STRING, run_id STRING, source_url STRING,
  scraped_at TIMESTAMP
);

-- ---------------------------------------------------------------- matcher --

CREATE OR REPLACE VIEW `@PROJECT@.core.game_events` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_game_events`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.game_team_summary` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_game_summary`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.game_goalies` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_game_goalies`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.game_lineups` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_game_lineups`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

-- Spelarstatistik ur matchrapporten: skott, tekningar, officiellt +/- och
-- målvakternas speltid. Skrivs bara när rapportens ETag ändrats.
CREATE OR REPLACE VIEW `@PROJECT@.core.game_boxscore` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_game_boxscore`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

-- Seriens ovriga matcher. Samma avduplicering som vara: senaste per match.
-- Vara egna finns INTE har. Den som vill ha hela serien laser bada.
CREATE OR REPLACE VIEW `@PROJECT@.core.league_game_events` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_league_game_events`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.league_game_summary` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_league_game_summary`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.league_game_goalies` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_league_game_goalies`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.league_game_lineups` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_league_game_lineups`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY game_id);

-- --------------------------------------------------------- ögonblicksbilder --

CREATE OR REPLACE VIEW `@PROJECT@.core.schedule` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_schedule`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.standings` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_standings`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.player_season_stats` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_player_stats`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.goalie_season_stats` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_goalie_stats`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.roster` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_roster`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

-- Födelsedatum, position och kaptensbindel ur trupprapporten. Innehållet är
-- per lag och säsong, så det behandlas som en ögonblicksbild.
-- Swehockeys lagstatistik, en rad per lag, avsnitt och matt.
CREATE OR REPLACE VIEW `@PROJECT@.core.team_stats` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_team_stats`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

CREATE OR REPLACE VIEW `@PROJECT@.core.player_bio` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_player_bio`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id);

-- Säsongsregistret skrivs med MERGE, inte append. Det behöver ingen
-- avduplicering och tas med bara för att core ska vara hela ytan API:t läser.
CREATE OR REPLACE VIEW `@PROJECT@.core.season` AS
SELECT * FROM `@PROJECT@.raw_sports.swehockey_seasons`;

-- ---------------------------------------------------------------- historik --

-- Tabellen som den såg ut vid varje tillfälle den ändrades, en rad per lag och
-- dag. Nu när ögonblicksbilder bara skrivs när de faktiskt ändrats är en ny
-- generation liktydig med en spelad omgång.
--
-- OBS: detta går inte att rekonstruera bakåt. Avslutade säsonger har skrapats
-- om i efterhand, och varje sådan generation bär sluttabellen med ett färskt
-- scraped_at. För "tabellplacering över tid" i redan spelade säsonger måste
-- ställningen härledas ur matchresultaten i core.schedule i stället.
CREATE OR REPLACE VIEW `@PROJECT@.core.standings_history` AS
SELECT *
FROM (
  SELECT *, DATE(scraped_at) AS snapshot_date
  FROM `@PROJECT@.raw_sports.swehockey_standings`
)
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY season_group_id, snapshot_date);


-- ------------------------------------------------------------------ nyheter --

-- Tabellen skapas här av samma skäl som rapporttabellerna ovan: en vy kan inte
-- byggas över något som inte finns, och nyhetsskörningen kör i en egen
-- Cloud Function som kan ha kört senast än den här deployen.
CREATE TABLE IF NOT EXISTS `@PROJECT@.raw_sports.news_articles` (
  article_id STRING, published_at TIMESTAMP, title STRING, tag STRING,
  publisher STRING, official BOOL, url STRING, scraped_at TIMESTAMP
);

-- Samma artikel ses om vid varje körning, så här räknas den en gång. Att den
-- setts flera gånger är däremot inte brus: first_seen_at är när uppgiften dök
-- upp hos oss, vilket är det svar man vill ha när ett rykte ska dateras.
--
-- MIN() räknas över hela partitionen innan QUALIFY filtrerar, så det första
-- tillfället överlever att bara sista generationen behålls.
CREATE OR REPLACE VIEW `@PROJECT@.core.news` AS
SELECT
  * EXCEPT (scraped_at),
  scraped_at AS last_seen_at,
  MIN(scraped_at) OVER (PARTITION BY article_id) AS first_seen_at
FROM `@PROJECT@.raw_sports.news_articles`
QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY article_id);

-- ------------------------------------------------------- hela serien (38) --
-- Våra matcher och seriens övriga i en uppsättning, med is_ours som skiljer.
-- Rå-tabellerna står kvar var för sig; historiken skrivs inte om. Kolumnerna
-- räknas upp och typas uttryckligen: de två tabellerna har vuxit fram vid
-- olika tidpunkter och har inte samma metakolumner. Se docs/DATAMODELL.md.
--
-- De gamla vyerna game_* och league_game_* står kvar tills ingen fråga läser
-- dem, så att ingen fråga byter betydelse under flytten.

CREATE OR REPLACE VIEW `@PROJECT@.core.match_events` AS
SELECT
  TRUE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  SAFE_CAST(event_index AS INT64) AS event_index,
  CAST(event_type AS STRING) AS event_type,
  SAFE_CAST(period AS INT64) AS period,
  CAST(time AS STRING) AS time,
  TRIM(CAST(team_code AS STRING)) AS team_code,
  CAST(home_team AS STRING) AS home_team,
  CAST(away_team AS STRING) AS away_team,
  SAFE_CAST(player_number AS INT64) AS player_number,
  CAST(player_name AS STRING) AS player_name,
  CAST(assist1_name AS STRING) AS assist1_name,
  CAST(assist2_name AS STRING) AS assist2_name,
  CAST(on_ice_for AS STRING) AS on_ice_for,
  CAST(on_ice_against AS STRING) AS on_ice_against,
  SAFE_CAST(penalty_minutes AS INT64) AS penalty_minutes,
  CAST(detail AS STRING) AS detail,
  CAST(score_state AS STRING) AS score_state,
  SAFE_CAST(home_goals AS INT64) AS home_goals,
  SAFE_CAST(away_goals AS INT64) AS away_goals,
  SAFE_CAST(is_power_play AS BOOL) AS is_power_play,
  SAFE_CAST(is_short_handed AS BOOL) AS is_short_handed,
  SAFE_CAST(is_empty_net AS BOOL) AS is_empty_net
FROM `@PROJECT@.core.game_events`
UNION ALL
SELECT
  FALSE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  SAFE_CAST(event_index AS INT64) AS event_index,
  CAST(event_type AS STRING) AS event_type,
  SAFE_CAST(period AS INT64) AS period,
  CAST(time AS STRING) AS time,
  TRIM(CAST(team_code AS STRING)) AS team_code,
  CAST(home_team AS STRING) AS home_team,
  CAST(away_team AS STRING) AS away_team,
  SAFE_CAST(player_number AS INT64) AS player_number,
  CAST(player_name AS STRING) AS player_name,
  CAST(assist1_name AS STRING) AS assist1_name,
  CAST(assist2_name AS STRING) AS assist2_name,
  CAST(on_ice_for AS STRING) AS on_ice_for,
  CAST(on_ice_against AS STRING) AS on_ice_against,
  SAFE_CAST(penalty_minutes AS INT64) AS penalty_minutes,
  CAST(detail AS STRING) AS detail,
  CAST(score_state AS STRING) AS score_state,
  SAFE_CAST(home_goals AS INT64) AS home_goals,
  SAFE_CAST(away_goals AS INT64) AS away_goals,
  SAFE_CAST(is_power_play AS BOOL) AS is_power_play,
  SAFE_CAST(is_short_handed AS BOOL) AS is_short_handed,
  SAFE_CAST(is_empty_net AS BOOL) AS is_empty_net
FROM `@PROJECT@.core.league_game_events`;

CREATE OR REPLACE VIEW `@PROJECT@.core.match_team_summary` AS
SELECT
  TRUE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  CAST(team_name AS STRING) AS team_name,
  SAFE_CAST(is_home AS BOOL) AS is_home,
  SAFE_CAST(shots AS INT64) AS shots,
  SAFE_CAST(saves AS INT64) AS saves,
  SAFE_CAST(pim AS INT64) AS pim,
  SAFE_CAST(pp_pct AS FLOAT64) AS pp_pct,
  CAST(pp_time AS STRING) AS pp_time,
  SAFE_CAST(shooting_pct AS FLOAT64) AS shooting_pct,
  SAFE_CAST(save_pct AS FLOAT64) AS save_pct,
  SAFE_CAST(pdo AS FLOAT64) AS pdo,
  CAST(shots_by_period AS STRING) AS shots_by_period,
  CAST(saves_by_period AS STRING) AS saves_by_period,
  CAST(pim_by_period AS STRING) AS pim_by_period
FROM `@PROJECT@.core.game_team_summary`
UNION ALL
SELECT
  FALSE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  CAST(team_name AS STRING) AS team_name,
  SAFE_CAST(is_home AS BOOL) AS is_home,
  SAFE_CAST(shots AS INT64) AS shots,
  SAFE_CAST(saves AS INT64) AS saves,
  SAFE_CAST(pim AS INT64) AS pim,
  SAFE_CAST(pp_pct AS FLOAT64) AS pp_pct,
  CAST(pp_time AS STRING) AS pp_time,
  SAFE_CAST(shooting_pct AS FLOAT64) AS shooting_pct,
  SAFE_CAST(save_pct AS FLOAT64) AS save_pct,
  SAFE_CAST(pdo AS FLOAT64) AS pdo,
  CAST(shots_by_period AS STRING) AS shots_by_period,
  CAST(saves_by_period AS STRING) AS saves_by_period,
  CAST(pim_by_period AS STRING) AS pim_by_period
FROM `@PROJECT@.core.league_game_summary`;

CREATE OR REPLACE VIEW `@PROJECT@.core.match_goalies` AS
SELECT
  TRUE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  TRIM(CAST(team_code AS STRING)) AS team_code,
  SAFE_CAST(goalie_number AS INT64) AS goalie_number,
  CAST(goalie_name AS STRING) AS goalie_name,
  SAFE_CAST(save_pct AS FLOAT64) AS save_pct,
  SAFE_CAST(saves AS INT64) AS saves,
  SAFE_CAST(shots_against AS INT64) AS shots_against,
  SAFE_CAST(goals_against AS INT64) AS goals_against
FROM `@PROJECT@.core.game_goalies`
UNION ALL
SELECT
  FALSE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  TRIM(CAST(team_code AS STRING)) AS team_code,
  SAFE_CAST(goalie_number AS INT64) AS goalie_number,
  CAST(goalie_name AS STRING) AS goalie_name,
  SAFE_CAST(save_pct AS FLOAT64) AS save_pct,
  SAFE_CAST(saves AS INT64) AS saves,
  SAFE_CAST(shots_against AS INT64) AS shots_against,
  SAFE_CAST(goals_against AS INT64) AS goals_against
FROM `@PROJECT@.core.league_game_goalies`;

CREATE OR REPLACE VIEW `@PROJECT@.core.match_lineups` AS
SELECT
  TRUE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  CAST(team_name AS STRING) AS team_name,
  CAST(jersey_colour AS STRING) AS jersey_colour,
  CAST(block AS STRING) AS block,
  SAFE_CAST(line_number AS INT64) AS line_number,
  SAFE_CAST(player_number AS INT64) AS player_number,
  CAST(player_name AS STRING) AS player_name
FROM `@PROJECT@.core.game_lineups`
UNION ALL
SELECT
  FALSE AS is_ours,
  SAFE_CAST(game_id AS INT64) AS game_id,
  SAFE_CAST(season_group_id AS INT64) AS season_group_id,
  CAST(team_name AS STRING) AS team_name,
  CAST(jersey_colour AS STRING) AS jersey_colour,
  CAST(block AS STRING) AS block,
  SAFE_CAST(line_number AS INT64) AS line_number,
  SAFE_CAST(player_number AS INT64) AS player_number,
  CAST(player_name AS STRING) AS player_name
FROM `@PROJECT@.core.league_game_lineups`;

-- Lagkod till lagnamn per säsong. Händelserna bär koden ("MIF"),
-- sammanfattningen namnet. Ett mål som ökar hemmalagets siffra är
-- hemmalagets, så varje mål är en röst och majoriteten vinner — samma regel
-- som serien.lagkoder i API:t.
CREATE OR REPLACE VIEW `@PROJECT@.core.team_code_map` AS
WITH mal AS (
  SELECT season_group_id, game_id, event_index, team_code, home_team, away_team,
         home_goals, away_goals,
         LAG(home_goals, 1, 0) OVER w AS home_fore,
         LAG(away_goals, 1, 0) OVER w AS away_fore
  FROM `@PROJECT@.core.match_events`
  WHERE event_type = 'goal' AND team_code IS NOT NULL AND team_code != ''
    AND home_goals IS NOT NULL AND away_goals IS NOT NULL
  WINDOW w AS (PARTITION BY game_id ORDER BY event_index)
),
rost AS (
  SELECT season_group_id, team_code,
         CASE
           WHEN home_goals > home_fore AND away_goals = away_fore THEN home_team
           WHEN away_goals > away_fore AND home_goals = home_fore THEN away_team
         END AS team_name
  FROM mal
)
SELECT season_group_id, team_code, team_name, COUNT(*) AS roster_antal
FROM rost
WHERE team_name IS NOT NULL
GROUP BY season_group_id, team_code, team_name
QUALIFY ROW_NUMBER() OVER (PARTITION BY season_group_id, team_code ORDER BY COUNT(*) DESC) = 1;

-- Lagkod till lagnamn per match. Ett lag som inte gjort mål än saknar kod i
-- säsongens karta; finns bara två koder i matchen och den ena är känd, hör
-- den okända till det andra laget.
CREATE OR REPLACE VIEW `@PROJECT@.core.match_team_code` AS
WITH koder AS (
  SELECT DISTINCT game_id, season_group_id, team_code, home_team, away_team
  FROM `@PROJECT@.core.match_events`
  WHERE team_code IS NOT NULL AND team_code != ''
),
kanda AS (
  SELECT k.*,
         IF(m.team_name IN (k.home_team, k.away_team), m.team_name, NULL) AS kand
  FROM koder k
  LEFT JOIN `@PROJECT@.core.team_code_map` m
    ON m.season_group_id = k.season_group_id AND m.team_code = k.team_code
)
SELECT game_id, season_group_id, team_code,
       COALESCE(
         kand,
         IF(COUNT(*) OVER (PARTITION BY game_id) = 2
            AND COUNTIF(kand IS NOT NULL) OVER (PARTITION BY game_id) = 1,
            IF(MAX(kand) OVER (PARTITION BY game_id) = home_team, away_team, home_team),
            NULL)
       ) AS team_name
FROM kanda;
