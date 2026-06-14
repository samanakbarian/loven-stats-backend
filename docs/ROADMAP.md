# Roadmap 2026 — Backend Leveransplan (Synkad)

Senast uppdaterad: 2026-06-14
Källa: verifierad mot backend, `slutspel/frontend_v2` och produktions-API

## Syfte

Detta dokument beskriver hur `loven-stats-backend` levererar målarkitekturen:
ingestion, datakvalitet, lagring, API och drift.

Feature-backloggen som konkretiserar leveranser, API-kontrakt, beroenden och
acceptanskriterier finns i `docs/FEATURE_BACKLOG_2026.md`.

Utredningen för avancerad hockeyanalys, machine learning och simuleringar finns
i `docs/ADVANCED_HOCKEY_ANALYTICS_STACK_2026.md`.

Aktuell integration mellan kodbasen och målarkitekturen finns i
`docs/ARCHITECTURE_INTEGRATION_2026_06.md`.

## Status just nu

Vi har gått från plan till första implementation:

- dbt-lager för `staging`, `marts/core` och `serving` är etablerat
- prioriterade fact-tabeller är definierade som dbt-modeller
- serving-vyer för API är etablerade
- Swehockey-ingestionen stödjer flera säsongs-id:n och senaste snapshot
- analytics v0 och SHL preseason-projektion används av frontend v2
- processlokal TTL-cache finns för analytics, statistics, silly och X-feed

Not: “klar” i roadmap betyder modellnivå klar i dbt. Full produktionsnytta
kräver fortfarande verifierade dbt-körningar, datakvalitetslogg och att API
läser från `serving_*`. Analytics- och simulationsstatus ska inte tolkas som
färdig ML; nuvarande SHL-projektion är heuristisk v0.

## Målbild (oförändrad)

1. Tillförlitliga och källspårbara dataflöden.
2. API-kontrakt med tydlig freshness och schema-version.
3. Kostnadskontrollerad AI/analys.
4. Driftbar plattform med övervakning och incidentrutiner.
5. Machine learning och simuleringar med tydlig modellversion, backtesting och osäkerhetsintervall.

## Implementationsstatus mot prioriteringslistan

1. `fact_event_players` — klar (dbt-modell)
2. `fact_roster_status` — klar (dbt-modell)
3. `fact_match_lineup` — klar (dbt-modell)
4. `fact_content_items` — klar (dbt-modell)
5. `loven_serving` (modellnivå) — klar (`serving_*` modeller)
6. Orchestration-lager — delvis (Cloud Functions/Run Jobs finns; schedulerkedja behöver härdas)
7. Data quality/dbt tests — delvis (grundtester tillagda; relations/freshness kvar)
8. Cache — delvis (processlokal TTL-cache finns; distribuerad cache saknas)
9. Analytics/SHL projection — delvis (produktkopplad heuristik v0; modelljobb,
   register, kalibrering och backtesting saknas)

## Leveransfaser (uppdaterad)

## Fas 1 — Datagrund och serving-bas (genomförd V1)
Levererat:
- `staging/core` + `staging/content`
- `marts/core` facts
- `serving`-vyer
- grundläggande dbt-tester

Kvar i fasen:
- köra `dbt run/test` i CI/dbt Cloud mot fulla råkällor
- verifiera datakvalitet på riktiga volymer

## Fas 2 — Ingestion och orchestration-härdning (pågående)
Mål:
- säkerställa att `raw_sports.*`, `raw_roster.*`, `raw_financials.*`, `raw_content.*` fylls stabilt
- härda schedulerflöden och felhantering

Levererat 2026-06-14:
- append-only `raw_ops.ingestion_runs`
- `raw_ops.data_quality_runs`
- `run_id` och källspårning för Swehockey
- kvalitetsgrind före publicering
- dbt-filter för godkända ingestion-runs

Leverabler:
- verifierad körkedja med återstartbarhet
- tydlig freshness per källa och endpoint
- entydig aktiv säsong per liga
- `run_id`, radantal och data quality-resultat per körning

## Fas 3 — API migration till serving (nästa)
Mål:
- API ska läsa från `serving_*` i stället för ad hoc-joins/logik

Leverabler:
- `/api/silly-season` -> `serving_silly_season_feed`
- `/api/v1/roster` -> `serving_roster_overview`
- `/api/v1/matches` -> `serving_match_summary`
- `/api/v1/financials` -> `serving_team_economy_dashboard`

## Fas 4 — Driftlager (efter migration)
Mål:
- observability och cachelager

Leverabler:
- `etl_run_log`, `api_request_log`, stale-larm
- Firestore-cache för produktvyer

## Fas 5 — Modell- och simulationslager
Mål:
- flytta heuristisk analytics från request path till reproducerbara modelljobb
- införa modellregister, backtesting och kalibrerade osäkerhetsintervall

Leverabler:
- team strength rating v1
- versionerad SHL-simulator med Monte Carlo
- snapshot-tabeller för godkända modellresultat

## Styrmätetal

- Freshness SLA per endpoint
- Ingest success rate per källa
- Andel API-svar med komplett `meta`
- 5xx-rate per endpoint
- Tid till upptäckt av stale data

## Nästa konkreta backlog

1. Driftsätt den nya Swehockey-funktionen och kör ett schemalagt smoketest.
2. Verifiera historisk datatäckning och slutför saknade backfill-körningar.
3. Lägg volymavvikelse, freshness-larm och domänspecifika kvalitetskontroller.
4. Uppdatera kontrakttester till nuvarande analytics-schema och rensa
   diagnostik-/engångsskript från produktionsroten.
5. Kör och verifiera dbt-modeller mot skarpa råkällor.
6. Migrera latest-snapshot och prioriterade endpoints till `serving_*`.
7. Konsolidera `GET /api/v1/current-state` och `GET /api/v1/lovenlaget`.
8. Extrahera SHL projection v0 till ett versionerat modelljobb och modellregister.
9. Bygg team strength rating v1 och kalibrerad SHL Monte Carlo simulator v1.
10. Ersätt processlokal cache med distribuerad cache där flera instanser kräver det.
