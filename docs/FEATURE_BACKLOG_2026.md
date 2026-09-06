# Feature Backlog 2026

Senast uppdaterad: 2026-09-06
Galler for: `loven-stats-backend` som produktionskalla och `slutspel/frontend_v2` som konsument.

## Syfte

Detta dokument kompletterar `docs/ROADMAP.md` med en mer konkret feature-backlog.
Roadmapen beskriver leveransfaserna; detta dokument beskriver vad varje feature
behover i data, API, frontend och verifiering.

För avancerad hockeyanalys, machine learning, simuleringar, scoutinglager och
modellkrav, se även `docs/ADVANCED_HOCKEY_ANALYTICS_STACK_2026.md`.

Verifierad implementationsstatus och arkitekturgap finns i
`docs/ARCHITECTURE_INTEGRATION_2026_06.md`.

## Nulage att utga fran

- Backend har redan `GET /api/v1/seasons`, `GET /api/v1/statistics`,
  `GET /api/v1/analytics`, `GET /api/silly-season`, `GET /api/v1/lovenlaget`,
  `GET /api/v1/x-feed` och `GET /api/v1/financials`.
- `GET /api/v1/statistics` och `GET /api/v1/analytics` laser fortfarande mest
  direkt fran `raw_sports.*` och bygger svar i Python.
- Båda endpointsen väljer senaste `scraped_at`-snapshot och använder en
  processlokal TTL-cache.
- Swehockey-scrapern kan iterera över flera aktiva regular season/playoff-id:n.
- Sju säsongsrader är definierade från HA 2023/24 till 2026/27. Både SHL och
  HA 2026/27 är aktiva för ingestion; API-defaulten väljer SHL deterministiskt.
- **dbt har aldrig körts.** `dbt/` innehåller modeller för `staging`,
  `marts/core` och `serving`, men det finns ingen `target/`, ingen
  `profiles.yml`, och `deploy.sh` anropar den inte. Allt som körs i produktion
  är vanlig SQL i `sql/core_views.sql` och `sql/marts.sql`, deployad med
  `deploy.sh views`. Planera aldrig en feature som *förutsätter* dbt utan att
  först ta migrationen som eget arbete — se feature 25.
- **Nyheterna ligger inte i BigQuery.** `functions/silly_scraper.py` skriver
  JSON-snapshots till GCS (`raw/silly_season/scraped_*.json`), och
  `GET /api/silly-season` läser nyaste bloben vid anrop och slår ihop den med
  en hårdkodad baseline i `silly_season_data.py`. Klassificeringen sker med
  Gemini 2.5 Flash via Vertex, med tak på 15 anrop per körning och
  artikelcache i GCS. Frontend läser API:t live, inte en exporterad fil.
- `slutspel/frontend_v2` anropar aven `/api/v1/current-state` och
  `/api/v1/sportradar/results`, som finns i gamla Node-servern men inte i
  FastAPI-backenden.
- Roster och matchcenter ar delvis mockade i frontend v2.
- Preseason SHL är frontendens standardvy och använder analytics v0.
- SHL projected table är en heuristisk v0, inte en Monte Carlo-simulering.
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

### Fas 4: Flodet som datalager

1. Genererade notiser ur marten (feature 21)
2. Ett flodeskontrakt: `FeedItem` (feature 23)
3. Entitetslankning nyhet -> spelare och match (feature 22)
4. Matchrapporten vidare (feature 24)

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
- Metadata finns för HA 2023/24, SHL 2024/25, HA 2024/25, SHL 2025/26 och
  HA 2025/26; faktisk tabelltäckning måste verifieras separat.
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

Skarpning 2026-09 (från Fables förslag, utvärderat och accepterat):
- Indata ska vara ett **strukturerat objekt** — mål, målvakter, +/-, PP/BP,
  momentumsiffror — inte råtext. `/api/v1/match/{game_id}` bär numera `teams`
  och `goalies` och räcker som källa.
- Prompten ska bära regeln *"inga siffror som inte finns i indata"*. Det är den
  enda spärren mot att referatet hittar på ett skottantal.
- Genereras **en gång per `game_id`** och aldrig om. Cirka 50 anrop per säsong,
  vilket ligger långt under det tak `silly_scraper` redan lever med.

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

### 21. Genererade notiser ur marten

Typ: Feature / Data Engineering
Prioritet: Hog — bygg denna forst i fas 4
Primart repo: `loven-stats-backend`
Berorda omraden: `sql/marts.sql`, `deploy.sh views`, ny endpoint, Nyheter-sidan

Beskrivning:
Mycket av det som borde sta i flodet ar deterministiskt och kraver ingen LLM:
milstolpar (spelare passerar 10/25/50 poang, laget passerar en poang- eller
vinsttroskel), sviter (fem raka, sasongens langsta), och truppforandringar
(debut, forsta malet, tredje raka matchen utanfor truppen). Texten kommer ur
mallar, inte ur en modell. Ingen kostnad, ingen hallucination.

Befintliga byggblock:
- `marts.fact_player_game` har en rad per spelare och match for **alla**
  matcher, inte bara de med poang. Milstolpar och sviter gar att rakna direkt.
- `marts.fact_team_game` bar lagets utfall per match.
- `marts.fact_standings_snapshot` bar tabellen som den sag ut varje dag den
  andrades.
- `marts.fact_lineup_slot` bar klubbens egen uppstallning per match, sa
  "utanfor truppen" gar att lasa utan att gissa.

Saknas:
- En vy `marts.generated_events` som per korning raknar fram raderna.
- `event_key` — en hash av typ, entitet och varde — sa att en omkorning ger
  exakt samma rad. Vyn ar en vy, inte en tabell, sa idempotensen kommer gratis
  sa lange nyckeln ar deterministisk. Ska notiserna kunna kvitteras eller
  skickas som push behovs en materialiserad tabell och da ar `event_key`
  primarnyckeln.
- Malltexter. Halls i SQL sa lange de ar en rad var; flyttas till Python forst
  om de behover boja sig efter genus eller numerus.

Foreslaget API/DB-kontrakt:
- Ny vy: `marts.generated_events` med `event_key`, `event_type`, `ts`,
  `player_key`, `game_id`, `value`, `title`, `body`.
- Ny endpoint: `GET /api/v1/generated-events?season=...&since=...`.

Beroenden och fallgropar:
- **Elo persisteras inte.** Fables forslag namnde "nytt sasongshogsta i Elo",
  men Elo raknas fram inne i `get_projection()` vid anrop och sparas aldrig.
  Den notistypen kraver att serien skrivs ner forst, och ar darfor **inte** med
  i forsta omgangen.
- Milstolpar far bara raknas over matcher vi faktiskt har. Samma fel som
  skjutprocenten hade — sasongens alla mal delat med skotten fran halva
  sasongen gav 91 procent.

Acceptanskriterier:
- Vyn kan koras om utan att ge nya `event_key` for samma handelse.
- Minst tre notistyper: poangmilstolpe, vinstsvit, debut.
- Ingen notis for en match som saknas i `fact_player_game`.
- Notiserna gar att lasa i flodet utan att ett LLM-anrop har skett.

### 22. Entitetslankning nyhet -> spelare och match

Typ: Feature / Data Engineering
Prioritet: Medium
Primart repo: `loven-stats-backend`
Berorda omraden: `functions/silly_scraper.py`, BigQuery, `GET /api/silly-season`

Beskrivning:
En nyhet som namner en spelare ska barra spelarens nyckel, sa att artikeln kan
visas pa spelarsidan och sa att flodet kan grupperas. Detsamma for matcher: en
nyhet daterad plus/minus en dag fran en match som namner motstandaren hor till
den matchen.

Befintliga byggblock:
- Namnformen ar redan lost tre ganger: `clean_person()` i `api/main.py`,
  `_name()` och `_roster_name()` i `functions/game_report_parser.py`. Media
  skriver "Liam Dower Nilsson", vi skriver "Dower Nilsson, Liam".
- `core.player_bio` bar fodelsedatum och position ur trupprapporten.
- `core.schedule` ger datum och motstandare per match.

Saknas:
- Steget maste ligga **efter** att nyheterna landat i BigQuery — idag finns de
  bara som JSON i GCS. Se feature 23; den ar en forutsattning.
- En datumbegransad trupp. Det ar den svara delen, inte namnmatchningen: en
  medieomnamning ar inte en trupphandelse, och en spelare kan skrivas om efter
  att ha lamnat. Utan datumfonster kopplas en avskedsartikel till en spelare
  som inte langre finns i truppen.

Foreslaget API/DB-kontrakt:
- LLM-anropet som redan sker utokas till strukturerad JSON:
  `{event_type, player_names[], status: rykte|uppgifter|officiellt,
  source_role: avslojar|bekraftar|refererar}`. Det ar samma anrop, alltsa
  ingen ny kostnad.
- `links[]` pa varje flodesrad bar `{kind: player|game, key}`.

Acceptanskriterier:
- Namn matchas normaliserat, utan diakritika, pa efternamn plus forsta initial.
- En trav ger `player_id`; en miss loggas och matchas inte manuellt.
- Ingen nyhet kopplas till en spelare som inte var i truppen vid publiceringen.

### 23. Ett flodeskontrakt: `FeedItem`

Typ: Refactor / Arkitektur
Prioritet: Hog
Primart repo: bada
Berorda omraden: `GET /api/silly-season`, `GET /api/v1/x-feed`, `Nyheter.tsx`

Beskrivning:
Frontend ska lasa **en** lista av `FeedItem { id, type, ts, title, body?, tag,
links[], sources[] }` dar `type` ar `story | generated | press | x`. Renderingen
valjs pa `type`; allt arbete sker i pipelinen, inte i React.

Motivet ar konkret: `GET /api/silly-season` laser nyaste GCS-bloben vid anrop
och slar ihop den med en hardkodad Python-baseline i `silly_season_data.py`.
Den sammanslagningen ar dar komplexiteten sitter, och den ar osynlig for
frontend som anda maste kanna bada formerna.

Saknas:
- Att nyheterna faktiskt skrivs till BigQuery i stallet for att bara ligga som
  JSON-snapshots i GCS. Utan det finns ingen tabell for feature 21 och 22 att
  lasa eller skriva till.
- En enda endpoint som serverar alla fyra typerna.

Avgransning — vad som **inte** ska goras:
- Fables forslag att exportera `feed.json` till GCS och trigga en Netlify build
  hook ar utvarderat och **avvisat**. Vi serverar redan fran API:t med
  TTL-cache; en andra serveringsvag lagger till ett inaktualitetsfonster utan
  att losa nagot. Cloud Run-kostnaden ar inget problem idag. Tas upp igen forst
  om den blir det.

Acceptanskriterier:
- `Nyheter.tsx` laser en lista och valjer rendering pa `type`.
- Baselinen i `silly_season_data.py` ar antingen borta eller en rad i samma
  tabell som allt annat.
- Gamla svarsformen kan tas bort utan att sidan slutar fungera.

### 24. Matchrapporten vidare

Typ: Feature / Frontend
Prioritet: Medium
Primart repo: `slutspel/frontend_v2`, delvis backend
Berorda omraden: `Matchrapport.tsx`, `GET /api/v1/match/{game_id}`

Klart 2026-09: lagmarke per rad, malvakter for bada lagen, skott per period,
delbart PNG-kort. Kvar, i ordning:

1. **Boxscore per spelare.** Mal, assist, +/-, utvisningsminuter, skott.
   `+/-` gar att rakna ur `on_ice_for` / `on_ice_against` som redan finns i
   svaret, med PP-mal exkluderade enligt regeln. Skott finns i
   `core.game_boxscore` for matcher med rapport. Diverging bar sorterad fran
   bast till samst.
2. **Handelser i momentumkurvan.** Namn pa malpunkterna vid tryck, utvisningar
   som korta streck under linjen, PP- och BP-mal markta.
3. **Kollapsa "pa isen".** Den tar en stor del av scrollen och lases sallan.
   Visa per mal vid tryck; flytta insikten till boxscoren, dar "vem var pa
   isen for flest mal emot" faktiskt betyder nagot.
4. **Spelform per mal** — 5v5, PP, BP, tom bur — som liten tagg vid stallningen.
5. **Utvisning -> utfall.** "PP utan mal" eller "resulterade i 2-0 (namn, tid)".
   Kopplar ihop tva kort som nu lever isar.
6. **Kedjorna ur uppstallningen** — vilka fem som startade. `fact_lineup_slot`
   bar redan datat; kom ihag att Swehockeys "1st Line" ar hela femman, tre
   forwards **och** backparet.
7. **Kontextkort.** Tabellplacering fore och efter, form in i matchen, inbordes
   moten under sasongen. Elo-forandring kraver att Elo persisteras — se
   feature 21.
8. **Publik mot arenans snitt** i huvudet: "5 799 · +22 % mot AIK:s snitt".

Ej byggbart, dokumenterat:
- **TOI, hits och blocks for utespelare finns inte.** Kolumnerna star i
  Swehockeys mall men ar tomma genom hela serien, i bade SHL och
  HockeyAllsvenskan. Se docstringen i `functions/game_report_parser.py`.
  Malvakternas speltid finns daremot, och anvands redan for exakt GAA.

### 25. Utvarderat och nedprioriterat

For sparbarhetens skull: forslag som provats mot koden och lagts at sidan,
med skalet utskrivet.
Tas upp igen nar forutsattningen andras.

**Klustring av medianyheter till stories.** Foreslagen nyckel var
`event_type + normaliserat spelarnamn + 14-dagarsfonster`. Mekaniken ar rimlig,
men nyhetsvolymen kring Bjorkloven ar en handfull poster i veckan — de flesta
kluster skulle bli ett. Byggs forst nar flodet faktiskt ar brusigt, och da
ovanpa feature 22 som anda ger entiteterna.

**Export av `feed.json` till GCS plus Netlify build hook.** Se avgransningen i
feature 23.

**Migration av produktionsflodet till dbt.** Detta ar ett eget projekt:
profiler, CI, och tretton core-vyer plus tio marts att flytta over. Att gora det
*for nyhetsflodets skull* ar fel forsta anledning — feature 21 blir en fil till
i `sql/marts.sql` och samma `deploy.sh views`, med samma idempotens och utan
migrationen. Ratt anledning att ta dbt ar tester och harstamning over hela
lagret, inte en enskild feature.

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
10. `FEED-001` `marts.generated_events` med `event_key` och tre notistyper.
11. `FEED-002` Nyheterna till BigQuery, som forutsattning for `FeedItem`.
12. `FEED-003` `FeedItem`-kontrakt och en endpoint for hela flodet.
13. `WEB-003` Boxscore per spelare pa matchrapporten, med harlett `+/-`.

## Beslutsregler

- Backendkontrakt vinner over PoC-kontrakt om de skiljer sig.
- En feature far inte visas som skarp om data saknas eller ar stale.
- AI ska bygga pa strukturerad regelanalys, inte ersatta datakvalitet.
- Nya endpoints ska returnera `meta.schema_version`, `meta.generated_at`,
  `meta.freshness_status` och `meta.data_quality` dar det ar relevant.
