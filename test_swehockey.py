import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Players/Statistics/ScoringLeaders/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
tables = soup.select('table')
if tables:
    print(f'Rows in main table: {len(tables[0].select("tr"))}')
else:
    print('No tables')
