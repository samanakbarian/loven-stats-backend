# Official Rendered Scraper (Björklöven)

Syfte: hämta officiella nyheter från `bjorkloven.com/nyheter` via renderad browser (Playwright), och skriva en normaliserad snapshot till GCS:

- `raw/silly_season/official_rendered_latest.json`

## Deploy (Cloud Run Job)

```bash
gcloud run jobs deploy official-rendered-scraper \
  --region=europe-west1 \
  --source=jobs/official_rendered_scraper \
  --set-env-vars=GCS_BUCKET_NAME=loven-stats-raw-data-prod,OFFICIAL_RENDERED_BLOB_NAME=raw/silly_season/official_rendered_latest.json
```

## Kör manuellt

```bash
gcloud run jobs execute official-rendered-scraper --region=europe-west1 --wait
```

## Schemaläggning (var 30:e minut)

Koppla Cloud Scheduler -> Cloud Run Job.
Rekommenderat cron:

```text
*/30 * * * *
```

## Integration

`functions/silly_scraper.py` läser automatiskt blobben via `OFFICIAL_RENDERED_BLOB_NAME` och inkluderar källan:

- `OfficialRendered (Bjorkloven)`

Detta fungerar parallellt med övriga källor (Expressen, HockeySverige, HockeyNews, EliteProspects, Google News fallback).
