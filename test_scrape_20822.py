import os
import json
from google.cloud import bigquery
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone

def _clean(s):
    if s is None: return ""
    return re.sub(r"\s+", " ", str(s)).strip()

def _safe_int(v):
    try: return int(v)
    except: return 0

def scrape_standings(season_group_id):
    url = f"https://stats.swehockey.se/ScheduleAndResults/Standings/{season_group_id}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    r.encoding = 'utf-8'
    html = r.text
    
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.tblNormal")
    rows = []
    for table in tables:
        for tr in table.select("tr"):
            cells = tr.select("th,td")
            if cells:
                rows.append([_clean(c.get_text(" ", strip=True)) for c in cells])
                
    out = []
    for r in rows:
        if r and _clean(r[0]).lower() == "home":
            break
        if len(r) < 13 or not _safe_int(r[0]):
            continue
        out.append({
            "season_group_id": int(season_group_id),
            "team_name": _clean(r[1]),
            "rank": _safe_int(r[0]),
            "games_played": _safe_int(r[2]),
            "wins": _safe_int(r[3]),
            "ot_wins": _safe_int(r[9]) + _safe_int(r[11]),
            "ot_losses": _safe_int(r[10]) + _safe_int(r[12]),
            "losses": _safe_int(r[5]),
            "points": _safe_int(r[8]),
            "goals_for": _safe_int(r[6]),
            "goals_against": _safe_int(r[7]),
            "goal_difference": _safe_int(r[6]) - _safe_int(r[7]),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return out

client = bigquery.Client()
standings = scrape_standings(20822)
print(f"Scraped {len(standings)} teams for 20822")

if standings:
    table_id = f"{client.project}.raw_sports.swehockey_standings"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
    )
    import io
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in standings)
    job = client.load_table_from_file(io.StringIO(ndjson), table_id, job_config=job_config)
    job.result()
    print("Uploaded!")
