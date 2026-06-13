import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Players/Statistics/ScoringLeaders/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
for select in soup.select('select'):
    print(select.get('name'), select.get('id'))
    for option in select.select('option'):
        print(f"  {option.get('value')} - {option.text}")
