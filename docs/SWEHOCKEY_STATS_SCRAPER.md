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

## On-ice: vad `Pos. Part.` ger, och vad den inte ger

Målcellen på `/Game/Events/{game_id}` bär `Pos. Part.` och `Neg. Part.` — de
spelare som stod på isen för det görande respektive det släppande laget.
Scrapern lägger dem i `on_ice_for` och `on_ice_against` som tröjnummer, och
`/api/v1/onice` räknar mål för och emot per spelare ur dem.

### Talen är inte tabellens plus/minus

Swehockeys spelartabell har egna kolumner `+`, `-` och `+/-`. De stämmer inte
med vad `Pos. Part.` ger. Mätt över hela HA 25/26, Björklövens 27 utespelare:

| | mina summor | Swehockeys | kvot |
|---|---|---|---|
| `+` | 606 | 704 | 1,162 |
| `-` | 318 | 366 | 1,151 |

Avvikelsen är konsekvent åt båda hållen, ungefär 16 procent. Den beror alltså
inte på att fel situationer räknas — plus- och minussidan skulle då dra åt
olika håll. För enskilda spelare stämmer ena sidan ibland exakt: Cronholm har
`-` 19 i tabellen och 19 hos oss, men `+` 50 mot våra 40.

Vad som prövats utan att förklara skillnaden:

- bara mål vid lika styrka
- lika styrka plus mål i underläge (närmast, medelavvikelse 1,71 mot 2,50)
- alla situationer, och varianter där för- och emotsidan räknas olika
- straffslagsmål in- och exkluderade

Slutsatsen är att Swehockey räknar on-ice på ett underlag vi inte ser på
händelsesidan. **Kalibrera inte bort skillnaden med en faktor.** Talen
redovisas i stället bredvid varandra: `gf_on`/`ga_on`/`diff` från händelserna,
och `official_plus_minus` från tabellen.

### Vad våra tal ändå ger som tabellens inte gör

- uppdelning på lika styrka mot alla situationer
- andel av lagets mål spelaren var med på
- vilka par som oftast står på isen tillsammans när laget gör mål
- samma nedbrytning per match

### Slutspelet saknar match-id

Swehockey länkar inte matcherna från slutspelssidan, och varken `Overview`,
`GameCenter` eller `Live` för samma säsongsgrupp har `/Game/Events/`-länkar.
Grundserien har 364 länkar, slutspelet noll. Slutspelsmatcher får därför
varken matchrapport eller händelser.
