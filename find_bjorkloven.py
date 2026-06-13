import urllib.request
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/ScheduleAndResults/Schedule/20962'
html = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})).read().decode('utf-8')
soup = BeautifulSoup(html, 'html.parser')

for a in soup.find_all('a', href=True):
    if 'Björklöven' in a.text or 'Bjorkloven' in a.text:
        print(f"{a.text.strip()}: {a['href']}")
