import os
os.environ["SWEHOCKEY_SEASON_GROUP_ID"] = "18266"
os.environ["SWEHOCKEY_TEAM_ID"] = "1139"

import sys
sys.path.insert(0, "c:/Users/saman/loven-stats-backend/functions")
from swehockey_stats_scraper import _fetch_schedule

rows, url = _fetch_schedule()
print(f"Scraped URL: {url}")
print(f"Total rows: {len(rows)}")
for r in rows[:10]:
    print(r)
