import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Teams/Info/PlayersByTeam/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
for h3 in soup.select('h3'):
    print(h3.text)
for div in soup.select('.teamHeading, h2, h3, h4'):
    print(div.name, div.text.strip())
