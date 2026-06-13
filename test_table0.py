import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Teams/Info/PlayersByTeam/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
tables = soup.select('table')
print(tables[0].select('tr')[1].text)
print(tables[0].select('tr')[2].text)
print(tables[0].select('tr')[3].text)
