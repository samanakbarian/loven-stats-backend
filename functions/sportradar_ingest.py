import functions_framework
import requests
import json
import logging
from datetime import datetime
import os
from google.cloud import storage

logging.basicConfig(level=logging.INFO)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
SPORTRADAR_API_KEY = os.environ.get("SPORTRADAR_API_KEY")

SEASON_URN = "sr:season:131137" # HA 25/26
BJORKLOVEN_URN = "sr:competitor:3747"

def fetch_sportradar(endpoint):
    if not SPORTRADAR_API_KEY:
        logging.error("Saknar SPORTRADAR_API_KEY i miljövariabler")
        return None
    url = f"https://api.sportradar.com/icehockey/trial/v2/en/{endpoint}.json?api_key={SPORTRADAR_API_KEY}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Fel vid hämtning från Sportradar ({endpoint}): {e}")
        return None

def save_to_gcs(data, filename):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"raw/sportradar/{filename}")
        blob.upload_from_string(json.dumps(data, ensure_ascii=False), content_type='application/json')
        logging.info(f"Sparade {filename} till GCS.")
    except Exception as e:
        logging.error(f"GCS error för {filename}: {e}")

@functions_framework.http
def run_sportradar_ingest(request):
    logging.info("Startar Sportradar Ingest...")
    
    # 1. Hämta Spelschema (Summaries)
    summaries = fetch_sportradar(f"seasons/{SEASON_URN}/summaries")
    if summaries:
        save_to_gcs(summaries, f"{SEASON_URN}_summaries.json")
        
        # Försök hitta en intressant Björklöven-match för att hämta timeline (play-by-play)
        # Vi letar efter en "closed" match
        match_id_to_fetch = None
        for summary in summaries.get("summaries", []):
            sport_event = summary.get("sport_event", {})
            status = sport_event.get("status", "unknown")
            competitors = sport_event.get("competitors", [])
            has_loven = any(c.get("id") == BJORKLOVEN_URN for c in competitors)
            
            if has_loven and summary.get("sport_event_status", {}).get("status") == "closed":
                match_id_to_fetch = sport_event.get("id")
                break
                
        if match_id_to_fetch:
            timeline = fetch_sportradar(f"sport_events/{match_id_to_fetch}/timeline")
            if timeline:
                save_to_gcs(timeline, f"{match_id_to_fetch}_timeline.json")
    
    # 2. Hämta Tabell (Standings)
    standings = fetch_sportradar(f"seasons/{SEASON_URN}/standings")
    if standings:
        save_to_gcs(standings, f"{SEASON_URN}_standings.json")
        
    # 3. Hämta Lagstatistik (Spelarpoäng)
    team_stats = fetch_sportradar(f"seasons/{SEASON_URN}/competitors/{BJORKLOVEN_URN}/statistics")
    if team_stats:
        save_to_gcs(team_stats, f"{SEASON_URN}_{BJORKLOVEN_URN}_statistics.json")
        
    return json.dumps({"status": "success", "message": "Sportradar data inhämtad och sparad till GCS"}), 200, {'Content-Type': 'application/json'}
