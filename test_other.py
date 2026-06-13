import os
import sys
from fastapi.testclient import TestClient

os.environ["GCP_PROJECT"] = "granskaren-d51a1"
os.environ["BQ_PROJECT_ID"] = "granskaren-d51a1"
sys.path.append(os.path.join(os.getcwd(), 'api'))

from main import app

client = TestClient(app)
response = client.get("/api/v1/lovenlaget")
print("LOVENLAGET STATUS:", response.status_code)
if response.status_code != 200:
    print(response.text)

response2 = client.get("/api/silly-season")
print("SILLY STATUS:", response2.status_code)
if response2.status_code != 200:
    print(response2.text)
