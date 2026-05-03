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


def reclassify_tag(article):
    """
    Conservative keyword-based fallback: only reclassifies ÖVRIGT articles where
    the TITLE clearly indicates a direct Björklöven transfer action.
    
    Gemini is usually right to tag things ÖVRIGT — we only override when the title
    unambiguously is about a player joining/leaving/extending with Björklöven.
    """
    tag = article.get("tag", "ÖVRIGT")
    if tag != "ÖVRIGT":
        return article
    
    title = article.get("title", "").lower()
    
    # Only reclassify based on TITLE, not body (body often mentions Björklöven in passing)
    title_mentions_bjorkloven = any(kw in title for kw in ['björklöven', 'bjorkloven'])
    # Be careful with 'löven' — too short, matches 'slöven', 'Gullöven' etc.
    # Only match ' löven' or start-of-string 'löven'
    if not title_mentions_bjorkloven:
        if title.startswith('löven') or ' löven' in title:
            title_mentions_bjorkloven = True
    
    if not title_mentions_bjorkloven:
        return article
    
    # Exclude "tidigare Björklöven-spelaren" / "ex-Björklöven" patterns (former players, not current squad)
    if any(kw in title for kw in ['tidigare', 'ex-', 'f.d.', 'före detta', 'forna']):
        return article
    
    # Now check for specific transfer actions IN THE TITLE tied to Björklöven
    # "X förlänger/förlängde med Björklöven"
    if any(kw in title for kw in ['förlänger', 'förlängde', 'förlängd']):
        article["tag"] = "KONTRAKTSFÖRLÄNGNING"
        return article
    
    # "X lämnar Björklöven" / "massflykt från Björklöven"  
    if any(phrase in title for phrase in ['lämnar björklöven', 'lämnar löven', 'från björklöven', 'från löven']):
        article["tag"] = "BEKRÄFTAD_FÖRLUST"
        return article
    
    # "X klar för Björklöven" / "X ansluter till Björklöven" / "nyförvärv"
    if any(phrase in title for phrase in ['klar för björklöven', 'klar för löven', 'ansluter till björklöven', 'ansluter till löven']):
        article["tag"] = "BEKRÄFTAT_NYFÖRVÄRV"
        return article
    if 'nyförvärv' in title and title_mentions_bjorkloven:
        article["tag"] = "BEKRÄFTAT_NYFÖRVÄRV"
        return article
    
    # Don't reclassify anything else — trust Gemini's judgment
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
