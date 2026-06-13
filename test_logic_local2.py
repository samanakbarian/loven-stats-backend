from google.cloud import bigquery
import re
client = bigquery.Client()
q = '''
SELECT player_name, team_code, games_played, goals, assists, points 
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats` 
WHERE season_group_id = 19979
'''
players = [dict(row.items()) for row in client.query(q).result()]

def is_bjk(t):
    if not t: return False
    return "björklöven" in t.lower() or "bjorkloven" in t.lower() or t.upper() == "IFB"

def clean_name(name):
    if not name: return ""
    name = re.split(r'\b(Pos|Abuse|Diving|Charging|Illegal|Unsportsmanlike|Kneeing)\b', name)[0]
    return name.strip()

def name_tokens(name):
    if not name: return set()
    s = name.lower()
    s = s.replace("ö", "o").replace("ä", "a").replace("å", "a")
    s = s.replace("?", "")
    s = s.replace(",", " ").replace("-", " ").replace("'", " ")
    return {t for t in s.split() if len(t) > 1}

roster_names = ["Fredrik Forsberg", "Daniel Brodin", "Lenni Killinen"]

def match_player(raw_name):
    cname = clean_name(raw_name)
    tokens = name_tokens(cname)
    if not tokens: return None
    for r in roster_names:
        rtokens = name_tokens(r)
        common = tokens.intersection(rtokens)
        if len(common) >= min(len(tokens), len(rtokens)) or len(common) >= 2:
            return r
    return None

for name in roster_names:
    candidates = []
    for p in players:
        if not is_bjk(p.get("team_code", "")):
            continue
        if match_player(p.get("player_name")) == name:
            candidates.append(p)
            
    print(f"Candidates for {name}: {len(candidates)}")
    if candidates:
        print("  found:", candidates[0])
