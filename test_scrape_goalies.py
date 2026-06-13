import sys
from bs4 import BeautifulSoup
import requests

url = "https://stats.swehockey.se/Teams/Info/PlayersByTeam/18263"
html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
soup = BeautifulSoup(html, "lxml")
tables = soup.select("table")

for i, table in enumerate(tables):
    rows = table.select("tr")
    if rows:
        first_row = [c.get_text(" ", strip=True) for c in rows[0].select("th,td")]
        print(f"Table {i} - First row: {first_row}")
        if len(rows) > 1:
            second_row = [c.get_text(" ", strip=True) for c in rows[1].select("th,td")]
            print(f"Table {i} - Second row: {second_row}")
