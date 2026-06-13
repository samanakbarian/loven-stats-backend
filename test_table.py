import os
import sys
import json
os.environ["GCP_PROJECT"] = "granskaren-d51a1"
os.environ["BQ_PROJECT_ID"] = "granskaren-d51a1"
sys.path.append(os.path.join(os.getcwd(), 'api'))

from main import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.get("/api/v1/analytics")
data = response.json()
print("shl_projected_table:", data.get("modules", {}).get("shl_projected_table"))
