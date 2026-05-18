# Arkitekturimplementation — V1 (Maj 2026)

Senast uppdaterad: 2026-05-18

## Genomfört i denna leverans

Denna leverans etablerar en första körbar implementation av målarkitekturen i dbt:

- `staging/core` för källnära normalisering
- `marts/core` för nya fact-tabeller
- `serving` för API-optimerade vyer
- schema/tests för datakvalitet

## Driftuppdatering 2026-05-18 (Komplett Swehockey-pipeline)

### BigQuery — Fullständigt datamart

Alla `raw_sports`-tabeller har uppdaterats med komplett schema:

| Tabell | Kolumner | Rader | Nyckeluppdatering |
|--------|----------|-------|-------------------|
| `swehockey_player_stats` | 15 | 50 | +`avg_ppg`, `position`, `jersey_number`; `plus_minus` nu STRING |
| `swehockey_goalie_stats` | 19 | 29 | +`shutouts`, `wins`, `losses`, `win_pct`, `gpi`, `jersey_number` |
| `swehockey_schedule` | 14 | 366 | +`game_id`, `period_results`, `venue`, `spectators`, `match_time` |
| `swehockey_standings` | 12 | 14 | Oförändrad |
| `swehockey_game_events` | 17 | 972 | **NY** — per-match mål + utvisningar |

### Data Integrity Fix

- SHL-kontaminering (season_group_id=18263) rensad
- Standings omberäknade från lokal schedule-data
- Script: `scrapers/swehockey/fix_wrong_league.py`, `fix_standings.py`

### Analytics API

Endpoint `GET /api/v1/analytics` med utökade moduler:

| Modul | Data | Beskrivning |
|-------|------|-------------|
| `timeline` | 52 datapunkter | Kumulativa poäng per match |
| `splits` | 2 objekt | Hemma vs borta: GP, V-F, GF, GA, PTS |
| `periods` | 3 objekt | Mål för/mot per period (P1/P2/P3) |
| `h2h` | 13 motståndare | Head-to-head: V, F, GF, GA, Diff |
| `form` | 52 datapunkter | Rolling 10-matchsfönster |
| `streaks` | Alla sviter | Längsta vinst/förlust + nuvarande |
| `player_impact` | BJK-spelare | G/GP, A/GP, P/GP + vs ligasnitt |
| `goalie_radar` | BJK-målvakter | SV%, GAA, V% percentiler |
| `special_teams` | PP/PK | PP% och PK% beräknade från game events |
| `attendance` | Publikdata | Snitt, max, min, trend |
| `age_curve` | SHL-preseason | Ålderskurva och trajectories för truppen |
| `shl_projected_table` | SHL-preseason | Predikterad tabell med P10/P50/P90 + `data_quality` |

Noteringar:
- Projektion använder senaste SHL-standings som styrkebas.
- Teamset mappas till kommande SHL 2026/27 (Björklöven in, MODO/Leksand ut).
- Om SHL-källdata saknas returneras `data_quality = "missing_shl_source"` och tom tabell.

### Frontend Analytics

Ny "Analys"-tab under `/statistik` med sub-tabbar och preseason-SHL:

- **Säsong:** Poängkurva (AreaChart), formkurva (AreaChart), stat-cards
- **Splits:** Hemma/borta-kort, periodstaplar (BarChart), H2H-tabell
- **Impact:** Scatter-chart (G/GP × A/GP), efficiency-tabell, målvakts-radar (RadarChart)
- **Preseason SHL:** readiness-scorecard, age curve, projections-tabell (visas i HA 25/26 som preseason-vy)

Bibliotek: Recharts (react-native charting, MIT).

## Driftuppdatering 2026-05-17 (API + content-lager)

Följande har härdats i produktions-API (Cloud Run):

- `GET /api/v1/x-feed` har fallback-query när primärsökning inte ger färska träffar för dagen.
- X-feed använder GCS-cache med styrning via `X_CACHE_MINUTES` för kostnadskontroll.
- Roster-sakring i `GET /api/silly-season`: `confirmed_signings` synkas automatiskt in i `roster` om spelare saknas.
- Baseline uppdaterad med Topi Niemelä som nyförvärv (RD), så han alltid syns i truppdata.

## Driftuppdatering 2026-05-17 (Swehockey-modul)

Ny Cloud Function `swehockey-stats-scraper` är implementerad och driftsatt i `europe-west1`.

- Hämtar fyra datatyper: spelare, målvakter, tabell, schema.
- Skriver raw-filer till:
  - `raw/web_scrapers/shl_stats/<YYYYMMDD_HHMMSS>_<typ>.json`
- Laddar till BigQuery dataset `raw_sports`:
  - `swehockey_player_stats`
  - `swehockey_goalie_stats`
  - `swehockey_standings`
  - `swehockey_schedule`
- Radmetadata:
  - `scraped_at`
  - `source = "swehockey"`
- Scheduler aktiv:
  - `swehockey-stats-scraper-job`
  - `0 6 * * 1`
  - `Europe/Stockholm`

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
9. **Multi-season-stöd — planerat (se MULTI_SEASON_PLAN.md)**

## Nästa steg

1. **Implementera multi-season-stöd** (se `slutspel/docs/MULTI_SEASON_PLAN.md`)
2. Aktivera och verifiera råtabeller i BigQuery (`raw_roster`, `raw_financials`)
3. Publicera API-endpoints som läser från `serving_*` i stället för ad hoc raw_sports-queries
4. Lägg in freshness-/relationships-tester per råkälla
5. Inför cache- och observability-lager
