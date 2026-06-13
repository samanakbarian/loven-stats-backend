import os
import importlib

os.environ["GCP_PROJECT"] = "granskaren-d51a1"

import functions.swehockey_stats_scraper as scraper

def run_scraper_for_season(season_id):
    print(f"Running for season {season_id}")
    os.environ["SWEHOCKEY_SEASON_GROUP_ID"] = str(season_id)
    # Reload module to pick up env var
    importlib.reload(scraper)
    res, status, _ = scraper.run_swehockey_stats_scraper(None)
    print(f"Status: {status}")
    print(res)

run_scraper_for_season(18266) # HA
run_scraper_for_season(18263) # SHL
