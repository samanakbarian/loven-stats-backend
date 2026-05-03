import functions_framework
import json
import logging
import requests
import os
from google.cloud import storage
from silly_scraper import run_scraper

# Inställning av logging
logging.basicConfig(level=logging.INFO)

# Inställningar
# I produktion hämtas dessa via miljövariabler satta via Secret Manager
SPORTRADAR_API_KEY = os.environ.get("SPORTRADAR_API_KEY", "2g9qsmEhHWO7SJ7hBIMJnNIP8Bu9QZmxU0CH6zty")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
# Bas-URL för Sportradar Global Ice Hockey v2 API (Trial/Developer miljö)
BASE_URL = "https://api.sportradar.com/icehockey/trial/v2/en"

def upload_to_gcs(bucket_name, destination_blob_name, data_dict):
    """Laddar upp en dictionary som JSON till en GCS bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        
        blob.upload_from_string(
            data=json.dumps(data_dict, ensure_ascii=False),
            content_type='application/json'
        )
        logging.info(f"Fil uppladdad till gs://{bucket_name}/{destination_blob_name}")
    except Exception as e:
        logging.error(f"Fel vid uppladdning till GCS: {e}")
        # Vid lokal testning utan GCP-autentisering, spara filen lokalt istället
        local_filename = destination_blob_name.split('/')[-1]
        logging.info(f"Lokal utveckling fallback: Sparar fil lokalt som {local_filename}")
        with open(local_filename, 'w', encoding='utf-8') as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)

def fetch_sportradar(endpoint):
    url = f"{BASE_URL}/{endpoint}.json"
    params = {"api_key": SPORTRADAR_API_KEY}
    try:
        logging.info(f"Hämtar från: {url.split('?')[0]}")
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Fel vid hämtning från {endpoint}: {e}")
        return None

@functions_framework.http
def fetch_sportradar_data(request):
    """
    HTTP Cloud Function som hämtar data från Sportradar API.
    By default, fetches HockeyAllsvenskan 25/26 summaries, standings, and Björklöven stats.
    """
    logging.info("Startar Sportradar Ingest...")
    SEASON_URN = "sr:season:131137"
    BJORKLOVEN_URN = "sr:competitor:3747"
    
    # 1. Hämta Spelschema (Summaries)
    summaries = fetch_sportradar(f"seasons/{SEASON_URN}/summaries")
    match_id_to_fetch = None
    if summaries:
        upload_to_gcs(GCS_BUCKET_NAME, f"raw/sportradar/{SEASON_URN}_summaries.json", summaries)
        
        # Hitta en avslutad Björklöven-match för att hämta play-by-play (timeline)
        for summary in summaries.get("summaries", []):
            sport_event = summary.get("sport_event", {})
            competitors = sport_event.get("competitors", [])
            has_loven = any(c.get("id") == BJORKLOVEN_URN for c in competitors)
            
            if has_loven and summary.get("sport_event_status", {}).get("status") == "closed":
                match_id_to_fetch = sport_event.get("id")
                break

    # 2. Hämta Timeline för en match (om hittad)
    if match_id_to_fetch:
        timeline = fetch_sportradar(f"sport_events/{match_id_to_fetch}/timeline")
        if timeline:
            upload_to_gcs(GCS_BUCKET_NAME, f"raw/sportradar/{match_id_to_fetch}_timeline.json", timeline)
            
    # 3. Hämta Tabell (Standings)
    standings = fetch_sportradar(f"seasons/{SEASON_URN}/standings")
    if standings:
        upload_to_gcs(GCS_BUCKET_NAME, f"raw/sportradar/{SEASON_URN}_standings.json", standings)
        
    # 4. Hämta Lagstatistik (Spelarpoäng)
    team_stats = fetch_sportradar(f"seasons/{SEASON_URN}/competitors/{BJORKLOVEN_URN}/statistics")
    if team_stats:
        upload_to_gcs(GCS_BUCKET_NAME, f"raw/sportradar/{SEASON_URN}_{BJORKLOVEN_URN}_statistics.json", team_stats)
        
    return json.dumps({
        "status": "success", 
        "message": "Data från Sportradar hämtad och sparad till GCS"
    }), 200, {'Content-Type': 'application/json'}
