import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Players/Statistics/ScoringLeaders/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
for a in soup.select('a'):
    if 'ScoringLeaders' in a.get('href', ''):
        print(a.get('href'), a.text.strip())
