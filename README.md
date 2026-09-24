# Löven Stats Hub — backend

Data och API för [sida377.se](https://sida377.se). Frontend ligger i
`samanakbarian/slutspel`.

- `functions/` — Cloud Functions: Swehockey-skörden och nyhetsinsamlingen.
- `sql/` — vyerna i BigQuery: `core` (senaste versionen av varje rad) och
  `marts` (spelare och lag per match).
- `api/` — FastAPI på Cloud Run.
- `jobs/` — renderad skörd av klubbens nyhetssida.
- `scripts/` — backtest och kalibrering av matchmodellen, säsongssynk.
- `tests/` — röktest, sparade svar och datakontroller.
- `backfill_season.py` — hämtar en äldre säsong i efterhand.
- `deploy.sh` — driftsättning från Cloud Shell: `./deploy.sh api | views | all`.

## Dokumentation

- [docs/NASTA_STEG.md](docs/NASTA_STEG.md) — läget, och vad som är på gång. Börja här.
- [docs/DATAPLATTFORM.md](docs/DATAPLATTFORM.md) — lagren, ETL-flödet och datamodellen.
- [docs/SWEHOCKEY_STATS_SCRAPER.md](docs/SWEHOCKEY_STATS_SCRAPER.md) — skörden och källans egenheter.
- [docs/DEPLOY.md](docs/DEPLOY.md) — driftsättning.
- [docs/API_ARKITEKTUR.md](docs/API_ARKITEKTUR.md) — API:ts målbild.
- [docs/FEATURE_BACKLOG_2026.md](docs/FEATURE_BACKLOG_2026.md) — backloggen.

## Lokalt

```
cd api && pip install -r requirements.txt && uvicorn main:app --reload
```
