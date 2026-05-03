import requests
import json

api_key = '2g9qsmEhHWO7SJ7hBIMJnNIP8Bu9QZmxU0CH6zty'
base_url = 'https://api.sportradar.com/icehockey/trial/v2/en'

# 1. Hitta HockeyAllsvenskan
print("Hämtar ligor...")
comps = requests.get(f'{base_url}/competitions.json?api_key={api_key}').json()
ha_comp = None
for c in comps.get('competitions', []):
    if 'Allsvenskan' in c['name'] or 'HockeyAllsvenskan' in c['name']:
        ha_comp = c
        break

if ha_comp:
    print(f"Hittade liga: {ha_comp['name']} (ID: {ha_comp['id']})")
    
    # 2. Hämta lag (Competitors) för HockeyAllsvenskan. 
    # API: /competitions/{competition_id}/competitors.json (not standard, let's use seasons)
    seasons = requests.get(f"{base_url}/competitions/{ha_comp['id']}/seasons.json?api_key={api_key}").json()
    if 'seasons' in seasons and len(seasons['seasons']) > 1:
        # Index 0 är oftast kommande säsong (som är tom nu), vi tar föregående (index 1)
        latest_season = seasons['seasons'][1]['id']
        print(f"Hittade föregående säsong: {latest_season}")
        
        # 3. Hämta lag
        teams = requests.get(f"{base_url}/seasons/{latest_season}/competitors.json?api_key={api_key}").json()
        
        bjorven = None
        for t in teams.get('season_competitors', []):
            if 'Björklöven' in t.get('name', '') or 'Bjorkloven' in t.get('name', ''):
                bjorven = t
                break
                
        if bjorven:
            print(f"Hittade IF Björklöven! ID: {bjorven['id']}")
            
            # 4. Hämta Roster / Truppstatus för Silly Season!
            print("\nHämtar Truppstatus (Competitor Profile)...")
            profile = requests.get(f"{base_url}/competitors/{bjorven['id']}/profile.json?api_key={api_key}").json()
            
            players = profile.get('players', [])
            print(f"\nTruppstatus för Björklöven:")
            print(f"Antal kontrakterade spelare i API:et: {len(players)}")
            
            print("\nSpelare:")
            for p in players[:5]: # Visa de 5 första
                print(f"- {p.get('name')} ({p.get('type')})")
            if len(players) > 5:
                print("... och fler.")
                
        else:
            print("Kunde inte hitta Björklöven. Kanske i en annan säsong?")
else:
    print("Hittade inte HockeyAllsvenskan i listan.")
