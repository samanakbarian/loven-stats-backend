"""Quick probe to find HA 23/24 season_group_id on stats.swehockey.se"""
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
        # Check for breadcrumb or heading with season info
        text = soup.get_text(" ")
        
        if "HockeyAllsvenskan" in text:
            # Extract year info
            for year_pattern in ["2023/24", "2023/2024", "2022/23", "2024/25", "2021/22", "2020/21"]:
                if year_pattern in text:
                    return f"HA {year_pattern}"
        if "hockeyallsvenskan" in title.lower():
            return f"HA (title: {title[:80]})"
        return None
    except:
        return None

# HA 25/26 regular = 18266
# HA 25/26 playoff = 19979
# Let's try subtracting known deltas: typically ~2000 between seasons
# Also try scanning around plausible ranges

# Known: HA 25/26 = 18266
# Hypothesis: HA 24/25 ~16xxx, HA 23/24 ~14xxx-15xxx

# Let's probe systematically in batches
print("Probing swehockey IDs to find HA seasons...")
print("=" * 50)

# Try wide range first with bigger steps, then narrow down
found = {}

for start, end in [(15200, 15400), (14800, 15200), (15400, 15700), (16000, 16500), (14000, 14800)]:
    print(f"\nScanning {start}-{end}...")
    for sid in range(start, end, 5):  # Step by 5 for speed
        result = probe(sid)
        if result:
            # Narrow down nearby
            for narrow in range(max(sid-5, start), min(sid+5, end)):
                r2 = probe(narrow)
                if r2:
                    found[narrow] = r2
                    print(f"  {narrow}: {r2}")
                time.sleep(0.2)
        time.sleep(0.15)
    
    if found:
        print(f"\nFound so far: {found}")
        # Check if we have what we need
        for sid, label in found.items():
            if "2023/24" in label:
                print(f"\n✅ HA 23/24 = {sid}")
                sys.exit(0)

print("\nAll found:", found)
