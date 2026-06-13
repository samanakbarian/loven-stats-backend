import urllib.request
import re
url = 'https://stats.swehockey.se/ScheduleAndResults/Standings/20962'
html = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})).read().decode('utf-8')
urls = re.findall(r'"(/[a-zA-Z0-9_\-\./]+)"', html)
print(set(urls))
