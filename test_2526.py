import urllib.request, json

BASE = 'https://loven-stats-api-324947473206.europe-west1.run.app'

# Test ha_2526 (preseason/active) analytics
url = BASE + '/api/v1/analytics?season=ha_2526'
d = json.loads(urllib.request.urlopen(url).read())

if 'error' in d:
    print('ERROR ha_2526:', d['error'])
else:
    modules = d.get('modules', {})
    silly = modules.get('silly_season', {})
    shl_r = silly.get('shl_readiness', {})
    goalies = shl_r.get('goalies', [])
    print('=== ha_2526 SHL Readiness Goalies ===')
    for g in goalies:
        name = g.get('name')
        sv = g.get('proj_sv_pct')
        readiness = g.get('readiness')
        print(f'  {name}: proj_sv={sv}, readiness={readiness}')

    proj_table = silly.get('shl_projected_table', {}).get('table', [])
    print(f'\nProjected table: {len(proj_table)} teams')
    bjk = next((t for t in proj_table if 'bj' in t['team_name'].lower() or 'lov' in t['team_name'].lower()), None)
    print('Bjorkloven row:', bjk)
