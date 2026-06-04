# Feature Backlog 2026

Senast uppdaterad: 2026-06-04
Galler for: `loven-stats-backend` som produktionskalla och `slutspel/frontend_v2` som konsument.

## Syfte

Detta dokument kompletterar `docs/ROADMAP.md` med en mer konkret feature-backlog.
Roadmapen beskriver leveransfaserna; detta dokument beskriver vad varje feature
behover i data, API, frontend och verifiering.

För avancerad hockeyanalys, machine learning, simuleringar, scoutinglager och
modellkrav, se även `docs/ADVANCED_HOCKEY_ANALYTICS_STACK_2026.md`.

## Nulage att utga fran

- Backend har redan `GET /api/v1/seasons`, `GET /api/v1/statistics`,
  `GET /api/v1/analytics`, `GET /api/silly-season`, `GET /api/v1/lovenlaget`,
  `GET /api/v1/x-feed` och `GET /api/v1/financials`.
- `GET /api/v1/statistics` och `GET /api/v1/analytics` laser fortfarande mest
  direkt fran `raw_sports.*` och bygger svar i Python.
- dbt-lagret har `staging`, `marts/core` och `serving`, men flera API-floden
  ar inte migrerade till `serving_*` annu.
- `slutspel/frontend_v2` anropar aven `/api/v1/current-state` och
  `/api/v1/sportradar/results`, som finns i gamla Node-servern men inte i
  FastAPI-backenden.
- Roster och matchcenter ar delvis mockade i frontend v2.
- `raw_sports.swehockey_seasons` ar central for sasongsstyrning.

## Prioriterad implementation

### Fas 1: Datagrund och snabb anvandarnytta

1. Historisk sasongsbackfill
2. Automatisk datakvalitetskontroll efter scraper-korning
3. Sasongsjamforelse side-by-side
4. Rolling 5/10/20-matchform
5. Laget just nu, datadriven startsida

### Fas 2: Fordjupad analys

1. Spelarutveckling over tid
2. Liga-genomsnitt och percentiler
3. PP/PK per period
4. Matchens momentum-kurva
5. Matchforklarare

### Fas 3: Avancerade features

1. Spelarroller och spelarprofiler
2. Head-to-head mot kommande motstandare
3. AI-sammanfattning per match
4. Export av statistik till CSV
5. xG-light och rinkvisualisering nar koordinatdata finns
6. Push-notiser vid milstolpar
7. Team strength rating och Monte Carlo-simuleringar
8. Modellregister med backtesting och data quality per modell

## Featuredetaljer

### 1. Historisk sasongsbackfill

Typ: Feature / Data Engineering
Prioritet: Hog
Primart repo: `loven-stats-backend`
Berorda omraden: BigQuery, Swehockey scraper, `backfill_season.py`, datavalidering

Beskrivning:
Ladda in historiska HockeyAllsvenskan-sasonger i BigQuery, initialt 2022/23,
2023/24 och 2024/25. Detta ar grundkravet for sasongsjamforelse,
spelarutveckling, modell-backtesting och mer trovärdiga SHL-projektioner.

Befintliga byggblock:
- `backfill_season.py` har redan logik for spelare, malvakter, tabell och schema.
- `raw_sports.swehockey_seasons` anvands av `lookup_season()` i `api/main.py`.
- `tests/test_data_validation.py` validerar redan dubbletter, schema-parsning och
  kanda kontrollvarden.

Saknas:
- En tydlig, aterstartbar korstrategi per sasong och season_group_id.
- Separat hantering for grundserie och slutspel.
- Kontrollfraga per laddad sasong for matcher, spelare, malvakter och events.
- Dokumenterad lista over historiska `season_key`, `regular_season_id` och
  `playoff_id`.

Foreslaget API/DB-kontrakt:
- `GET /api/v1/seasons` ska returnera alla laddade sasonger med `key`, `name`,
  `league`, `is_active`, `regular_season_id`, `playoff_id` och `data_quality`.
- `GET /api/v1/statistics?season=ha_2324` ska fungera for varje laddad sasong.
- Backfill ska skriva append-only till `raw_sports.*` och inte skriva over aktiv
  sasong.

Acceptanskriterier:
- Minst tre historiska sasonger finns i `raw_sports.swehockey_seasons`.
- Minst spelare, malvakter, tabell och schema finns per sasong.
- Backfill kan koras om utan att skapa dubbletter i spelarstatistik.
- Kontrolltester finns for antal matcher, antal spelare och minst ett kant
  spelarvarde per sasong.
- Frontend kan valja sasong via `season_key`.

### 2. Automatisk datakvalitetskontroll efter scraper-korning

Typ: Feature / Data Quality
Prioritet: Hog
Primart repo: `loven-stats-backend`
Berorda omraden: Scrapers, BigQuery, pytest/dbt tests, drift

Beskrivning:
Efter varje scraper- eller backfillkorning ska systemet kontrollera att datan ar
rimlig innan den anvands for produktinsikter.

Befintliga byggblock:
- `tests/test_data_validation.py` innehaller de forsta BigQuery-baserade
  kontrollerna.
- dbt har grundtester i `serving_models.yml` och `core_models.yml`.

Saknas:
- En samlad kvalitetsrapport per korning.
- Freshness-kontroller per kalla.
- Status som API/frontend kan visa utan att lasa tekniska loggar.

Foreslaget API/DB-kontrakt:
- Ny tabell: `raw_ops.data_quality_runs`.
- Ny endpoint: `GET /api/v1/ops/data-quality?season=...`.
- Varje kontroll ska ge `check_id`, `status`, `severity`, `message`,
  `observed_value`, `expected_value`, `run_at`.

Acceptanskriterier:
- Kontroller kor efter scraper/backfill.
- Dubbletter, null-nycklar, orimligt langa lagnamn och saknade matchdatum
  upptacks.
- Resultat loggas historiskt.
- Varningar kan visas i intern adminvy.
- Samma kontroller kan ateranvandas for historisk backfill.

### 3. Sasongsjamforelse side-by-side

Typ: Feature / Analytics
Prioritet: Hog
Primart repo: delat, backend for kontrakt och frontend for vy
Berorda omraden: `GET /api/v1/statistics`, `GET /api/v1/analytics`, Statistik-vyn

Beskrivning:
Gor det mojligt att jamfora Bjorklovens prestation mellan sasonger.

Befintliga byggblock:
- Frontendens Statistik-sida har redan sasongsval.
- Backend kan filtrera `statistics` och `analytics` pa `season`.

Saknas:
- Ett jamforbart, kompakt svar per sasong.
- Normaliserade nyckeltal, inte bara radtabeller.

Foreslaget API/DB-kontrakt:
- Ny endpoint: `GET /api/v1/season-compare?seasons=ha_2324,ha_2425,ha_2526`.
- Svar per sasong:
  - `record`
  - `points_per_game`
  - `goals_for_per_game`
  - `goals_against_per_game`
  - `power_play_pct`
  - `penalty_kill_pct`
  - `form_curve`
  - `data_quality`

Acceptanskriterier:
- Minst tva sasonger kan valjas.
- Backend returnerar samma schema for varje sasong.
- Frontend visar KPI side-by-side och minst en graf over matcher.
- Saknade nyckeltal visas som `null` med datakvalitetsforklaring.
- Jamforelsen kraschar inte nar en sasong saknar events.

### 4. Rolling 5/10/20-matchform

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: delat
Berorda omraden: `GET /api/v1/analytics`, `AnalyticsTabs`

Beskrivning:
Lat anvandaren valja fonsterbredd for formkurvor, exempelvis 5, 10 eller 20
matcher.

Befintliga byggblock:
- `GET /api/v1/analytics` beraknar redan en formmodul.
- `AnalyticsTabs` visar analysgrafer och cachear svar i sessionStorage.

Saknas:
- Query-parametern `window`.
- Frontendkontroller for fonsterbredd.
- Tydlig hantering nar sasongen har farre matcher an valt fonster.

Foreslaget API-kontrakt:
- `GET /api/v1/analytics?season=ha_2526&window=10`.
- Tillatna varden i forsta version: `5`, `10`, `20`.
- Default: `5`.

Acceptanskriterier:
- Backend validerar `window` och returnerar default vid ogiltigt varde.
- Grafen uppdateras nar anvandaren byter fonster.
- Borjan av sasongen beraknas med tillgangligt antal matcher.
- `meta.analytics_window` finns i svaret.

### 5. Laget just nu, datadriven startsida

Typ: Feature / Product Analytics
Prioritet: Hog
Primart repo: delat
Berorda omraden: `GET /api/v1/lovenlaget`, saknat `/api/v1/current-state`,
`slutspel/frontend_v2/src/hooks/useCurrentState.ts`

Beskrivning:
Gor startsidan till en faktisk nulagesbild: form, senaste matchens forklaring,
nasta match, trendbrott, formstarka spelare, varningssignaler och freshness.

Befintliga byggblock:
- `GET /api/v1/lovenlaget` finns i FastAPI.
- Gamla Node-servern har `/api/v1/current-state`.
- Frontend v2 anropar redan `/api/v1/current-state`, men FastAPI saknar den.

Saknas:
- Ett beslutat backend-kontrakt for current-state.
- Match- och rosterdata i FastAPI som ersatter frontendmockar.
- Prioriteringsregler for vilka insights som ska visas forst.

Foreslaget API-kontrakt:
- Antingen flytta current-state in i `GET /api/v1/lovenlaget`, eller skapa
  `GET /api/v1/current-state` i FastAPI. Undvik att bada lever olika schema.
- Svaret ska innehalla:
  - `headline`
  - `body`
  - `biggest_question`
  - `latest_signal`
  - `supporter_snack`
  - `next_watch`
  - `evidence`
  - `roster_summary`
  - `next_match`
  - `recent_form`
  - `meta`

Acceptanskriterier:
- Startsidan bygger pa riktig backenddata, inte hardkodade mockar.
- Minst fem dynamiska insights visas.
- Data uppdateras nar ny match- eller sillydata finns.
- Saknade kallor visas som explicit `data_quality` eller `freshness_status`.
- Frontend har bara ett primary current-state-kontrakt.

### 6. Spelarutveckling over tid

Typ: Feature / Analytics
Prioritet: Hog
Primart repo: backend for identitet, frontend for profilvy
Berorda omraden: player-id, crosswalk, spelarprofil

Beskrivning:
Visa hur en spelares prestation utvecklas over flera sasonger.

Befintliga byggblock:
- Swehockey-statistik har `player_name`, `team_id`, `season_group_id`.
- Warehouse-designen beskriver `player_id_crosswalk` och `dim_players`.

Saknas:
- Stabil spelaridentifiering over sasonger.
- Hantering av namnvarianter, nummerbyten och spelare med samma namn.
- Spelarprofilvy i frontend.

Foreslaget API/DB-kontrakt:
- Ny tabell eller modell: `dim_players` / `player_identity_crosswalk`.
- Ny endpoint: `GET /api/v1/players/{player_id}/history`.
- Svar grupperat pa `season_key`, med totals och per-match-serier nar data finns.

Acceptanskriterier:
- En spelare kan foljas over flera sasonger.
- Två personer med samma namn kan separeras.
- Osakra matchningar markeras med confidence.
- Frontend visar trendkurva for valda nyckeltal.
- Saknade sasonger visas explicit.

### 7. Liga-genomsnitt och percentiler

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: backend/dbt
Berorda omraden: BigQuery, `GET /api/v1/statistics`, spelar- och lagtabeller

Beskrivning:
Visa spelar- och lagstatistik i relation till ligans genomsnitt och percentiler.

Befintliga byggblock:
- Backend hamtar redan league-wide top scorers och goalies.
- Statistikvyn har tabeller som kan visa extra kolumner.

Saknas:
- Beraknade league benchmarks per sasong, position och minsta antal matcher.
- Percentiler i API-schema.

Foreslaget kontrakt:
- Utoka `GET /api/v1/statistics` med `benchmarks`.
- Alternativt skapa dbt-modeller:
  - `mart_player_season_percentiles`
  - `mart_team_season_benchmarks`

Acceptanskriterier:
- Backend returnerar ligasnitt och percentil per vald statistik.
- Minimumgrans for matcher anvands.
- Frontend visar benchmark begripligt.
- Ofullstandig ligadata markeras.

### 8. PP/PK per period

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: backend/dbt
Berorda omraden: `raw_sports.swehockey_game_events`, analytics, Statistik-vyn

Beskrivning:
Bryt ned power play och penalty kill per period.

Befintliga byggblock:
- `GET /api/v1/analytics` laser `swehockey_game_events` nar game ids finns.
- Analytics har redan `special_teams` som modul.

Saknas:
- Tillforlitlig identifiering av PP/PK-mojligheter fran events.
- Periodiserad output.

Foreslaget kontrakt:
- `modules.special_teams_by_period` i `GET /api/v1/analytics`.
- Varje rad: `period`, `pp_goals`, `pp_opportunities`, `pp_pct`,
  `pk_goals_against`, `pk_times`, `pk_pct`.

Acceptanskriterier:
- PP/PK visas per period.
- Data kan filtreras per sasong.
- Databegransningar dokumenteras i `meta`.

### 9. Matchens momentum-kurva

Typ: Feature / Analytics
Prioritet: Hog
Primart repo: backend for berakning, frontend for matchvy
Berorda omraden: game events, matchcenter

Beskrivning:
Skapa en momentumgraf som visar hur matchbilden svanger over tid.

Befintliga byggblock:
- `swehockey_game_events` finns enligt implementationdokumentation.
- Matchcenter i frontend finns men ar mockat.

Saknas:
- FastAPI-endpoint for matchdetaljer.
- Momentumalgoritm och dokumentation.

Foreslaget API-kontrakt:
- `GET /api/v1/matches/{game_id}/momentum`.
- Svar: `game_id`, `periods`, `timeline`, `events`, `method_version`.
- Momentum v1 kan vikta skott, mal, PP, utvisningar och periodtryck.

Acceptanskriterier:
- Momentum visas som tidslinje per match.
- Mal och utvisningar markeras.
- Grafen kan filtreras per period.
- Algoritmen fungerar utan xG-data.

### 10. Matchforklarare

Typ: Feature / Analytics
Prioritet: Hog
Primart repo: backend first
Berorda omraden: matchdata, analytics, matchcenter

Beskrivning:
Returnera 3-5 regelbaserade forklaringar till varfor Bjorkloven vann eller
forlorade en match.

Befintliga byggblock:
- Analytics beraknar redan perioder, special teams och form.
- AI-sammanfattning kan senare ateranvanda samma strukturerade underlag.

Saknas:
- Matchdetail-endpoint med konsekventa nyckeltal.
- Regeluppsattning for matchinsikter.

Foreslaget API-kontrakt:
- `GET /api/v1/matches/{game_id}/explain`.
- Varje insight: `title`, `body`, `impact`, `evidence`, `metric`, `direction`.

Acceptanskriterier:
- Insikterna bygger pa faktiska nyckeltal.
- Regeln ar repeterbar och fungerar utan AI.
- Frontend visar forklaringarna i matchvyn.
- Output har schema-version.

### 11. Head-to-head mot kommande motstandare

Typ: Feature / Match Prep
Prioritet: Medium/Hog
Primart repo: delat
Berorda omraden: schedule, matchcenter, Laget-vy

Beskrivning:
Visa historik och jamforelse infor nasta match.

Saknas:
- Backendkontrakt for nasta match.
- Normaliserad opponent-id over sasonger.

Foreslaget API-kontrakt:
- `GET /api/v1/matches/next`.
- `GET /api/v1/head-to-head?opponent_id=...&season=...`.

Acceptanskriterier:
- Nasta motstandare identifieras fran schema.
- Minst fem senaste inbordes moten visas nar data finns.
- Saknad historik hanteras utan tom UI.

### 12. Spelarroller och spelarprofiler

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: backend for regler, frontend for visning

Beskrivning:
Klassificera spelare i forenklade roller som malskytt, playmaker,
tvavägsspelare, PP-specialist eller defensiv back.

Saknas:
- Rollregler och minsta datakrav.
- Profilvy som konsumerar rolltaggar.

Foreslaget kontrakt:
- `player_roles` i spelarstatistik och spelarprofil.
- Varje roll ska ha `label`, `confidence`, `evidence`.

Acceptanskriterier:
- Varje spelare kan fa 1-3 rolltaggar.
- Reglerna dokumenteras.
- Frontend kan filtrera pa roll.

### 13. Formvarningar och trendbrott

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: backend

Beskrivning:
Identifiera nar laget eller en spelare har tydligt trendbrott.

Befintliga byggblock:
- Rolling form finns delvis i analytics.
- Laget-vyn har plats for signaler.

Foreslaget kontrakt:
- `modules.trend_alerts` i `GET /api/v1/analytics`.
- Aven relevant sammanfattning i current-state/Lovenlaget.

Acceptanskriterier:
- Trender kan beraknas for lag och spelare.
- Det finns minsta matchkrav.
- Trendkort visas bara nar signalen ar stark nog.

### 14. AI-sammanfattning per match

Typ: Feature / AI
Prioritet: Lag/Medium
Primart repo: backend

Beskrivning:
Generera en AI-baserad matchsammanfattning fran game events och matchstatistik.

Befintliga byggblock:
- X-feed har redan kostnadskontrollerad AI/caching-logik.
- Silly-scraper har AI-cache.

Saknas:
- Matchspecifik prompt och cache.
- Regelbaserad matchforklaring som tryggt underlag.

Foreslaget kontrakt:
- `GET /api/v1/matches/{game_id}/summary`.
- AI ska bara genereras efter match eller vid explicit refresh.
- Cache per `game_id` och `source_hash`.

Acceptanskriterier:
- AI-anrop sker inte vid varje sidvisning.
- Sammanfattning baseras pa faktisk matchdata.
- Cachead version ateranvands.
- Fel i AI-lagret stoppar inte matchvyn.

### 15. xG-light baserat pa skottposition

Typ: Feature / Analytics
Prioritet: Medium, men beroende av data
Primart repo: backend/dbt

Beskrivning:
Berakna forenklat expected goals baserat pa skottavstand och skottvinkel.

Befintliga byggblock:
- Warehouse-designen har `shot_distance_m`, `shot_angle`, `xg`.
- dbt har `stg_shot_features` och `fact_shot_features`.

Saknas:
- Faktisk koordinatdata i operativ kalla.
- Modellversion och dokumenterad heuristik.

Foreslaget kontrakt:
- xG-light ska vara dold eller `data_quality=missing_shot_coordinates` nar
  koordinater saknas.
- `xg_model_version = heuristic_v1`.

Acceptanskriterier:
- Datamodellen stodjer skottavstand och vinkel.
- Backend returnerar xG nar skottkoordinater finns.
- Funktionen exponeras inte som exakt modell.

### 16. Interaktiv rinkvisualisering / shot map

Typ: Feature / Visualisering
Prioritet: Lag
Primart repo: frontend, beroende av backenddata

Beskrivning:
Visa skottkartor per match, spelare och lag pa en rink.

Beroende:
- Koordinatdata fran Sportradar eller annan kalla.
- xG-light eller `fact_shot_features` bor finnas for bra nytta.

Acceptanskriterier:
- Frontendkomponenten hanterar tom data.
- Backend returnerar `x`, `y`, `shot_type`, `is_goal`, `player`, `period`.
- Visualiseringen fungerar responsivt.

### 17. Adminvy for scraperstatus och dataladdningar

Typ: Feature / Operations
Prioritet: Medium
Primart repo: delat, men backend kontrakt forst

Beskrivning:
Visa intern status for dataladdningar, fel och kvalitet.

Foreslaget API-kontrakt:
- `GET /api/v1/ops/ingestion-status`.
- `GET /api/v1/ops/data-quality`.
- Skydda vy/endpoint om informationen ar intern.

Acceptanskriterier:
- Visar senaste scraperkorning, laddade sasonger, fel och varningar.
- Visar vilka dataset/tabeller som ar uppdaterade.
- Kan anvandas vid incident eller backfill.

### 18. Spelarjamforelse

Typ: Feature / Analytics
Prioritet: Medium
Primart repo: delat

Beskrivning:
Lat anvandaren jamfora tva eller flera spelare side-by-side.

Beroende:
- Stabil player-id.
- Spelarhistorik och percentiler ger betydligt battre nytta.

Foreslaget API-kontrakt:
- `GET /api/v1/player-compare?player_ids=...&season=...`.

Acceptanskriterier:
- Minst tva spelare kan jamforas.
- Backend returnerar samma nyckeltal per spelare.
- Frontend visar tabell och radar/spider chart.

### 19. Export av statistik till CSV

Typ: Feature
Prioritet: Lag/Medium
Primart repo: frontend first

Beskrivning:
Exportera aktuell tabellvy till CSV.

Rekommendation:
Borja i frontend for befintliga tabeller. Backendexport behovs for stora eller
serverfiltrerade dataset.

Acceptanskriterier:
- Export respekterar aktivt filter och sasong.
- Filnamn innehaller vy, sasong och datum.
- Export fungerar for spelarstatistik, lagstatistik och matchlista.

### 20. Push-notiser vid milstolpar

Typ: Feature / Notifications
Prioritet: Lag
Primart repo: backend + Firebase

Beskrivning:
Skicka push-notiser nar Bjorkloven eller spelare nar viktiga milstolpar.

Beroende:
- Stabil ingestion.
- Trend/milstolpsmotor.
- Anvandarpreferenser och FCM.

Acceptanskriterier:
- Minst tre milstolpar kan trigga notiser.
- Samma notis skickas inte flera ganger.
- Anvandaren kan sla av och pa notiser.
- Backend loggar skickade notiser.

## Forsta tickets att skapa

1. `DATA-001` Historisk sasongsbackfill for HA 2022/23-2024/25.
2. `DATA-002` Data quality run-logg och kontroller efter scraper/backfill.
3. `API-001` FastAPI-kontrakt for current-state eller konsolidering in i
   `GET /api/v1/lovenlaget`.
4. `API-002` `GET /api/v1/season-compare`.
5. `API-003` `GET /api/v1/analytics` med `window`.
6. `WEB-001` Statistikflik "Jamfor" i `frontend_v2`.
7. `WEB-002` Laget-vy kopplad till ett enda current-state-kontrakt.
8. `ML-001` Modellregister och metadata-schema för ML/simuleringar.
9. `SIM-001` Team strength rating v1 och SHL Monte Carlo simulator v1.

## Beslutsregler

- Backendkontrakt vinner over PoC-kontrakt om de skiljer sig.
- En feature far inte visas som skarp om data saknas eller ar stale.
- AI ska bygga pa strukturerad regelanalys, inte ersatta datakvalitet.
- Nya endpoints ska returnera `meta.schema_version`, `meta.generated_at`,
  `meta.freshness_status` och `meta.data_quality` dar det ar relevant.
