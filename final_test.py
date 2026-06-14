import urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = 'https://loven-stats-api-324947473206.europe-west1.run.app'

for season in ['ha_2425', 'ha_2526']:
    url = BASE + '/api/v1/analytics?season=' + season
    d = json.loads(urllib.request.urlopen(url).read())
    if 'error' in d:
        print(f'{season} ERROR: {d["error"][:200]}')
        continue
    silly = d.get('modules', {}).get('silly_season', {})
    shl_r = silly.get('shl_readiness', {})
    goalies = shl_r.get('goalies', [])
    table = silly.get('shl_projected_table', {}).get('table', [])

    print(f'\n=== {season} ===')
    print('SHL Readiness Goalies:')
    for g in goalies:
        print(f'  {g.get("name")}: ha_sv={g.get("ha_sv_pct")}, proj_sv={g.get("proj_sv_pct")}, readiness={g.get("readiness")}')

    print(f'Projected table: {len(table)} teams')
    bjk = next((t for t in table if 'bj' in str(t.get('team_name', '')).lower()), None)
    if bjk:
        print(f'  Bjorkloven: rank={bjk.get("rank")} pts={bjk.get("points")}')
    else:
        print('  Bjorkloven not found')
    if table:
        print(f'  Top 3: {[(t["team_name"], t["points"]) for t in table[:3]]}')

# Also verify seasons dropdown
url2 = BASE + '/api/v1/seasons'
d2 = json.loads(urllib.request.urlopen(url2).read())
print('\n=== Seasons Dropdown ===')
for s in d2.get('seasons', []):
    print(f'  {s["key"]} | {s["name"]} | active={s["is_active"]}')
print(f'Active: {d2.get("active")}')
