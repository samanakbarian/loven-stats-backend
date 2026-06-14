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

Scrapern kör defensivt per datatyp (try/except per del), så fel i en del blockerar inte övriga.

## GCS raw output

Path:

- `raw/web_scrapers/shl_stats/<YYYYMMDD_HHMMSS>_<typ>.json`

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

- `scraped_at` (TIMESTAMP)
- `source = "swehockey"`

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

- `stg_swehockey_player_stats`
- `stg_swehockey_goalie_stats`
- `stg_swehockey_standings`

Source + freshness/tests:

- `models/staging/core/schema_swehockey.yml`

Freshness:

- `warn_after: 3h`
- `error_after: 12h`

Facts integration:

- `stg_swehockey_player_stats` -> `fact_event_players`
- `stg_swehockey_goalie_stats` -> `fact_goalie_game_stats`
- `stg_swehockey_standings` -> `fact_team_standings_snapshot`

## Driftstatus 2026-05-17

- Deployad i `europe-west1`
- Scheduler-jobb aktivt: `swehockey-stats-scraper-job`
- Manuell verifiering körd:
  - spelare: 75 rader per körning
  - målvakter: 45 rader per körning
  - tabell: 70 rader per körning
  - schema: 59 rader per körning

Not: Swehockey-sidorna är inte stabila för team-specifika URL:er i alla säsongsgrupper. Scrapern använder därför robust fallback mot ligatabeller och filtrerar på team-tokens när möjligt.
