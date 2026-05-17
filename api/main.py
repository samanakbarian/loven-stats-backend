import os
import json
import logging
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from google.cloud import bigquery
from datetime import datetime, timezone, timedelta

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
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_QUERY_DEFAULT = os.environ.get(
    "X_QUERY_DEFAULT",
    '(Björklöven OR Bjorkloven OR #Björklöven OR #Bjorkloven) -is:retweet -is:reply lang:sv'
)
X_MAX_RESULTS_DEFAULT = int(os.environ.get("X_MAX_RESULTS_DEFAULT", "30"))
X_QUERY_BROAD_DEFAULT = os.environ.get(
    "X_QUERY_BROAD_DEFAULT",
    '((Björklöven OR Bjorkloven OR #Björklöven OR #Bjorkloven OR Löven OR #Löven) (hockey OR SHL OR allsvenskan OR nyförvärv OR förlänger OR lämnar OR silly)) -is:retweet -is:reply lang:sv'
)
X_CACHE_BLOB = os.environ.get("X_CACHE_BLOB", "derived/x_feed/latest.json")
X_CACHE_MINUTES = int(os.environ.get("X_CACHE_MINUTES", "60"))
X_AI_ENABLED = os.environ.get("X_AI_ENABLED", "false").lower() == "true"
X_AI_MODEL = os.environ.get("X_AI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Welcome to Löven Stats Hub API"}

@app.get("/api/v1/health")
def health_check():
    return {"status": "healthy"}


@app.get("/api/v1/statistics")
def get_statistics_snapshot(team_query: str = Query(default="bjo,björklöven,bjorkloven")):
    """
    Returns latest Swehockey snapshot from raw_sports tables.
    This is a first module API for frontend Statistics tab.
    """
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        tokens = [t.strip().lower() for t in str(team_query or "").split(",") if t.strip()]
        if not tokens:
            tokens = ["bjo", "björklöven", "bjorkloven"]

        def _matches(value: str) -> bool:
            v = (value or "").lower()
            return any(token in v for token in tokens)

        def _query_rows(table_name: str):
            q = f"""
            WITH latest AS (
              SELECT MAX(scraped_at) AS max_scraped
              FROM `{bq_client.project}.raw_sports.{table_name}`
            )
            SELECT *
            FROM `{bq_client.project}.raw_sports.{table_name}`
            WHERE scraped_at = (SELECT max_scraped FROM latest)
            """
            return [dict(row.items()) for row in bq_client.query(q).result()]

        players = _query_rows("swehockey_player_stats")
        goalies = _query_rows("swehockey_goalie_stats")
        standings = _query_rows("swehockey_standings")
        schedule = _query_rows("swehockey_schedule")

        team_players = [p for p in players if _matches(str(p.get("team_code", "")))]
        team_goalies = [g for g in goalies if _matches(str(g.get("team_code", "")))]
        team_standing = next((s for s in standings if _matches(str(s.get("team_name", "")))), None)
        team_games = [m for m in schedule if _matches(str(m.get("home_team", ""))) or _matches(str(m.get("away_team", "")))]

        has_team_match = bool(team_players or team_goalies or team_standing or team_games)
        if not has_team_match:
            # Fallback: return league snapshot so UI is never empty.
            team_players = players
            team_goalies = goalies
            team_standing = standings[0] if standings else None
            team_games = schedule

        top_scorers = sorted(
            team_players,
            key=lambda p: (int(p.get("points") or 0), int(p.get("goals") or 0)),
            reverse=True
        )[:10]
        top_goalies = sorted(
            team_goalies,
            key=lambda g: float(g.get("save_pct") or 0.0),
            reverse=True
        )[:5]

        latest_times = []
        for rows in (players, goalies, standings, schedule):
            if rows and rows[0].get("scraped_at"):
                latest_times.append(str(rows[0]["scraped_at"]))

        return {
            "status": "ok",
            "source": "swehockey",
            "scope": "team" if has_team_match else "league_fallback",
            "team_query_tokens": tokens,
            "snapshot_scraped_at": max(latest_times) if latest_times else None,
            "counts": {
                "players_total": len(players),
                "goalies_total": len(goalies),
                "standings_total": len(standings),
                "schedule_total": len(schedule),
                "team_players": len(team_players),
                "team_goalies": len(team_goalies),
                "team_games": len(team_games),
            },
            "team_standing": team_standing,
            "top_scorers": top_scorers,
            "top_goalies": top_goalies,
            "upcoming_or_recent_games": team_games[:12],
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/statistics")
        return {
            "status": "error",
            "error": str(e),
        }

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


def sync_roster_with_confirmed_signings(baseline):
    roster = baseline.get("roster", []) or []
    signings = baseline.get("confirmed_signings", []) or []
    existing = {str((p.get("name") or "")).strip().lower() for p in roster}

    for s in signings:
        name = str((s.get("name") or "")).strip()
        if not name:
            continue
        key = name.lower()
        if key in existing:
            continue
        roster.append({
            "name": name,
            "number": s.get("number"),
            "pos": s.get("pos") or "FW",
            "status": "NYFÖRVÄRV",
            "contractUntil": s.get("contractUntil"),
            "note": s.get("note") or "",
        })
        existing.add(key)

    baseline["roster"] = roster
    return baseline

def article_identity(item):
    source = (item.get("source") or "").strip().lower()
    url = (item.get("url") or item.get("link") or "").strip().lower()
    title = normalize_title(item.get("title", ""))
    return f"{source}::{url}::{title}"

def compute_new_since_previous(current_scraped, previous_scraped):
    previous_ids = {article_identity(i) for i in (previous_scraped or [])}
    return [i for i in (current_scraped or []) if article_identity(i) not in previous_ids]

def _safe_date(value):
    try:
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def build_last_24h_summary(current_scraped, previous_scraped, critical_now):
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=24)
    current_scraped = current_scraped or []
    previous_scraped = previous_scraped or []

    new_items = compute_new_since_previous(current_scraped, previous_scraped)

    def in_window(item):
        dt = _safe_date(item.get("date"))
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= window_start

    recent = [item for item in current_scraped if in_window(item)]

    def count_tag(tag_name):
        return sum(1 for item in recent if item.get("tag") == tag_name)

    return {
        "new_signals": len(new_items),
        "articles_24h": len(recent),
        "signings": count_tag("BEKRÄFTAT_NYFÖRVÄRV"),
        "departures": count_tag("BEKRÄFTAD_FÖRLUST"),
        "extensions": count_tag("KONTRAKTSFÖRLÄNGNING"),
        "rumors": count_tag("HETT_RYKTE"),
        "critical_open": len(critical_now or []),
    }

def build_dynamic_silly_summary(feed, roster):
    now = datetime.utcnow()
    recent_cutoff = now.timestamp() - (120 * 24 * 3600)  # ~4 months

    def is_recent(item):
        dt = _safe_date(item.get("date"))
        if not dt:
            return True
        return dt.timestamp() >= recent_cutoff

    recent = [i for i in (feed or []) if is_recent(i)]

    signings = sum(1 for i in recent if i.get("tag") == "BEKRÄFTAT_NYFÖRVÄRV")
    departures = sum(1 for i in recent if i.get("tag") == "BEKRÄFTAD_FÖRLUST")
    extensions = sum(1 for i in recent if i.get("tag") == "KONTRAKTSFÖRLÄNGNING")
    expiring = sum(1 for p in (roster or []) if p.get("status") == "UTGÅENDE")

    return {
        "contracted": signings + extensions,
        "signings": signings,
        "expiring": expiring,
        "departures": departures,
        "extensions": extensions,
    }

def load_recent_silly_snapshots(limit=5):
    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blobs = list(bucket.list_blobs(prefix="raw/silly_season/scraped_"))
    if not blobs:
        return []
    sorted_blobs = sorted(blobs, key=lambda b: b.updated or b.time_created, reverse=True)[:limit]
    snapshots = []
    for blob in sorted_blobs:
        try:
            payload = json.loads(blob.download_as_string())
            feed = payload.get("news_feed", [])
            source_counts = {}
            for item in feed:
                source = item.get("source") or "unknown"
                source_counts[source] = source_counts.get(source, 0) + 1
            snapshots.append({
                "blob": blob.name,
                "updated_at": (blob.updated.isoformat() if blob.updated else None),
                "articles": len(feed),
                "source_counts": source_counts,
            })
        except Exception as e:
            snapshots.append({
                "blob": blob.name,
                "updated_at": (blob.updated.isoformat() if blob.updated else None),
                "articles": None,
                "error": str(e),
                "source_counts": {},
            })
    return snapshots

@app.get("/api/silly-season")
def get_silly_season():
    """
    Hämtar senaste scraper-datan från GCS och mergar med baseline.
    """
    scraped_articles = []
    previous_scraped_articles = []
    last_refresh = datetime.now().isoformat()
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        # Hämta blob med prefix raw/silly_season/scraped_ sorterat på senast uppdaterad
        blobs = list(bucket.list_blobs(prefix="raw/silly_season/scraped_"))
        
        if blobs:
            sorted_blobs = sorted(blobs, key=lambda b: b.updated or b.time_created, reverse=True)
            latest_blob = sorted_blobs[0]
            content = latest_blob.download_as_string()
            data = json.loads(content)
            scraped_articles = data.get("news_feed", [])
            last_refresh = latest_blob.updated.isoformat() if latest_blob.updated else last_refresh
            if len(sorted_blobs) > 1:
                prev_content = sorted_blobs[1].download_as_string()
                prev_data = json.loads(prev_content)
                previous_scraped_articles = prev_data.get("news_feed", [])
    except Exception as e:
        logging.error(f"Kunde inte hämta scraper-data från GCS: {e}")
        # Fortsätt med bara baseline
    
    baseline = SILLY_SEASON_BASELINE.copy()
    baseline = sync_roster_with_confirmed_signings(baseline)
    
    # Deduplicera mot baseline för presentation i feed
    deduped_for_feed = deduplicate_articles(scraped_articles, baseline.get("news_feed", []))
    # Beräkna verkligt nytt sedan förra scraper-snapshoten
    new_articles = compute_new_since_previous(scraped_articles, previous_scraped_articles)

    for i, article in enumerate(deduped_for_feed):
        article["id"] = f"scraped-{i}"
        article["scraped"] = True
        
        # Reclassify articles that Gemini incorrectly tagged as ÖVRIGT
        reclassify_tag(article)
        
        # Om tiden saknas, försök extrahera den eller sätt aktuell tid
        if "time" not in article:
            article["time"] = datetime.now().strftime("%H:%M")

    # Slå ihop och sortera fallande på datum, sedan tid
    merged_feed = deduped_for_feed + baseline.get("news_feed", [])
    merged_feed.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
    
    baseline["news_feed"] = merged_feed
    if merged_feed:
        latest = merged_feed[0]
        title = latest.get("title") or ""
        if title:
            baseline["headline"] = title
        # Ensure at least one fresh breaking candidate from latest feed item.
        latest.setdefault("priority", "breaking")
    
    if "_meta" not in baseline:
        baseline["_meta"] = {}
        
    baseline["_meta"]["lastRefresh"] = last_refresh
    baseline["_meta"]["newArticles"] = len(new_articles)
    baseline["_meta"]["scrapedArticles"] = len(scraped_articles)
    baseline["_meta"]["summary"] = build_dynamic_silly_summary(merged_feed, baseline.get("roster", []))
    baseline["_meta"]["last24h"] = build_last_24h_summary(scraped_articles, previous_scraped_articles, [])
    
    return baseline

@app.get("/api/silly-season/ops")
def get_silly_ops():
    """
    Intern driftvy för silly-pipeline.
    Returnerar senaste snapshot-körningar från GCS utan att påverka publik UI.
    """
    try:
        snapshots = load_recent_silly_snapshots(limit=5)
        latest_updated = snapshots[0]["updated_at"] if snapshots else None
        return {
            "status": "ok",
            "latest_updated_at": latest_updated,
            "freshness_status": compute_freshness_status(latest_updated),
            "runs": snapshots,
        }
    except Exception as e:
        logging.error(f"Kunde inte läsa silly ops-data: {e}")
        return {
            "status": "error",
            "error": str(e),
            "latest_updated_at": None,
            "freshness_status": "unknown",
            "runs": [],
        }


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


def _x_sentiment_score(text: str):
    t = (text or "").lower()
    positive = ["klar", "nyförvärv", "vinner", "förlänger", "stärker", "succé", "poängkung"]
    negative = ["lämnar", "skadad", "missar", "kris", "förlust", "sparken", "avslutar"]
    pos_hits = sum(1 for w in positive if w in t)
    neg_hits = sum(1 for w in negative if w in t)
    if pos_hits > neg_hits:
        return "positive", min(95, 55 + (pos_hits - neg_hits) * 10)
    if neg_hits > pos_hits:
        return "negative", min(95, 55 + (neg_hits - pos_hits) * 10)
    return "neutral", 50


def _fetch_x_recent(query: str, max_results: int):
    if not X_BEARER_TOKEN:
        return {"items": [], "error": "missing_token"}
    url = "https://api.x.com/2/tweets/search/recent"
    params = {
        "query": query,
        "max_results": max(10, min(100, max_results)),
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        if response.status_code != 200:
            return {"items": [], "error": f"x_http_{response.status_code}", "detail": response.text[:300]}
        payload = response.json()
        users = {u.get("id"): u for u in (payload.get("includes", {}).get("users", []) if isinstance(payload.get("includes", {}), dict) else [])}
        items = []
        for tweet in payload.get("data", []) or []:
            author = users.get(tweet.get("author_id"), {})
            username = author.get("username", "")
            text = tweet.get("text", "")
            sentiment_label, sentiment_score = _x_sentiment_score(text)
            items.append({
                "id": tweet.get("id"),
                "text": text,
                "created_at": tweet.get("created_at"),
                "author_name": author.get("name") or username or "okänd",
                "author_username": username,
                "url": f"https://x.com/{username}/status/{tweet.get('id')}" if username and tweet.get("id") else None,
                "lang": tweet.get("lang"),
                "public_metrics": tweet.get("public_metrics", {}),
                "source": "x",
                "sentiment_label": sentiment_label,
                "sentiment_score": sentiment_score,
            })
        return {"items": items, "error": None}
    except Exception as e:
        logging.error(f"X fetch failed: {e}")
        return {"items": [], "error": "x_fetch_failed"}


def _load_x_cache():
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(X_CACHE_BLOB)
        if not blob.exists():
            return None
        payload = json.loads(blob.download_as_text())
        return payload
    except Exception as e:
        logging.warning(f"Could not load X cache: {e}")
        return None


def _save_x_cache(payload):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(X_CACHE_BLOB)
        blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")
    except Exception as e:
        logging.warning(f"Could not save X cache: {e}")


def _cache_is_fresh(cache_payload):
    if not cache_payload:
        return False
    generated_at = cache_payload.get("meta", {}).get("generated_at")
    if not generated_at:
        return False
    dt = _safe_date(generated_at)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(minutes=X_CACHE_MINUTES)


def _latest_item_age_hours(items):
    if not items:
        return None
    latest = None
    for item in items:
        dt = _safe_date(item.get("created_at"))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if latest is None or dt > latest:
            latest = dt
    if latest is None:
        return None
    return (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0


def _has_item_from_today_utc(items):
    today = datetime.now(timezone.utc).date()
    for item in items:
        dt = _safe_date(item.get("created_at"))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc).date() == today:
            return True
    return False


def _build_x_ai_summary(items):
    if not X_AI_ENABLED:
        return {"enabled": False, "summary": "", "model": None, "error": "disabled"}
    if not GEMINI_API_KEY:
        return {"enabled": True, "summary": "", "model": X_AI_MODEL, "error": "missing_api_key"}
    if not items:
        return {"enabled": True, "summary": "Inga relevanta inlägg just nu.", "model": X_AI_MODEL, "error": None}
    top = items[:20]
    compact_lines = []
    for i, item in enumerate(top, 1):
        compact_lines.append(f"{i}. @{item.get('author_username','okand')}: {item.get('text','')[:220]}")
    prompt = (
        "Du analyserar ett svenskt socialt flöde om Björklöven.\n"
        "Skriv en kort sammanfattning på svenska (max 90 ord):\n"
        "1) Övergripande ton\n"
        "2) Viktigaste ämnen\n"
        "3) En tydlig risk eller möjlighet.\n"
        "Hitta inte på fakta utanför inläggen.\n\n"
        "Inlägg:\n" + "\n".join(compact_lines)
    )
    def fallback_summary():
        positives = sum(1 for i in items if i.get("sentiment_label") == "positive")
        negatives = sum(1 for i in items if i.get("sentiment_label") == "negative")
        neutrals = sum(1 for i in items if i.get("sentiment_label") == "neutral")
        top = sorted(items, key=lambda i: (i.get("public_metrics", {}).get("like_count", 0) + i.get("public_metrics", {}).get("retweet_count", 0) * 2), reverse=True)[:2]
        topics = ", ".join([f"@{t.get('author_username','okänd')}" for t in top]) if top else "inga tydliga toppsignaler"
        tone = "övervägande neutral" if neutrals >= max(positives, negatives) else ("övervägande positiv" if positives > negatives else "övervägande negativ")
        return (
            f"Flödet är {tone}. Positiva signaler: {positives}, negativa: {negatives}, neutrala: {neutrals}. "
            f"Mest synliga konton just nu: {topics}. Fokus ligger främst på truppsnack, rykten och SHL-uppladdning."
        )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{X_AI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 220}}
        res = requests.post(url, json=body, timeout=25)
        if res.status_code != 200:
            return {"enabled": True, "summary": fallback_summary(), "model": X_AI_MODEL, "error": f"gemini_http_{res.status_code}"}
        payload = res.json()
        parts = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = " ".join([p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]).strip()
        if len(text) < 60:
            text = fallback_summary()
        return {"enabled": True, "summary": text.strip(), "model": X_AI_MODEL, "error": None}
    except Exception as e:
        logging.warning(f"Gemini X summary failed: {e}")
        return {"enabled": True, "summary": fallback_summary(), "model": X_AI_MODEL, "error": "gemini_failed"}


def _build_x_payload(query: str, max_results: int):
    fetched = _fetch_x_recent(query, max_results)
    items = fetched.get("items", [])
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for item in items:
        counts[item.get("sentiment_label", "neutral")] = counts.get(item.get("sentiment_label", "neutral"), 0) + 1
    total = len(items) or 1
    ai_summary = _build_x_ai_summary(items)
    payload = {
        "query": query,
        "count": len(items),
        "items": items,
        "sentiment_summary": {
            "positive": counts["positive"],
            "neutral": counts["neutral"],
            "negative": counts["negative"],
            "positive_pct": round((counts["positive"] / total) * 100, 1),
            "negative_pct": round((counts["negative"] / total) * 100, 1),
        },
        "ai_summary": ai_summary,
        "meta": {
            "provider": "x_api_v2_recent_search",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "error": fetched.get("error"),
            "cache_minutes": X_CACHE_MINUTES,
            "ai_summary_ready": bool(ai_summary.get("summary")),
        }
    }
    return payload


def _build_x_payload_with_fallback(max_results: int):
    primary = _build_x_payload(X_QUERY_DEFAULT, max_results)
    primary_items = primary.get("items", []) or []
    primary_age_hours = _latest_item_age_hours(primary_items)
    needs_fallback = (
        len(primary_items) == 0
        or not _has_item_from_today_utc(primary_items)
        or (primary_age_hours is not None and primary_age_hours > 24)
    )

    if not needs_fallback:
        primary.setdefault("meta", {})
        primary["meta"]["query_mode"] = "primary"
        return primary

    fallback = _build_x_payload(X_QUERY_BROAD_DEFAULT, max_results)
    fallback_items = fallback.get("items", []) or []

    merged = []
    seen = set()
    for item in primary_items + fallback_items:
        item_id = item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        merged.append(item)

    merged.sort(
        key=lambda x: _safe_date(x.get("created_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    merged = merged[:max_results]

    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for item in merged:
        counts[item.get("sentiment_label", "neutral")] = counts.get(item.get("sentiment_label", "neutral"), 0) + 1
    total = len(merged) or 1

    ai_summary = _build_x_ai_summary(merged)
    payload = {
        "query": X_QUERY_DEFAULT,
        "count": len(merged),
        "items": merged,
        "sentiment_summary": {
            "positive": counts["positive"],
            "neutral": counts["neutral"],
            "negative": counts["negative"],
            "positive_pct": round((counts["positive"] / total) * 100, 1),
            "negative_pct": round((counts["negative"] / total) * 100, 1),
        },
        "ai_summary": ai_summary,
        "meta": {
            "provider": "x_api_v2_recent_search",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "error": primary.get("meta", {}).get("error") or fallback.get("meta", {}).get("error"),
            "cache_minutes": X_CACHE_MINUTES,
            "ai_summary_ready": bool(ai_summary.get("summary")),
            "query_mode": "fallback_merged",
        },
    }
    return payload


@app.get("/api/v1/x-feed")
def get_x_feed(force_refresh: bool = Query(False)):
    cached = _load_x_cache()
    if not force_refresh and _cache_is_fresh(cached):
        cached.setdefault("meta", {})
        cached["meta"]["from_cache"] = True
        return cached
    payload = _build_x_payload_with_fallback(X_MAX_RESULTS_DEFAULT)
    payload.setdefault("meta", {})
    payload["meta"]["from_cache"] = False
    _save_x_cache(payload)
    return payload


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
            "last_24h": meta.get("last24h") or {
                "new_signals": 0,
                "articles_24h": 0,
                "signings": 0,
                "departures": 0,
                "extensions": 0,
                "rumors": 0,
                "critical_open": len(critical_now),
            },
        },
    }

# @app.get("/api/v1/games/{game_id}/momentum")
# def get_momentum(game_id: str):
#     # Anropa BigQuery här
#     pass
