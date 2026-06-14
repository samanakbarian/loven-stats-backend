"""
Manually scrape and load SHL 24/25 (18263) and HA 24/25 (18266) goalie+player data into BigQuery.
These are inactive seasons that need historical data loaded.
"""
import sys
sys.path.append('c:/Users/saman/loven-stats-backend/functions')

from swehockey_stats_scraper import (
    _fetch_goalie_stats, _fetch_player_stats, _now, _append_bq_rows, _upload_raw_json, GCP_PROJECT, BQ_DATASET, SWEHOCKEY_TEAM_ID, SOURCE
)
from google.cloud import bigquery

scraped_at = _now().isoformat()
bq_client = bigquery.Client(project=GCP_PROJECT)

historical_season_ids = ["18263", "18266"]  # SHL 24/25, HA 24/25

for season_id in historical_season_ids:
    print(f"\n=== Season {season_id} ===")

    for data_type, fetcher, table_name in [
        ("player_stats", _fetch_player_stats, "swehockey_player_stats"),
        ("goalie_stats", _fetch_goalie_stats, "swehockey_goalie_stats"),
    ]:
        try:
            rows, url = fetcher(season_id)
            print(f"  {data_type}: {len(rows)} rows from {url}")
            if rows:
                payload = {"meta": {"source": SOURCE, "type": data_type, "season_group_id": int(season_id), "source_url": url, "scraped_at": scraped_at}, "rows": rows}
                gcs_key = f"{data_type}_{season_id}_{scraped_at.replace(':', '').replace('-', '')}"
                _upload_raw_json(payload, gcs_key)
                loaded = _append_bq_rows(bq_client, table_name, rows, scraped_at)
                print(f"  -> Loaded {loaded} rows into {table_name}")
        except Exception as e:
            import traceback
            print(f"  ERROR in {data_type}: {e}")
            traceback.print_exc()

print("\nDone!")
