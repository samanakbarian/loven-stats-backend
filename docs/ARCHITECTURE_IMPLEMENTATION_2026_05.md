# Arkitekturimplementation — V1 (Maj 2026)

Senast uppdaterad: 2026-05-17

## Genomfört i denna leverans

Denna leverans etablerar en första körbar implementation av målarkitekturen i dbt:

- `staging/core` för källnära normalisering
- `marts/core` för nya fact-tabeller
- `serving` för API-optimerade vyer
- schema/tests för datakvalitet

## Driftuppdatering 2026-05-17 (API + content-lager)

Foljande har hardats i produktions-API (Cloud Run):

- `GET /api/v1/x-feed` har fallback-query nar primarsokning inte ger faska traffar for dagen.
- X-feed anvander GCS-cache med styrning via `X_CACHE_MINUTES` for kostnadskontroll.
- Roster-sakring i `GET /api/silly-season`: `confirmed_signings` synkas automatiskt in i `roster` om spelare saknas.
- Baseline uppdaterad med Topi Niemela som nyforvarv (RD), sa han alltid syns i truppdata.

Not:
- Dessa andringar ligger i API-lagret och paverkar frontend via befintliga endpoints.

## Nya staging-modeller

- `stg_event_players`
- `stg_roster_status_events`
- `stg_match_lineups`
- `stg_team_financials`
- `stg_team_standings_snapshots`
- `stg_goalie_game_stats`
- `stg_shot_features`
- `stg_attendance`

## Nya fact-modeller (`marts/core`)

- `fact_event_players`
- `fact_roster_status`
- `fact_match_lineup`
- `fact_content_items`
- `fact_goalie_game_stats`
- `fact_team_standings_snapshot`
- `fact_shot_features`
- `fact_team_financials`
- `fact_attendance`

## Nya serving-modeller

- `serving_silly_season_feed`
- `serving_roster_overview`
- `serving_match_summary`
- `serving_team_economy_dashboard`

## Status mot prioriterad handlingsordning

1. `fact_event_players` — klar (V1)
2. `fact_roster_status` — klar (V1)
3. `fact_match_lineup` — klar (V1)
4. `fact_content_items` — klar (V1)
5. `loven_serving` (modellnivå) — klar (V1)
6. Orchestration-lager — delvis (Cloud Functions/Run Jobs finns, scheduler-kedja kvar att härda)
7. Data quality/tests — delvis (grundtester i dbt tillagda)
8. Cache/Firestore — ej implementerat

## Nästa steg (direkt efter denna leverans)

1. Skapa/validera råtabeller i BigQuery:
   - `raw_sports.*`
   - `raw_roster.roster_status_events`
   - `raw_financials.team_financials`
2. Wirea ingestion så att dessa tabeller fylls kontinuerligt.
3. Publicera API-endpoints som läser från `serving_*` i stället för ad hoc-joins.
4. Lägg in freshness-/relationships-tester per råkälla.
