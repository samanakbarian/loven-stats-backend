import requests
from bs4 import BeautifulSoup
import re

url = 'https://stats.swehockey.se/ScheduleAndResults/Standings/18266'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'lxml')
for a in soup.select('a'):
    href = a.get('href')
    if href and 'Team' in href:
        print(href, a.text.strip())
