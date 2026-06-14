import requests
from bs4 import BeautifulSoup
import re
import json

def _clean(s):
    if s is None: return ""
    return re.sub(r"\s+", " ", str(s)).strip()

def _fetch_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    r.encoding = 'utf-8' # Force utf-8
    return r.text

def _extract_table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.tblNormal")
    rows = []
    for table in tables:
        tr_elements = table.select("tr")
        for tr in tr_elements:
            cells = tr.select("th,td")
            if not cells: continue
            rows.append([_clean(c.get_text(" ", strip=True)) for c in cells])
    return rows

url = "https://stats.swehockey.se/ScheduleAndResults/Schedule/20961"
html = _fetch_html(url)
print("Bytes test:", "Björklöven".encode("utf-8"))
rows = _extract_table_rows(html)

for r in rows:
    game_str = ""
    for i, col in enumerate(r):
        c = _clean(col)
        if " - " in c and len(c) > 7:
            game_str = c
            break
    if game_str and "IF Bj" in game_str:
        print("MATCHED ROW:", game_str)
        home, away = game_str.split(" - ", 1)
        print("Home:", home)
        print("Away:", away)
        break
