import os
import sys
from fastapi.testclient import TestClient

os.environ["GCP_PROJECT"] = "granskaren-d51a1"
os.environ["BQ_PROJECT_ID"] = "granskaren-d51a1"
sys.path.append(os.path.join(os.getcwd(), 'api'))

from main import app

client = TestClient(app)
response = client.get("/api/v1/analytics")
print("STATUS CODE:", response.status_code)
if response.status_code != 200:
    print("BODY:", response.text)
else:
    print("SUCCESS, body length:", len(response.text))
