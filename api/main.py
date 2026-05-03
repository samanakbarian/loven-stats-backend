import os
import json
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from datetime import datetime

import re
from silly_season_data import SILLY_SEASON_BASELINE

app = FastAPI(
    title="Löven Stats Hub API",
    description="Backend API for Löven Stats Hub, serving data from BigQuery & GCS",
    version="1.0.0"
)

# Tillåt CORS för frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Byt till Netlify-domänen i produktion
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Welcome to Löven Stats Hub API"}

@app.get("/api/v1/health")
def health_check():
    return {"status": "healthy"}

def normalize_title(title):
    return re.sub(r'[^\wåäö\s]', '', title.lower()).strip()

# Keyword-based fallback classification for articles that Gemini incorrectly tagged as ÖVRIGT
TRANSFER_KEYWORDS = {
    'BEKRÄFTAT_NYFÖRVÄRV': ['nyförvärv', 'klar för björklöven', 'skrivit på', 'signerar', 'värvning', 'ansluter till björklöven', 'ansluter till löven'],
    'BEKRÄFTAD_FÖRLUST': ['lämnar björklöven', 'lämnar löven', 'massflytt från björklöven', 'massflykt från björklöven', 'tackar av', 'följer inte med'],
    'KONTRAKTSFÖRLÄNGNING': ['förlänger med björklöven', 'förlängde med björklöven', 'förlänger med löven', 'förlängde med löven', 'nytt kontrakt med björklöven', 'stannar i löven', 'stannar i björklöven'],
    'HETT_RYKTE': ['rykte', 'ryktas till björklöven', 'ryktas till löven', 'intresse för', 'uppges', 'spekuleras', 'sillyrummet'],
}

def reclassify_tag(article):
    """Keyword-based fallback: reclassifies ÖVRIGT articles that clearly are transfer news."""
    tag = article.get("tag", "ÖVRIGT")
    if tag != "ÖVRIGT":
        return article
    
    text = (article.get("title", "") + " " + article.get("body", "")).lower()
    
    # Check if the article is about a player leaving Björklöven specifically
    is_bjorkloven_subject = any(kw in text for kw in ['björklöven', 'bjorkloven', 'löven'])
    if not is_bjorkloven_subject:
        return article
    
    # Check contract extensions FIRST (highest priority — "förlängde" + "skrivit på" should be extension, not signing)
    if any(kw in text for kw in ['förlänger', 'förlängde', 'förlängd']):
        article["tag"] = "KONTRAKTSFÖRLÄNGNING"
        return article
    
    # Check for direct transfer keywords combined with Björklöven context
    for new_tag, keywords in TRANSFER_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            article["tag"] = new_tag
            return article
    
    # Looser matching: "lämnar" + björklöven context
    if any(kw in text for kw in ['lämnar', 'klar för']):
        # Check if the leaving/joining is about Björklöven or another team
        # "X lämnar Björklöven" = BEKRÄFTAD_FÖRLUST
        # "X lämnar Y — klar för Björklöven" = BEKRÄFTAT_NYFÖRVÄRV 
        if 'lämnar' in text and ('björklöven' in text.split('lämnar')[1] if 'lämnar' in text else False):
            article["tag"] = "BEKRÄFTAD_FÖRLUST"
        elif 'klar för' in text and any(kw in text.split('klar för')[1] for kw in ['björklöven', 'löven'] if 'klar för' in text):
            article["tag"] = "BEKRÄFTAT_NYFÖRVÄRV"
        elif 'lämnar' in text:
            article["tag"] = "BEKRÄFTAD_FÖRLUST"
    
    return article

def deduplicate_articles(scraped, baseline):
    seen = set()
    for item in baseline:
        seen.add(normalize_title(item.get('title', '')))
    
    unique_scraped = []
    for item in scraped:
        normalized = normalize_title(item.get('title', ''))
        if normalized not in seen:
            seen.add(normalized)
            unique_scraped.append(item)
    return unique_scraped

@app.get("/api/silly-season")
def get_silly_season():
    """
    Hämtar senaste scraper-datan från GCS och mergar med baseline.
    """
    scraped_articles = []
    last_refresh = datetime.now().isoformat()
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        # Hämta blob med prefix raw/silly_season/scraped_ sorterat på senast uppdaterad
        blobs = list(bucket.list_blobs(prefix="raw/silly_season/scraped_"))
        
        if blobs:
            latest_blob = sorted(blobs, key=lambda b: b.updated or b.time_created, reverse=True)[0]
            content = latest_blob.download_as_string()
            data = json.loads(content)
            scraped_articles = data.get("news_feed", [])
            last_refresh = latest_blob.updated.isoformat() if latest_blob.updated else last_refresh
    except Exception as e:
        logging.error(f"Kunde inte hämta scraper-data från GCS: {e}")
        # Fortsätt med bara baseline
    
    baseline = SILLY_SEASON_BASELINE.copy()
    
    # Deduplicera och lägg till id
    new_articles = deduplicate_articles(scraped_articles, baseline.get("news_feed", []))
    
    for i, article in enumerate(new_articles):
        article["id"] = f"scraped-{i}"
        article["scraped"] = True
        
        # Reclassify articles that Gemini incorrectly tagged as ÖVRIGT
        reclassify_tag(article)
        
        # Om tiden saknas, försök extrahera den eller sätt aktuell tid
        if "time" not in article:
            article["time"] = datetime.now().strftime("%H:%M")

    # Slå ihop och sortera fallande på datum, sedan tid
    merged_feed = new_articles + baseline.get("news_feed", [])
    merged_feed.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
    
    baseline["news_feed"] = merged_feed
    
    if "_meta" not in baseline:
        baseline["_meta"] = {}
        
    baseline["_meta"]["lastRefresh"] = last_refresh
    baseline["_meta"]["newArticles"] = len(new_articles)
    baseline["_meta"]["scrapedArticles"] = len(scraped_articles)
    
    return baseline

# @app.get("/api/v1/games/{game_id}/momentum")
# def get_momentum(game_id: str):
#     # Anropa BigQuery här
#     pass
