import requests
import json
API = "https://loven-stats-api-324947473206.europe-west1.run.app"
data = requests.get(f"{API}/api/v1/analytics?season=ha_2526").json()
pt = data["modules"]["shl_projected_table"]
print(f"Data quality: {pt['data_quality']}")
bjk = pt.get("bjk_summary", {})
print(f"Rank: {bjk.get('projected_rank')}")
print(f"Points: {bjk.get('projected_points')}")
for t in pt["table"]:
    if "is_bjk" in t and t["is_bjk"]:
        print(t)
