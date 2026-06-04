# Roadmap 2026 — Backend Leveransplan (Synkad)

Senast uppdaterad: 2026-05-11  
Källa: synkad med `slutspel/docs/ARCHITECTURE_REVIEW_2026.md`

## Syfte

Detta dokument beskriver hur `loven-stats-backend` levererar målarkitekturen:
ingestion, datakvalitet, lagring, API och drift.

Feature-backloggen som konkretiserar leveranser, API-kontrakt, beroenden och
acceptanskriterier finns i `docs/FEATURE_BACKLOG_2026.md`.

Utredningen för avancerad hockeyanalys, machine learning och simuleringar finns
i `docs/ADVANCED_HOCKEY_ANALYTICS_STACK_2026.md`.

## Status just nu

Vi har gått från plan till första implementation:

- dbt-lager för `staging`, `marts/core` och `serving` är etablerat
- prioriterade fact-tabeller är definierade som dbt-modeller
- serving-vyer för API är etablerade

Not: “klar” i roadmap betyder modellnivå klar i dbt. Full produktionsnytta kräver att råtabeller fylls kontinuerligt och att API läser från `serving_*`.

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
8. Cache/Firestore — ej implementerat

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

Leverabler:
- verifierad körkedja med återstartbarhet
- tydlig freshness per källa och endpoint

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

## Styrmätetal

- Freshness SLA per endpoint
- Ingest success rate per källa
- Andel API-svar med komplett `meta`
- 5xx-rate per endpoint
- Tid till upptäckt av stale data

## Nästa konkreta backlog

1. Genomför historisk säsongsbackfill för HA 2022/23, 2023/24 och 2024/25.
2. Inför automatisk datakvalitetskontroll efter scraper/backfill.
3. Konsolidera `GET /api/v1/current-state` och `GET /api/v1/lovenlaget` så frontend v2 har ett skarpt current-state-kontrakt.
4. Lägg till `GET /api/v1/season-compare` för säsongsjämförelse.
5. Parametrisera `GET /api/v1/analytics` med rolling window (`5`, `10`, `20`).
6. Skapa modellregister för ML/simuleringar (`model_name`, `model_version`, backtest, data quality).
7. Bygg team strength rating v1 och SHL Monte Carlo simulator v1.
8. Migrera prioriterade API-endpoints till `serving_*`.
9. Lägg till relationships- och freshness-tester i dbt.
10. Inför observability- och cachelager.
