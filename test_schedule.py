import sys
import requests
from bs4 import BeautifulSoup

urls = [
    "https://stats.swehockey.se/Teams/Info/Schedule/1139",
    "https://stats.swehockey.se/ScheduleAndResults/Schedule/18263",
]

for u in urls:
    html = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}).text
    soup = BeautifulSoup(html, "lxml")
    trs = soup.select("table tr")
    print(f"URL: {u}")
    print(f"Total TRs: {len(trs)}")
    
    # how many match games
    matches = 0
    for tr in trs:
        text = tr.get_text()
        if "-" in text and "IF Björklöven" in text:
            matches += 1
    print(f"Matches for Björklöven: {matches}")
