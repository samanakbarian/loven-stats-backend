import requests
from bs4 import BeautifulSoup

html = requests.get('https://stats.swehockey.se/ScheduleAndResults/Standings/20822').text
soup = BeautifulSoup(html, 'lxml')
for i, t in enumerate(soup.select('table')):
    print(f"Table {i} class: {t.get('class')}, rows: {len(t.select('tr'))}")
