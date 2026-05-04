import os
import json
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from google.cloud import bigquery
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
BQ_PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "loven_marts")
BQ_LOVENLAGET_TABLE = os.environ.get("BQ_LOVENLAGET_TABLE", "mart_lovenlaget_snapshot")

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
    
    # "X lämnar Björklöven" / "massflykt från Björklöven" (also handle missing spaces from HTML parsing)
    if any(phrase in title for phrase in [
        'lämnar björklöven', 'lämnarbjörklöven', 'lämnar löven', 'lämnarlöven',
        'från björklöven', 'frånbjörklöven', 'från löven', 'frånlöven',
    ]):
        article["tag"] = "BEKRÄFTAD_FÖRLUST"
        return article
    
    # "X klar för Björklöven" / "X ansluter till Björklöven" / "nyförvärv"
    if any(phrase in title for phrase in [
        'klar för björklöven', 'klar förbjörklöven', 'klar för löven', 'klar förlöven',
        'ansluter till björklöven', 'ansluter tillbjörklöven', 'ansluter till löven',
    ]):
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


def compute_freshness_status(last_refresh_iso: str | None) -> str:
    if not last_refresh_iso:
        return "unknown"
    try:
        refreshed_at = datetime.fromisoformat(last_refresh_iso.replace("Z", "+00:00"))
    except Exception:
        return "unknown"

    age_seconds = (datetime.now(refreshed_at.tzinfo) - refreshed_at).total_seconds()
    if age_seconds <= 6 * 3600:
        return "fresh"
    if age_seconds <= 24 * 3600:
        return "stale"
    return "critical"


@app.get("/api/v1/lovenlaget")
def get_lovenlaget_snapshot():
    """
    Startsides-snapshot för nya frontenden.
    Returnerar komprimerade signaler med konsekvenstext + meta/freshness.
    """
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        table_fqn = f"`{bq_client.project}.{BQ_DATASET}.{BQ_LOVENLAGET_TABLE}`"
        query = f"""
            select *
            from {table_fqn}
            order by snapshot_at desc
            limit 1
        """
        rows = list(bq_client.query(query).result())
        if rows:
            row = rows[0]
            return {
                "title": "Lövenläget",
                "season": "2026/2027",
                "league": row.get("league") or "SHL",
                "readiness": {
                    "score": int(row.get("readiness_score") or 0),
                    "summary": row.get("readiness_summary") or "",
                },
                "critical_now": [
                    row.get("critical_1") or "",
                    row.get("critical_2") or "",
                    row.get("critical_3") or "",
                ],
                "latest_impact": {
                    "title": row.get("latest_impact_title") or "Inga nya signaler ännu",
                    "impact_level": row.get("latest_impact_level") or "medium",
                    "meaning": row.get("latest_impact_meaning") or "Vi väntar på nya verifierade signaler.",
                },
                "squad_status": {
                    "goalies": row.get("goalies_status") or "bevaka",
                    "defense": row.get("defense_status") or "bevaka",
                    "centers": row.get("centers_status") or "bevaka",
                    "forwards": row.get("forwards_status") or "bevaka",
                },
                "economy_status": {
                    "risk_level": row.get("economy_risk_level") or "okänd",
                    "budget_pressure": row.get("economy_budget_pressure") or "okänd",
                    "next_question": row.get("economy_next_question") or "Vad behöver prioriteras nu?",
                },
                "meta": {
                    "schema_version": row.get("schema_version") or "v1",
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "source_updated_at": row.get("source_updated_at").isoformat() if row.get("source_updated_at") else None,
                    "freshness_status": row.get("freshness_status") or "unknown",
                    "new_signals": int(row.get("new_signals") or 0),
                    "scraped_articles": int(row.get("scraped_articles") or 0),
                    "expiring_contracts": int(row.get("expiring_contracts") or 0),
                },
            }
    except Exception as e:
        logging.warning(f"Kunde inte läsa mart_lovenlaget_snapshot från BigQuery, fallback till heuristik: {e}")

    silly = get_silly_season()
    meta = silly.get("_meta", {})
    source_updated_at = meta.get("lastRefresh")
    freshness_status = compute_freshness_status(source_updated_at)

    roster = silly.get("roster", [])
    departures = silly.get("confirmed_departures", [])
    signings = silly.get("confirmed_signings", [])
    expiring = silly.get("expiring_contracts", [])

    gk_count = sum(1 for p in roster if p.get("pos") == "GK")
    d_count = sum(1 for p in roster if p.get("pos") in ("LD", "RD"))
    c_count = sum(1 for p in roster if p.get("pos") == "CE")
    fw_count = sum(1 for p in roster if p.get("pos") in ("LW", "RW", "CE"))

    readiness_score = max(45, min(90, 62 + len(signings) * 2 - max(0, len(departures) - 5)))
    critical_now = [
        "Toppback saknas",
        "Centerdjup osäkert" if c_count < 4 else "Centerdjup behöver spets",
        "Ekonomiskt tryck måste bevakas",
    ]

    latest_signal = None
    if silly.get("news_feed"):
        latest_signal = silly["news_feed"][0]

    return {
        "title": "Lövenläget",
        "season": silly.get("season", "2026/2027"),
        "league": silly.get("league", "SHL"),
        "readiness": {
            "score": readiness_score,
            "summary": "Nära, men två luckor kan sänka bygget.",
        },
        "critical_now": critical_now,
        "latest_impact": {
            "title": latest_signal.get("title") if latest_signal else "Inga nya signaler ännu",
            "impact_level": "high" if len(departures) > len(signings) else "medium",
            "meaning": "Det här flyttar nålen direkt och påverkar lagbalansen." if latest_signal else "Vi väntar på nya verifierade signaler.",
        },
        "squad_status": {
            "goalies": "stabilt" if gk_count >= 2 else "bevaka",
            "defense": "kritisk lucka" if d_count < 8 else "bevaka",
            "centers": "bevaka" if c_count < 4 else "stabilt",
            "forwards": "stabilt" if fw_count >= 10 else "bevaka",
        },
        "economy_status": {
            "risk_level": "medel",
            "budget_pressure": "högt",
            "next_question": "Har klubben råd med två spetsnamn?",
        },
        "meta": {
            "schema_version": "v1",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "source_updated_at": source_updated_at,
            "freshness_status": freshness_status,
            "new_signals": meta.get("newArticles", 0),
            "scraped_articles": meta.get("scrapedArticles", 0),
            "expiring_contracts": len(expiring),
        },
    }

# @app.get("/api/v1/games/{game_id}/momentum")
# def get_momentum(game_id: str):
#     # Anropa BigQuery här
#     pass
