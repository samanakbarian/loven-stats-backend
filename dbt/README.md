# dbt foundation för Löven Stats Backend

Detta är en körbar dbt-grund för:

- staging (`staging/content`, `staging/core`)
- core facts (`marts/core`)
- produktmart (`marts/product`)
- serving-vyer (`serving`)

## Snabbstart

1. Installera dbt BigQuery:
   - `pip install dbt-bigquery`
2. Lägg en lokal `profiles.yml` med profilnamn `loven_stats`.
3. Kör:
   - `dbt debug`
   - `dbt run --select stg_silly_articles`
   - `dbt run --select fact_content_items serving_silly_season_feed`
   - `dbt run --select marts.core`
   - `dbt run --select marts.product`
   - `dbt test --select marts.core serving marts.product`

För att läsa riktig silly-källa via staging:
- `dbt run --select stg_silly_articles mart_lovenlaget_snapshot --vars '{use_real_silly_source: true}'`

## Notering

Produktmarten kan fortfarande köras med bootstrap/fallback, men målbilden är att API läser från `serving_*`-lagret med datan från `raw_sports`, `raw_roster`, `raw_financials` och `raw_content`.
