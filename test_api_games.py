from fastapi.testclient import TestClient
from api.main import app
import json

client = TestClient(app)
response = client.get("/api/v1/statistics?season=ha_2324")
data = response.json()
print(f"HA 23/24 games: {len(data['games'])}")
