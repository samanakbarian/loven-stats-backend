import sys
from bs4 import BeautifulSoup
import requests

url = "https://stats.swehockey.se/Teams/Info/PlayersByTeam/18263"
html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
soup = BeautifulSoup(html, "lxml")
tables = soup.select("table")

for i, table in enumerate(tables):
    rows = table.find_all("tr", recursive=False)
    if not rows and table.find("tbody"):
        rows = table.find("tbody").find_all("tr", recursive=False)
    print(f"Table {i} has {len(rows)} direct rows. Nested TRs: {len(table.select('tr'))}")
