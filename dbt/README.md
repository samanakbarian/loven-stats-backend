# dbt bootstrap for Löven Stats Backend

Detta är en första körbar dbt-grund för produktmarten `mart_lovenlaget_snapshot`.

## Snabbstart

1. Installera dbt BigQuery:
   - `pip install dbt-bigquery`
2. Lägg en lokal `profiles.yml` med profilnamn `loven_stats`.
3. Kör:
   - `dbt debug`
   - `dbt run --select mart_lovenlaget_snapshot`
   - `dbt test --select mart_lovenlaget_snapshot`

För att läsa riktig silly-källa via staging (i stället för bootstrap):
- `dbt run --select stg_silly_articles mart_lovenlaget_snapshot --vars '{use_real_silly_source: true}'`

## Notering

V1-marten använder bootstrapvärden så att pipeline kan etableras direkt.
Nästa steg är att ersätta bootstraplogiken med stagingkällor (silly/roster/financials).
