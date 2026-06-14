import requests
from bs4 import BeautifulSoup

# SHL 24/25 season
r = requests.get('https://stats.swehockey.se/Teams/Info/PlayersByTeam/18263')
soup = BeautifulSoup(r.text, 'lxml')
tables = soup.select('table')
for i, t in enumerate(tables):
    if t.find('table'):
        continue
    rows = t.find_all('tr', recursive=False)
    if not rows and t.find('tbody'):
        rows = t.find('tbody').find_all('tr', recursive=False)
    if not rows:
        continue
    first_row_text = [c.get_text(' ', strip=True) for c in rows[0].select('th,td')]
    if 'Goalkeeping Statistics' in first_row_text:
        print(f'Table {i} headers: {first_row_text}')
        for j, tr in enumerate(rows[1:6]):
            cols = tr.select('th,td')
            row_text = [c.get_text(' ', strip=True) for c in cols]
            print(f'  Row {j+1} len={len(cols)}: {row_text}')
        break
print("Done")
