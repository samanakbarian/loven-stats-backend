import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Teams/Info/PlayersByTeam/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
tables = soup.select('table')
print(f"Tables: {len(tables)}")
if tables:
    for i, t in enumerate(tables):
        print(f"  Table {i} rows: {len(t.select('tr'))}")
