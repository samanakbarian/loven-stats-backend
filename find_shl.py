import urllib.request
from bs4 import BeautifulSoup
import re
url = 'https://stats.swehockey.se/'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
html = urllib.request.urlopen(req).read().decode('utf-8')
soup = BeautifulSoup(html, 'html.parser')
for a in soup.find_all('a', href=True):
    if 'HockeyAllsvenskan' in a.text:
        print(f"{a.text.strip()}: {a['href']}")
