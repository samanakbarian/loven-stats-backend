from api.main import app
from fastapi.testclient import TestClient
import json

client = TestClient(app)
response = client.get("/api/v1/analytics?season=ha_2526")
data = response.json()

tr = data["modules"]["shl_transition"]

print("\n--- SKATERS ---")
for p in tr.get("skaters", []):
    if "🆕" in p.get("name", ""):
        print(f"NEW SKATER: {p}")

print("\n--- GOALIES ---")
for p in tr.get("goalies", []):
    print(f"GOALIE: {p}")
    
