import urllib.request
from bs4 import BeautifulSoup

url = 'https://stats.swehockey.se/ScheduleAndResults/Standings/20962'
html = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})).read().decode('utf-8')
soup = BeautifulSoup(html, 'html.parser')

table = soup.find('table', {'class': 'tblNormal'})
if table:
    for row in table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) > 2:
            print(cells[1].text.strip())
