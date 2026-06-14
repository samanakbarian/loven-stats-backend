import requests
import re
r = requests.get('https://www.hockeyallsvenskan.se/assets/index-BCPHuM_P.js')
urls = re.findall(r'https://[^\"\']+', r.text)
api_urls = [u for u in urls if 'api' in u.lower() or 'stats' in u.lower()]
for u in set(api_urls):
    print(u)
