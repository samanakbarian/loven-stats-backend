import urllib.request
import re
req = urllib.request.Request('https://stats.swehockey.se/', headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req).read().decode('utf-8', errors='replace')
for m in re.finditer(r'<a[^>]+href=[\'\"]([^\'\"]*\/([0-9]+))[\'\"][^>]*>([^<]*HockeyAllsvenskan[^<]*)<\/a>', raw, re.IGNORECASE):
    print(m.group(2), m.group(3).strip())
