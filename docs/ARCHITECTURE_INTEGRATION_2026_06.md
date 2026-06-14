# Arkitekturintegration juni 2026

Senast verifierad: 2026-06-14

Detta dokument kopplar den faktiska implementationen i `loven-stats-backend`
och `slutspel/frontend_v2` till målarkitekturen i
`docs/ADVANCED_HOCKEY_ANALYTICS_STACK_2026.md`.

## Verifierat nuläge

Verifieringen omfattar:

- backend-commits efter `38f6557`
- aktuella FastAPI-routes och analytics-moduler
- Swehockey-scrapern
- produktions-API:t i Cloud Run
- BigQuery-tabellen `raw_sports.swehockey_seasons`
- frontend v2 och en genomförd produktionsbuild

Backendens Python-filer kompilerar och frontend v2 bygger. Vite-builden varnar
för en stor huvudbundle, men slutförs utan TypeScript- eller byggfel.

## Ny implementation sedan 2026-06-04

### Multi-season ingestion

Swehockey-scrapern har byggts om för att:

- läsa aktiva regular season- och playoff-id:n från
  `raw_sports.swehockey_seasons`
- köra spelare, målvakter, tabell och schema per aktivt season group-id
- hantera Swehockeys nya HTML-tabellstruktur
- deduplicera spelare, målvakter och matcher inom en scrape
- skriva separata GCS-objekt per datatyp och säsong
- ladda append-only snapshots till BigQuery

BigQuery innehåller verifierat följande säsongsmetadata:

| Säsongsnyckel | Liga | Season group-id | Aktiv |
|---|---|---:|---|
| `ha_2324` | HA | 14678 | nej |
| `shl_2425` | SHL | 15977 | nej |
| `ha_2425` | HA | 15986 | nej |
| `shl_2526` | SHL | 18263 | nej |
| `ha_2526` | HA | 18266 | nej |
| `shl_2627` | SHL | 20961 | ja |
| `ha_2627` | HA | 20962 | ja |

Det finns även ett manuellt historikscript för att ladda spelar- och
målvaktsdata för SHL/HA 2024/25.

### Snapshot-aware API

`GET /api/v1/statistics` och `GET /api/v1/analytics` hämtar nu senaste
`scraped_at` per säsong i stället för att läsa alla appendade snapshots.

Detta är ett viktigt stabiliseringssteg, men logiken ligger fortfarande i
FastAPI och bör flyttas till staging-/martmodeller.

### Analytics v0

`GET /api/v1/analytics` levererar nu:

- timeline och kumulativ poängkurva
- hemma/borta-splits
- periodanalys
- head-to-head
- rolling form
- streaks
- player impact relativt ligasnitt
- goalie radar och percentiler
- special teams
- attendance
- penalty breakdown
- Elo-historik
- nästa match-prognos
- projected standings
- scoring timeline
- chemistry
- first-goal impact
- Pythagorean expectation
- game-state records
- SHL transition/readiness
- age curve
- SHL projected table
- AI Coach-text via Gemini

Produktions-API:t returnerar verifierat `status=ok`, 14 lag i
`shl_projected_table` och två målvakter i `shl_transition`.

### SHL projection v0

Den nuvarande SHL-projektionen kombinerar:

- historisk SHL-tabell som styrkebas
- Björklövens roster och silly season-baseline
- spelar- och målvaktsprojektioner
- special teams
- värvningar, tapp och utgående kontrakt

Den returnerar P10/P50/P90, top 6-chans och playout-risk.

Arkitekturklassificering:

- detta är en användbar heuristisk `projection_v0`
- det är ännu inte en Monte Carlo-simulator
- P10/P90 skapas med deterministiska volatilitetsspann
- sannolikheterna är ännu inte kalibrerade eller backtestade
- modellmetadata lagras inte i ett modellregister

### Roster och SHL-benchmark

Silly season-baseline har uppdaterats med nya värvningar, förlängningar,
rink-positioner och manuella SHL-projektioner.

Analytics kan nu:

- använda SHL-statistik för spelare som saknar relevant HA-rad
- använda manuella overrides för nyförvärv
- undvika att applicera HA-till-SHL-regression på redan befintlig SHL-data
- hitta närmast relevanta SHL-säsong som faktiskt har målvaktsdata

Detta löser produktbehov på kort sikt, men blandar källdata, manuella antaganden
och modelloutput i samma request-path.

### Cache

FastAPI använder nu processlokal `TTLCache`:

- statistics: 6 timmar
- analytics: 6 timmar
- silly season: 30 minuter
- X-feed: 30 minuter

Frontendens analytics-komponent använder dessutom sessionStorage-cache i tio
minuter.

Detta är cache v0. Den är inte delad mellan Cloud Run-instanser och har ingen
central invalidering eller observerbar cache-status.

### Frontend v2

Frontendens aktiva startsida är nu `/preseason-shl`; `/` redirectar dit.

Preseason-vyn använder:

- `modules.shl_transition`
- `modules.age_curve`
- `modules.shl_projected_table`
- AI Coach-fältet `shl_sportchef`

Statistikvyn stödjer säsongsval och använder både statistics- och
analytics-endpoints. Frontend har fallback till `ha_2526` om vald säsong saknar
lagdata.

Följande är fortfarande inte produktionsintegrerat:

- roster store använder mockdata
- matchcenter store använder mockdata
- FastAPI saknar `/api/v1/current-state`
- FastAPI saknar `/api/v1/sportradar/results`
- FastAPI saknar skarpa matchcenter-endpoints
- Lövenläget finns kvar i koden men är dolt från navigationen

## Nuvarande produktionsflöde

```text
Swehockey HTML
    |
    v
Cloud Function scraper
    |
    +--> GCS raw snapshots
    |
    +--> BigQuery raw_sports append snapshots
              |
              v
FastAPI latest-snapshot SQL + Python analytics/heuristics/Gemini
              |
              v
Processlokal TTL-cache
              |
              v
frontend_v2 Preseason/Statistik
```

Detta flöde fungerar, men det hoppar fortfarande över den tänkta semantiska
kedjan `staging -> marts/analytics -> serving`.

## Integration med målarkitekturen

### Data foundation

Status: delvis implementerad.

Levererat:

- sju verifierade säsongsmetadata-rader
- multi-season scraper
- historisk 2024/25-load för spelare/målvakter
- latest-snapshot-läsning

Kvar:

- HA 2022/23
- full coverage per säsong för schema, events och playoff
- unik aktiv produktsäsong per liga/use case
- run-id, source lineage och kvalitetsstatus per scrape
- idempotent load eller deduplicerad staging

### Semantic layer

Status: modeller finns i repo, men används inte som huvudväg av API:t.

Kvar:

- materialisera och verifiera dbt i produktionsmiljön
- modellera latest snapshot i staging
- skapa analytics marts
- låta API läsa stabila serving-kontrakt

### Player evaluation

Status: analytics v0 implementerad.

Levererat:

- player impact
- goalie radar
- liga-percentiler
- age curve
- manuella roster-overrides

Kvar:

- stabil player identity
- position-normaliserade benchmarks
- modellversionering
- multi-season player history
- scouting candidate board

### Team strength och simulering

Status: heuristic projection v0 implementerad.

Levererat:

- Elo-baserade delar
- Pythagorean expectation
- SHL projected table
- P10/P50/P90-liknande intervall
- roster- och special teams-justeringar

Kvar:

- separat team-strength-modell
- kalibrerad matchmodell
- faktisk Monte Carlo över schema
- backtesting
- scenario engine
- modellregister

### AI

Status: runtime AI Coach implementerad i analytics-requesten.

Kvar:

- flytta AI-generering till batch/snapshot
- lagra promptversion, input hash och modellversion
- skilja regelbaserade insights från genererad text
- central cache och kostnadslogg

### Produktkontrakt

Status: Preseason/analytics är integrerat; matchcenter/current-state är inte.

Kvar:

- versionerad analytics-response
- separera `silly_season` från analytics-moduler
- avveckla dubbla strukturer för `shl_transition` och
  `silly_season.shl_readiness`
- återinföra Lövenläget först när current-state-kontraktet finns i FastAPI
- ersätta mockad roster och matchcenter

## Kritiska arkitekturrisker

### Två aktiva säsonger

Både `shl_2627` och `ha_2627` är markerade `is_active=true`.
`lookup_season()` väljer efter integrationen SHL deterministiskt när ingen
säsong anges. Preseason-vyn kan fortsatt fråga explicit efter `ha_2526` för
Björklövens senaste HockeyAllsvenska grundserie.

Åtgärd:

- ersätt på sikt global `is_active` med exempelvis `is_current_by_league`
- kräv `league/use_case` där produktkontraktet inte har en naturlig SHL-default
- verifiera säsongskartan automatiskt mot Swehockeys säsongsväljare

### Modellkod i request-path

Analytics, projektion, Gemini, roster-overrides och SQL ligger i samma stora
`api/main.py`.

Åtgärd:

- extrahera `analytics/`, `models/`, `simulations/` och `repositories/`
- förberäkna dyra moduler
- låt API endast orkestrera och serialisera

### Heuristik presenteras som sannolikhet

`top6_chance_pct`, `playout_risk_pct` och P10/P90 är ännu inte resultat från
kalibrerade simuleringar.

Åtgärd:

- märk svaret med `model_type=heuristic`
- lägg till `model_version`, `backtest_status` och `calibration_status`
- kalla inte intervallen Monte Carlo före simulation v1

### Append-only raw utan run governance

Latest-snapshot SQL döljer dubbletter men löser inte lineage, partiella runs
eller felaktiga snapshots.

Åtgärd:

- skapa `raw_ops.ingestion_runs`
- ge varje rad `run_id`
- publicera endast godkända runs till staging

### Repo-hygien

Repo-roten innehåller många committade engångsskript, patch-skript,
diagnosutskrifter och tomma outputfiler. Ett produktionsverifieringsscript
förväntar dessutom ett äldre projectionsschema och faller på `team_name`.

Åtgärd:

- flytta återanvändbara verktyg till `scripts/ops`, `scripts/backfill` och
  `scripts/diagnostics`
- flytta riktiga tester till `tests/`
- ta bort genererade outputfiler ur Git
- ersätt ad hoc-verifiering med kontraktstester

### Encoding och typdisciplin

Flera Python- och TypeScript-filer innehåller mojibake. `App.tsx` använder
`@ts-nocheck`, vilket döljer kontraktsfel.

Åtgärd:

- normalisera källfiler till UTF-8
- generera eller dela API-typer
- ta bort `@ts-nocheck`
- inför kontraktstest mellan analytics JSON och TypeScript-typer

## Reviderad övergångsplan

### Steg 1: Stabilisera nuvarande v0

- fixa aktiv säsong-semantik
- lägg `run_id` och kvalitetsstatus på ingestion
- uppdatera produktionsverifiering till aktuellt schema
- märk SHL-projektionen som heuristisk v0
- städa repo-roten

### Steg 2: Flytta beräkning ur API

- skapa latest-snapshot stagingmodeller
- skapa `mart_team_season_analytics`
- skapa `mart_player_season_analytics`
- skapa `mart_shl_projection_snapshot`
- skapa versionerade serving-vyer

### Steg 3: Produktkontrakt

- dela analytics i mindre versionerade endpoints eller snapshots
- bygg skarpa roster- och matchcenter-endpoints
- konsolidera current-state/Lövenläget
- ta bort frontendmockar

### Steg 4: Modellplattform

- skapa `raw_ops.model_runs`
- extrahera team-strength v1
- bygg match probability v1
- bygg Monte Carlo v1
- backtesta på historiska SHL-säsonger

### Steg 5: Avancerad hockey intelligence

- player identity och multi-season profiler
- xG när koordinatdata finns
- player similarity och scouting
- roster scenario simulator
- opponent prep och matchförklarare
