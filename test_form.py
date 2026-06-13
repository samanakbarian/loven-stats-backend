import requests
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/Players/Statistics/ScoringLeaders/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
print("Forms:")
for form in soup.select('form'):
    print(form.get('action'), form.get('id'))
    for input in form.select('input, select'):
        print(f"  {input.get('name')}: {input.get('value')} ({input.name})")
