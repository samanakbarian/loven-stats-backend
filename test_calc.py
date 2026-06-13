from api.main import app
from fastapi.testclient import TestClient
client = TestClient(app)
data = client.get("/api/v1/analytics?season=ha_2526").json()

tr = data["modules"]["shl_transition"]
skaters = tr.get("skaters", [])
goalies = tr.get("goalies", [])

sk_adj = [s.get("adj_proj_ppg", 0) for s in skaters]
g_adj = [g.get("proj_sv_pct", 0) for g in goalies]

print("sk_adj len:", len(sk_adj), sum(sk_adj)/len(sk_adj) if sk_adj else 0)
print("g_adj len:", len(g_adj), sum(g_adj)/len(g_adj) if g_adj else 0)

