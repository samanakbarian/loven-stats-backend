import urllib.request
import json
url = "https://loven-stats-api-324947473206.europe-west1.run.app/api/v1/analytics?season=ha_2526"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    d = json.loads(response.read().decode())
    shl_goalies = d.get('modules', {}).get('silly_season', {}).get('shl_readiness', {}).get('goalies', [])
    so_goalies = d.get('modules', {}).get('season_overview', {}).get('goalies', [])
    with open('test_goalies_out.txt', 'w', encoding='utf-8') as f:
        f.write('shl_readiness goalies: ' + str([g['name'] for g in shl_goalies]) + '\n')
        f.write('season_overview goalies: ' + str([g['name'] for g in so_goalies]) + '\n')
