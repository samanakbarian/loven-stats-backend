import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Teams/Info/PlayersByTeam/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
table = soup.select('table')[0]
rows = table.select('tr')
for i in range(15):
    if i < len(rows):
        cells = [c.text.strip() for c in rows[i].select('th,td')]
        print(cells)
