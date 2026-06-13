from google.cloud import bigquery
client = bigquery.Client()
players = [dict(row.items()) for row in client.query("SELECT player_name, team_code, team_name, games_played, goals, assists, points FROM `granskaren-d51a1.raw_sports.swehockey_player_stats` WHERE season_group_id = 19979").result()]

def is_bjk(t):
    if not t: return False
    return "björklöven" in t.lower() or "bjorkloven" in t.lower() or t.upper() == "IFB"

roster = ["Fredrik Forsberg", "Daniel Brodin", "Lenni Killinen"]

def name_tokens(name):
    if not name: return set()
    s = name.lower()
    s = s.replace("ö", "o").replace("ä", "a").replace("å", "a")
    s = s.replace("?", "")
    s = s.replace(",", " ").replace("-", " ").replace("'", " ")
    return {t for t in s.split() if len(t) > 1}

def match_player(raw_name):
    tokens = name_tokens(raw_name)
    if not tokens: return None
    for r in roster:
        rtokens = name_tokens(r)
        common = tokens.intersection(rtokens)
        if len(common) >= min(len(tokens), len(rtokens)) or len(common) >= 2:
            return r
    return None

def is_bjk_player(p):
    # original buggy code:
    # return is_bjk(p.get("team")) or match_player(p.get("name"))
    
    # wait! In main.py, it was:
    return is_bjk(p.get("team")) or match_player(p.get("name"))

bjk_skaters = [p for p in players if is_bjk_player(p)]
print("bjk_skaters found:", len(bjk_skaters))

for name in roster:
    # original logic:
    candidates = []
    for p in players:
        # BUG: The 'or' logic
        if not (is_bjk(p.get("team_code", "")) or is_bjk(p.get("team_name", ""))):
            continue
        # BUG: Using p.get("player_name") here but in bjk_skaters it used p.get("name")
        if match_player(p.get("player_name")) == name:
            candidates.append(p)
            
    print(f"Candidates for {name}: {len(candidates)}")
    bq_p = candidates[0] if candidates else None
    if bq_p:
        print(f"  {name} found in candidates!")
    else:
        # THIS IS WHAT HAPPENS
        print(f"  {name} NOT found! bq_p is None.")
        # But wait! bq_p = next((p for p in bjk_skaters if match_player(p.get("name")) == name), None)
        # That's what main.py line 644 says?
        # WAIT! In main.py, it actually says:
        # candidates = []
        # for p in players:
        #   if not (is_bjk(p.get("team_code", "")) or is_bjk(p.get("team_name", ""))):
        #       continue
        #   if match_player(p.get("player_name")) == name:
        #       candidates.append(p)
