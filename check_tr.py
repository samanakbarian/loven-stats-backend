import requests
import json
API = "https://loven-stats-api-324947473206.europe-west1.run.app"
data = requests.get(f"{API}/api/v1/analytics?season=ha_2526").json()
tr = data["modules"]["shl_transition"]
print(list(tr.keys()))
for p in tr.get("skaters", []) + tr.get("goalies", []):
    name = p.get("name", "").lower()
    if "lehtinen" in name or "alba" in name:
        print(f"FOUND: {p['name']}")
