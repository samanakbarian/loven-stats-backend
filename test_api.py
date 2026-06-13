import urllib.request, json
try:
    with urllib.request.urlopen('https://loven-stats-api-ttpybm4dva-ew.a.run.app/api/v1/analytics?cache_bypass=true') as response:
        data = json.loads(response.read().decode('utf-8'))
        goalies = data['modules']['goalie_radar']
        timeline = data['modules']['timeline']
        print(f'Goalies count: {len(goalies)}')
        for g in goalies:
            print("Goalie:", g['name'])
        print(f'Timeline games count: {len(timeline["games"])}')
except Exception as e:
    print(e)
