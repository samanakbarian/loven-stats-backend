import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Teams/Info/PlayersByTeam/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
tables = soup.select('table')
for i, t in enumerate(tables):
    rows = t.select('tr')
    if len(rows) > 0:
        first_row = [c.text.strip() for c in rows[0].select('th,td')]
        if len(first_row) > 0 and 'Top' in first_row[-1]:
            print(f"Table {i}: Team: {first_row[0]}, Rows: {len(rows)}")
