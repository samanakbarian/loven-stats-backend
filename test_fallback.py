import os
import sys
import json
os.environ["GCP_PROJECT"] = "granskaren-d51a1"
os.environ["BQ_PROJECT_ID"] = "granskaren-d51a1"
sys.path.append(os.path.join(os.getcwd(), 'api'))

from api.main import get_analytics

res = get_analytics()
print("Modules length:", len(res.get("modules", [])))
print("First module:", res.get("modules", [])[0])
print("Type of first module:", type(res.get("modules", [])[0]))
