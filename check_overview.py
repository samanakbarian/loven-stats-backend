import urllib.request
from bs4 import BeautifulSoup
req = urllib.request.Request('https://stats.swehockey.se/ScheduleAndResults/Overview/20962', headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req).read().decode('utf-8', errors='replace')
soup = BeautifulSoup(raw, 'html.parser')
teams = [a.text.strip() for a in soup.select('table.tblContent td a')]
print('Total links in Overview:', len(teams))
print('Björklöven in Overview:', any('Bj' in t for t in teams))
