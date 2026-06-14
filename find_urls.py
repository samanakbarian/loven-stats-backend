import requests
import re
r = requests.get('https://www.hockeyallsvenskan.se/assets/index-BCPHuM_P.js')
urls = re.findall(r'https://[^\"\']+', r.text)
for u in set(urls):
    if 'api' in u.lower() or 'stats' in u.lower() or 's8y' in u.lower():
        print(u)
