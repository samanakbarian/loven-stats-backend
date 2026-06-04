import re
def _clean(s):
    return re.sub(r"\s+", " ", str(s)).strip()

rows = [
    ['2025-09-19', '2025-09-19 19:00', '19:00', 'MoDo Hockey - Östersunds IK', '3 - 1', '(0-0, 1-1, 2-0)', '7298', 'Hägglunds Arena'],
    ['19:00', '', '19:00', 'Södertälje SK - BIK Karlskoga', '1 - 4', '(0-2, 0-1, 1-1)', '5694', 'Scaniarinken'],
    ['1', '2011-11-12 09:00', 'Hanvikens SK - Saltsjöbadens IF', '2 - 3', '', '', 'Tyresö Ishall']
]

out = []
current_date = ""
for r in rows:
    if len(r) < 3:
        continue
    
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[0]))
    if date_match:
        current_date = date_match.group(0)
    elif re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[1])):
        current_date = re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[1])).group(0)

    game_str = ""
    result_str = ""
    for i, col in enumerate(r):
        c = _clean(col)
        if " - " in c and len(c) > 7:
            if re.search(r"[a-zA-ZÅÄÖåäö]", c):
                game_str = c
                if i + 1 < len(r):
                    result_str = _clean(r[i+1])
                break
    
    if not game_str or " - " not in game_str:
        continue
        
    home_team, away_team = game_str.split(" - ", 1)
    out.append({
        "match_date": current_date,
        "home_team": _clean(home_team),
        "away_team": _clean(away_team),
        "result": result_str,
    })

for o in out:
    print(o)
