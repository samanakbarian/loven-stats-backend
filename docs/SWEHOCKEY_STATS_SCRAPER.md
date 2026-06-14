# Swehockey Stats Scraper

Cloud Function Gen2: `swehockey-stats-scraper`.

## Scope

Scrapar `stats.swehockey.se` med:

- `SWEHOCKEY_TEAM_ID` (default `1139`)
- `SWEHOCKEY_SEASON_GROUP_ID` (fallback `20961`, SHL 2026/27)

Datatyper:

- spelarstatistik
- målvaktsstatistik
- tabellställning
- matchschema

Scrapern hämtar först samtliga datatyper, kör kvalitetskontroller och publicerar
sedan endast om hela den kritiska kvalitetsgrinden passerar.

## GCS raw output

Path:

- `raw/web_scrapers/swehockey/<datum>/<run_id>/<season_group_id>/<typ>.json`

Objekten är immutable per körning och innehåller `run_id`, källa, käll-URL,
säsongsgrupp och `scraped_at`.

## BigQuery raw tables

Dataset: `raw_sports` (skapas automatiskt vid behov)

Tabeller:

- `swehockey_player_stats`
- `swehockey_goalie_stats`
- `swehockey_standings`
- `swehockey_schedule`

Write mode:

- `WRITE_APPEND` (deduplicering sker i dbt)

Metadata per rad:

- `run_id`
- `scraped_at` (TIMESTAMP)
- `source = "swehockey"`
- `source_url`

Kör- och kvalitetsmetadata lagras i:

- `raw_ops.ingestion_runs`
- `raw_ops.data_quality_runs`

## Deploy (Cloud Functions Gen2)

```bash
gcloud functions deploy swehockey-stats-scraper \
  --gen2 \
  --region=europe-west1 \
  --runtime=python311 \
  --source=functions \
  --entry-point=run_swehockey_stats_scraper \
  --trigger-http \
  --allow-unauthenticated \
  --memory=1024Mi \
  --timeout=300s \
  --set-env-vars="GCP_PROJECT=granskaren-d51a1,GCS_BUCKET=loven-stats-raw-data-prod,SWEHOCKEY_TEAM_ID=1139,SWEHOCKEY_SEASON_GROUP_ID=20961"
```

## Scheduler (veckovis, Stockholm)

```bash
gcloud scheduler jobs create http swehockey-stats-scraper-job \
  --location=europe-west1 \
  --schedule="0 6 * * 1" \
  --time-zone="Europe/Stockholm" \
  --uri="https://europe-west1-granskaren-d51a1.cloudfunctions.net/swehockey-stats-scraper" \
  --http-method=GET
```

## dbt

Staging:

- `stg_successful_ingestion_runs`
- `stg_swehockey_player_stats`
- `stg_swehockey_goalie_stats`
- `stg_swehockey_standings`
- `stg_swehockey_schedule`

Source + freshness/tests:

- `models/staging/core/_core_sources.yml`
- `models/staging/core/schema_ops.yml`

Freshness:

- `warn_after: 3h`
- `error_after: 12h`

Facts integration:

- `stg_swehockey_player_stats` -> `fact_event_players`
- `stg_swehockey_goalie_stats` -> `fact_goalie_game_stats`
- `stg_swehockey_standings` -> `fact_team_standings_snapshot`

## Driftstatus 2026-06-14

- Deployad Gen2-revision: `swehockey-stats-scraper-00011-hot`
- Region: `europe-west1`
- Scheduler-jobb aktivt: `swehockey-stats-scraper-job`
- Direkt produktionsanrop: HTTP 200, 756 laddade rader, 0 fel.
- Manuellt scheduler-anrop: HTTP 200, 756 laddade rader, 0 fel.
- Aktiva ingestion-grupper:
  - SHL 2026/27: `20961`
  - HockeyAllsvenskan 2026/27: `20962`
- Spelar- och målvaktsstatistik är ännu tom före säsongsstart och loggas därför
  som `WARNING`, inte som blockerande fel.
- Tabell och schema passerar kritiska kontroller.
