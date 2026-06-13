import requests
import json

API = "https://loven-stats-api-324947473206.europe-west1.run.app"

print("=== /api/v1/analytics?season=ha_2526 ===")
r = requests.get(f"{API}/api/v1/analytics?season=ha_2526", timeout=60)
data = r.json()
text = json.dumps(data)

print(f"Lehtinen in response: {'Lehtinen' in text}")
print(f"Alba in response: {'Alba' in text}")

if "modules" in data and "shl_transition" in data["modules"]:
    tr = data["modules"]["shl_transition"]
    roster = tr.get("projected_roster", [])
    print(f"\nProjected roster size: {len(roster)}")
    for p in roster:
        if "lehtinen" in p.get("name","").lower() or "alba" in p.get("name","").lower():
            print(f"  FOUND: {p['name']} - {p.get('position','?')} - {p.get('status','?')}")
    
    signings = tr.get("signings", [])
    print(f"\nSignings: {len(signings)}")
    for s in signings:
        print(f"  {s.get('name')}")
