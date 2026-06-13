from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
data = client.get("/api/v1/analytics?season=ha_2526").json()

tr = data["modules"]["shl_transition"]
sk_adj = []
for p in tr.get("skaters", []):
    print(f"{p['name']}: {p.get('adj_proj_ppg')}")
    sk_adj.append(p.get("adj_proj_ppg", 0))

print("\nAvg sk_adj:", sum(sk_adj) / len(sk_adj) if sk_adj else 0)

bjk_points_model = 58.0
avg_sk_adj = sum(sk_adj) / len(sk_adj) if sk_adj else 0.35
g_adj = [g.get("proj_sv_pct", 0) for g in tr.get("goalies", [])]
avg_g_adj = sum(g_adj) / len(g_adj) if g_adj else 89.5

import json
from api.silly_season_data import SILLY_SEASON_BASELINE
signings_count = len(SILLY_SEASON_BASELINE.get("confirmed_signings", []))
departures_count = len(SILLY_SEASON_BASELINE.get("confirmed_departures", []))
expiring_count = len(SILLY_SEASON_BASELINE.get("expiring_contracts", []))

print("sk_adj:", avg_sk_adj, "g_adj:", avg_g_adj)
print("s:", signings_count, "d:", departures_count, "e:", expiring_count)

bjk_points_model += (avg_sk_adj - 0.38) * 80.0
bjk_points_model += (avg_g_adj - 89.5) * 2.4
# special teams
bjk_points_model += (96.5 - 95.0) * 0.35 # using dummy special teams
bjk_points_model += signings_count * 1.8
bjk_points_model -= departures_count * 0.5
bjk_points_model -= expiring_count * 0.9

print("Raw points model:", bjk_points_model)
print("Bounded:", max(46.0, min(96.0, bjk_points_model)))
