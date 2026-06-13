import requests
from bs4 import BeautifulSoup
import time
import sys

BASE = "https://stats.swehockey.se"

def probe(sid):
    try:
        r = requests.get(f"{BASE}/ScheduleAndResults/Standings/{sid}", 
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string if soup.title else ""
        text = soup.get_text(" ")
        
        if "SHL" in text or "SHL" in title:
            for year_pattern in ["2024/25", "2024/2025"]:
                if year_pattern in text:
                    return f"SHL {year_pattern}"
        return None
    except:
        return None

found = {}
print("Scanning for SHL 2024/2025...")
# SHL 25/26 is 18263. HA 23/24 is 15135.
# So SHL 24/25 is between 15500 and 17000 probably.
for sid in range(16000, 17000, 5):
    result = probe(sid)
    if result:
        print(f"Found something near {sid}")
        for narrow in range(max(sid-5, 16000), sid+5):
            r2 = probe(narrow)
            if r2:
                print(f"Match: {narrow}: {r2}")
                sys.exit(0)
    time.sleep(0.1)

print("Not found in 16000-17000. Scanning 15000-16000...")
for sid in range(15000, 16000, 5):
    result = probe(sid)
    if result:
        print(f"Found something near {sid}")
        for narrow in range(max(sid-5, 15000), sid+5):
            r2 = probe(narrow)
            if r2:
                print(f"Match: {narrow}: {r2}")
                sys.exit(0)
    time.sleep(0.1)
