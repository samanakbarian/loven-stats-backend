import urllib.request
import json
try:
    req = urllib.request.Request('https://loven-stats-api-324947473206.europe-west1.run.app/api/v1/analytics?season=ha_2526')
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    table = data['modules']['silly_season']['shl_projected_table']['table']
    for r in table:
        print(f"{r.get('projected_rank')}: {r.get('team')} - {r.get('projected_points')}")
except Exception as e:
    print('Error:', e)
