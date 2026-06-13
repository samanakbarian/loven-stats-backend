import requests
from bs4 import BeautifulSoup
import re

url = "https://stats.swehockey.se/ScheduleAndResults/Standings/18263"
headers = {"User-Agent": "Mozilla/5.0"}
r = requests.get(url, headers=headers)
html = r.text

soup = BeautifulSoup(html, "lxml")
table = soup.select_one("table.table") or soup.select_one("table")
if table:
    for i, tr in enumerate(table.select("tr")[:5]):
        cells = tr.select("th,td")
        row = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip() for c in cells]
        print(f"Row {i}: {row}")
else:
    print("No table found")
