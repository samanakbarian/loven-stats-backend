import os
from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
r = client.get("/api/v1/statistics?season=ha_2324")
print("HA 23/24 games:", r.json().get("counts", {}).get("team_games"))

r2 = client.get("/api/v1/statistics?season=shl_2526")
print("SHL 25/26 games:", r2.json().get("counts", {}).get("team_games"))

r3 = client.get("/api/v1/statistics?season=ha_2526")
print("HA 25/26 games:", r3.json().get("counts", {}).get("team_games"))
