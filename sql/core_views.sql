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
