"""
Fix BigQuery seasons table and populate correct season IDs.
"""
from google.cloud import bigquery

client = bigquery.Client(project="granskaren-d51a1")

# 1. Delete stale records
del_q = """
DELETE FROM granskaren-d51a1.raw_sports.swehockey_seasons
WHERE regular_season_id IN (16147, 20822, 18263, 20961, 20962)
"""
client.query(del_q).result()
print("Deleted old seasons")

# 2. Check what we have
print("Current seasons:")
for r in client.query("SELECT * FROM granskaren-d51a1.raw_sports.swehockey_seasons").result():
    print(" ", dict(r.items()))

# 3. Insert correct seasons
# SHL 24/25 regular_season_id = 18263 (confirmed from scraping stats.swehockey.se)
# HA 24/25  regular_season_id = 18266 (confirmed from scraping stats.swehockey.se)
# SHL 25/26 regular_season_id = 20961 (on front page, hasn't started)
# HA 25/26  regular_season_id = 20962 (on front page, hasn't started)
rows = [
    {"season_key": "shl_2425", "season_name": "SHL 2024/25", "league": "SHL",
     "regular_season_id": 18263, "playoff_id": None,
     "start_date": "2024-09-12", "end_date": "2025-03-31", "is_active": False},
    {"season_key": "ha_2425", "season_name": "HockeyAllsvenskan 2024/25", "league": "HA",
     "regular_season_id": 18266, "playoff_id": None,
     "start_date": "2024-09-12", "end_date": "2025-04-15", "is_active": False},
    {"season_key": "shl_2526", "season_name": "SHL 2025/26", "league": "SHL",
     "regular_season_id": 20961, "playoff_id": None,
     "start_date": "2025-09-11", "end_date": None, "is_active": True},
    {"season_key": "ha_2526", "season_name": "HockeyAllsvenskan 2025/26", "league": "HA",
     "regular_season_id": 20962, "playoff_id": None,
     "start_date": "2025-09-11", "end_date": None, "is_active": True},
]

table_id = "granskaren-d51a1.raw_sports.swehockey_seasons"
errors = client.insert_rows_json(table_id, rows)
if errors:
    print("Insert errors:", errors)
else:
    print("Inserted new seasons successfully")

# Verify
print("\nFinal seasons:")
for r in client.query("SELECT season_key, regular_season_id, is_active FROM granskaren-d51a1.raw_sports.swehockey_seasons ORDER BY start_date").result():
    print(f"  {r.season_key}  id={r.regular_season_id}  active={r.is_active}")
