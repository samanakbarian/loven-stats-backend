"""
Silly Season Scraper v2 — Björklöven transfer news aggregator.

Runs as a Google Cloud Function. Collects transfer-related news from:
  1. Google News RSS (primary — catches all media + official bjorkloven.com)
  2. EliteProspects transfers page (secondary — confirmed transactions)

Classifies articles as:
  KONTRAKTSFÖRLÄNGNING, BEKRÄFTAT_NYFÖRVÄRV, BEKRÄFTAD_FÖRLUST, HETT_RYKTE

Saves results to GCS as JSON for frontend consumption.
"""

import functions_framework
import requests
from bs4 import BeautifulSoup
import json
import logging
import re
import hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
import os
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

logging.basicConfig(level=logging.INFO)

# ─── Configuration ───────────────────────────────────────────────────────────

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
PROJECT_ID = "granskaren-d51a1"
LOCATION = "europe-west1"
CACHE_BLOB_NAME = "raw/silly_season/article_ai_cache.json"
OFFICIAL_RENDERED_BLOB_NAME = os.environ.get(
    "OFFICIAL_RENDERED_BLOB_NAME",
    "raw/silly_season/official_rendered_latest.json",
)
MAX_CACHE_ITEMS = 20000
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AI_DISABLED = os.environ.get("AI_DISABLED", "false").lower() == "true"
MAX_GEMINI_CALLS_PER_RUN = int(os.environ.get("MAX_GEMINI_CALLS_PER_RUN", "15"))

# ─── Keywords ────────────────────────────────────────────────────────────────

# Björklöven identity tokens — used for relevance filtering
BJORKLOVEN_TOKENS = [
    'björklöven', 'bjorkloven', 'björklövens', 'bjorklovens',
    'löven',  # short form used by media
]

# Classification keyword sets — checked in priority order
EXTENSION_KEYWORDS = [
    'förlänger', 'forlanger', 'förlängde', 'forlangde',
    'förlängning', 'forlangning', 'kontraktsförlängning',
    'nytt kontrakt', 'skriver nytt', 'nytt avtal',
    'stannar kvar', 'stannar i',
]

SIGNING_KEYWORDS = [
    'klar för björklöven', 'klar for bjorkloven',
    'klar för löven', 'klar for loven',
    'ansluter till björklöven', 'ansluter till bjorkloven',
    'nyförvärv', 'nyforvarv', 'värvar', 'varvar',
    'signerar', 'skrivit på', 'skrivit pa',
    'förstärker', 'forstarker',
]

LOSS_KEYWORDS = [
    'lämnar björklöven', 'lamnar bjorkloven',
    'lämnade björklöven', 'lamnade bjorkloven',
    'lämnar löven', 'lamnar loven',
    'lämnade löven', 'lamnade loven',
    'tackar av', 'inte förlänger', 'inte forlanger',
    'klar för ny klubb', 'klar for ny klubb',
    'klar för annan', 'klar for annan',
    'lämnar', 'lamnar', 'lämnade', 'lamnade',  # broader, checked last
]

RUMOR_KEYWORDS = [
    'rykte', 'ryktas', 'uppges', 'kopplas',
    'intresse', 'jagas', 'kan värva', 'kan varva',
    'uppgifter:', 'uppgifter',
    'spekuleras', 'enligt uppgifter',
]

# Transfer-relevance check — article must contain at least one of these
TRANSFER_RELEVANCE_WORDS = (
    EXTENSION_KEYWORDS + SIGNING_KEYWORDS + LOSS_KEYWORDS + RUMOR_KEYWORDS +
    ['kontrakt', 'transfer', 'övergång', 'overgang', 'utlåning', 'utlaning']
)

# Exclude women's-team coverage from this pipeline (scope is men's roster build).
WOMENS_CONTEXT_KEYWORDS = [
    "sdhl", "damhockey", "damlag", "damlaget", "damernas", "damerna",
    "damspelare", "kvinnliga", "women", "womens", "flickor", "f19", "f18",
    "f17", "f16",
]

# ─── Helpers ─────────────────────────────────────────────────────────────────

def fetch_url(url, timeout=15):
    """Fetch URL with a browser-like User-Agent."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logging.error("Fetch failed for %s: %s", url, e)
        return None


def fetch_article_body(url, max_len=1500):
    """Fetch article page and extract body text for enriched classification."""
    html = fetch_url(url)
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        # Try to find article text in common containers
        parts = [p.get_text(" ", strip=True) for p in soup.select('article p, main p, .article p')]
        text = " ".join(p for p in parts if p)
        return text[:max_len]
    except Exception:
        return ""


def has_bjorkloven_context(text):
    """Check if text mentions Björklöven or related terms."""
    t = text.lower()
    return any(token in t for token in BJORKLOVEN_TOKENS)


def is_transfer_relevant(text):
    """Check if text contains any transfer-related keywords."""
    t = text.lower()
    return any(kw in t for kw in TRANSFER_RELEVANCE_WORDS)


def is_womens_context(text):
    """Filter out women's-team content from silly feed."""
    t = (text or "").lower()
    return any(kw in t for kw in WOMENS_CONTEXT_KEYWORDS)


def is_womens_url(url):
    """Filter out common women's-team URL patterns."""
    u = (url or "").lower()
    womens_url_tokens = ["/dam", "/damer", "sdhl", "/f19", "/f18", "/f17", "/f16"]
    return any(tok in u for tok in womens_url_tokens)


def normalize_title(title):
    """Normalize a title for deduplication."""
    t = re.sub(r'[^\wåäö\s]', '', (title or '').lower())
    return re.sub(r'\s+', ' ', t).strip()


def title_similarity(a, b):
    """Compute similarity ratio between two normalized titles."""
    return SequenceMatcher(None, a, b).ratio()


def make_fingerprint(source, title, url):
    """Create a deterministic fingerprint for deduplication and caching."""
    payload = "||".join([
        (source or "").strip().lower(),
        (url or "").strip().lower(),
        normalize_title(title),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Classification ─────────────────────────────────────────────────────────

def classify_article(title, body="", source=""):
    """
    Classify a transfer article into one of the tag categories.
    Returns (tag, confidence) where confidence is 'high' or 'low'.

    Priority order:
      1. KONTRAKTSFÖRLÄNGNING — extension keywords
      2. BEKRÄFTAT_NYFÖRVÄRV — signing keywords + Björklöven context
      3. BEKRÄFTAD_FÖRLUST — loss keywords + Björklöven context
      4. HETT_RYKTE — rumor keywords
      5. None — not transfer-relevant

    Source from bjorkloven.com automatically gets higher confidence.
    """
    text = f"{title} {body}".lower()
    is_official = "bjorkloven" in (source or "").lower()
    bj_ctx = has_bjorkloven_context(text)

    # 1. Extensions — most specific, always relevant if Björklöven context
    if any(kw in text for kw in EXTENSION_KEYWORDS):
        # Filter out false positives: game contexts, not contract extensions
        false_positive_ctx = [
            'segersvit', 'segerserien', 'vinst', 'poängserie',
            'efter förlängning', 'efter forlangning',  # overtime in hockey
            'kvartsfinal', 'semifinal', 'final',  # game results
            'vann', 'förlorade',
        ]
        if any(fp in text for fp in false_positive_ctx):
            pass  # Not a contract extension, fall through
        elif bj_ctx or is_official:
            return "KONTRAKTSFÖRLÄNGNING", "high"
        else:
            # Only classify if clearly about a contract
            contract_words = ['kontrakt', 'avtal', 'säsong', 'skriver', 'stannar']
            if any(cw in text for cw in contract_words):
                return "KONTRAKTSFÖRLÄNGNING", "low"

    # 2. Signings — player joining Björklöven
    if any(kw in text for kw in SIGNING_KEYWORDS):
        if bj_ctx or is_official:
            return "BEKRÄFTAT_NYFÖRVÄRV", "high"

    # 3. Losses — player leaving Björklöven
    if any(kw in text for kw in LOSS_KEYWORDS):
        if bj_ctx or is_official:
            return "BEKRÄFTAD_FÖRLUST", "high" if is_official else "low"

    # 4. Rumors
    if any(kw in text for kw in RUMOR_KEYWORDS):
        if bj_ctx or is_official:
            return "HETT_RYKTE", "low"

    return None, None


# ─── AI Analysis ─────────────────────────────────────────────────────────────

def analyze_with_gemini(text):
    """Use Vertex AI Gemini to classify and analyze a hockey news article."""
    try:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        model = GenerativeModel(GEMINI_MODEL)

        prompt = f"""Analysera följande hockeynyhet med fokus på IF Björklöven:
"{text}"

Avgör om nyheten handlar om Björklövens LAGBYGGE (spelare in, ut, förlängningar, rykten).
Om nyheten bara nämner Björklöven i förbigående (t.ex. "spelat i Björklöven tidigare") → ÖVRIGT.

Returnera ENBART giltigt JSON:
{{
  "tag": "BEKRÄFTAT_NYFÖRVÄRV" | "BEKRÄFTAD_FÖRLUST" | "KONTRAKTSFÖRLÄNGNING" | "HETT_RYKTE" | "ÖVRIGT",
  "sentiment_pct": 0-100,
  "pros": ["..."],
  "cons": ["..."],
  "impact_type": "positive" | "negative" | null,
  "impact_text": "kort text" | null
}}"""

        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        data = json.loads(response.text)
        valid_tags = {"BEKRÄFTAT_NYFÖRVÄRV", "BEKRÄFTAD_FÖRLUST", "KONTRAKTSFÖRLÄNGNING", "HETT_RYKTE", "ÖVRIGT"}
        if data.get("tag") not in valid_tags:
            data["tag"] = "ÖVRIGT"
        return data
    except Exception as e:
        logging.error("Gemini error: %s", e)
        return {"tag": "ÖVRIGT", "sentiment_pct": 50, "pros": [], "cons": [], "impact_type": None, "impact_text": None}


# ─── AI with caching and budget ─────────────────────────────────────────────

def get_ai_analysis(fingerprint, text, ai_cache, stats):
    """Get AI analysis with caching and budget management."""
    if AI_DISABLED:
        stats["gemini_skipped_disabled"] += 1
        return None

    # Check cache
    cached = ai_cache.get(fingerprint)
    if cached and isinstance(cached, dict):
        stats["cache_hits"] += 1
        return cached.get("analysis")

    # Check budget
    if stats["gemini_calls"] >= MAX_GEMINI_CALLS_PER_RUN:
        stats["gemini_skipped_budget"] += 1
        return None

    # Call Gemini
    stats["gemini_calls"] += 1
    analysis = analyze_with_gemini(text)
    ai_cache[fingerprint] = {
        "analysis": analysis,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return analysis


# ─── GCS I/O ─────────────────────────────────────────────────────────────────

def load_ai_cache():
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(CACHE_BLOB_NAME)
        if not blob.exists():
            return {}
        data = json.loads(blob.download_as_string())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logging.warning("Could not load AI cache: %s", e)
        return {}


def save_ai_cache(cache):
    try:
        if len(cache) > MAX_CACHE_ITEMS:
            items = sorted(cache.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
            cache = dict(items[:MAX_CACHE_ITEMS])
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(CACHE_BLOB_NAME)
        blob.upload_from_string(json.dumps(cache, ensure_ascii=False), content_type='application/json')
    except Exception as e:
        logging.warning("Could not save AI cache: %s", e)


def save_to_gcs(data):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    blob_name = f"raw/silly_season/scraped_{ts}.json"
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json.dumps(data, ensure_ascii=False), content_type='application/json')
        logging.info("Saved to gs://%s/%s", GCS_BUCKET_NAME, blob_name)
    except Exception as e:
        logging.error("GCS save error: %s", e)


def load_official_rendered_items():
    """Load pre-rendered official Bjorkloven items from GCS and normalize shape."""
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(OFFICIAL_RENDERED_BLOB_NAME)
        if not blob.exists():
            return []
        payload = json.loads(blob.download_as_string())
        items = payload.get("news_feed", []) if isinstance(payload, dict) else []
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            link = (item.get("url") or item.get("link") or "").strip()
            if not title or not link:
                continue
            normalized.append({
                "title": title,
                "link": link,
                "pub_date": item.get("date", ""),
                "source_name": item.get("source", "OfficialRendered (Bjorkloven)"),
                "query_label": "official_rendered",
            })
        return normalized
    except Exception as e:
        logging.warning("Could not load official rendered items: %s", e)
        return []


# ─── Sources ─────────────────────────────────────────────────────────────────

def fetch_google_news_rss(query, label=""):
    """Fetch Google News RSS results for a search query. Returns list of article dicts."""
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=sv&gl=SE&ceid=SE:sv"
    xml = fetch_url(url)
    if not xml:
        return []

    articles = []
    try:
        # Try xml parser first, fallback to html.parser if lxml not installed
        try:
            soup = BeautifulSoup(xml, "xml")
        except Exception:
            soup = BeautifulSoup(xml, "html.parser")
        for item in soup.find_all("item"):
            title = (item.title.text or "").strip() if item.title else ""
            link = (item.link.text or "").strip() if item.link else ""
            pub_date = (item.pubDate.text or "").strip() if item.pubDate else ""
            source_name = (item.source.text or "").strip() if item.source else ""

            if not title or not link:
                continue

            articles.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source_name": source_name,
                "query_label": label,
            })
    except Exception as e:
        logging.error("Google News RSS parse error (%s): %s", label, e)

    logging.info("Google News RSS [%s]: %d items", label, len(articles))
    return articles


def fetch_eliteprospects():
    """Scrape EliteProspects transfers page for Björklöven transactions."""
    url = 'https://www.eliteprospects.com/transfers'
    html = fetch_url(url)
    if not html:
        return []

    articles = []
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for row in soup.select('div[class*="TransactionsTable_row"]'):
            text = row.get_text(strip=True)
            if has_bjorkloven_context(text):
                articles.append({
                    "title": f"EP: {text[:80]}",
                    "link": url,
                    "pub_date": "",
                    "source_name": "EliteProspects",
                    "query_label": "eliteprospects",
                })
    except Exception as e:
        logging.error("EliteProspects parse error: %s", e)

    logging.info("EliteProspects: %d items", len(articles))
    return articles


# ─── Deduplication ───────────────────────────────────────────────────────────

def deduplicate_articles(articles):
    """Remove duplicate articles based on normalized title similarity."""
    unique = []
    seen_titles = []

    for art in articles:
        norm = normalize_title(art.get("title", ""))
        if not norm:
            continue

        is_dupe = False
        for seen in seen_titles:
            if title_similarity(norm, seen) > 0.70:
                is_dupe = True
                break

        if not is_dupe:
            unique.append(art)
            seen_titles.append(norm)

    return unique


# ─── Parse pub date ──────────────────────────────────────────────────────────

def parse_pub_date(pub_date_str):
    """Parse RFC 2822 date from RSS into ISO date string."""
    if not pub_date_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── Main pipeline ──────────────────────────────────────────────────────────

def process_articles(raw_articles, ai_cache, stats):
    """Process raw articles: filter, classify, enrich, and return news items."""
    results = []

    for art in raw_articles:
        title = art["title"]
        link = art["link"]
        source_name = art.get("source_name", "")
        pub_date = parse_pub_date(art.get("pub_date", ""))

        full_text = title
        body = ""

        # Step 0: keep scope to men's roster build only.
        if is_womens_context(f"{title} {source_name}") or is_womens_url(link):
            continue

        # Step 1: Must be Björklöven-relevant
        if not has_bjorkloven_context(f"{title} {link} {source_name}"):
            continue

        # Step 2: Must be transfer-relevant
        if not is_transfer_relevant(title):
            # Try fetching article body for more context
            body = fetch_article_body(link)
            if is_womens_context(body):
                continue
            if body and is_transfer_relevant(body):
                full_text = f"{title} {body}"
            else:
                continue
        else:
            # Title can still hide women's context; fetch body for guard check.
            body = fetch_article_body(link)
            if is_womens_context(body):
                continue
            if body:
                full_text = f"{title} {body}"

        # Step 3: Classify
        tag, confidence = classify_article(title, body, source_name)

        if tag is None:
            # Last resort: try with article body
            if not body:
                body = fetch_article_body(link)
                full_text = f"{title} {body}"
            tag, confidence = classify_article(title, body, source_name)

        if tag is None:
            continue  # Not classifiable, skip

        # Step 4: For low-confidence or rumors, optionally use AI
        ai_data = None
        if confidence == "low" or tag == "HETT_RYKTE":
            fingerprint = make_fingerprint(source_name, title, link)
            ai_data = get_ai_analysis(fingerprint, full_text, ai_cache, stats)
            if ai_data and ai_data.get("tag") == "ÖVRIGT":
                stats["ai_filtered"] = stats.get("ai_filtered", 0) + 1
                continue  # AI says it's not relevant, skip
            if ai_data and ai_data.get("tag") in {"BEKRÄFTAT_NYFÖRVÄRV", "BEKRÄFTAD_FÖRLUST", "KONTRAKTSFÖRLÄNGNING", "HETT_RYKTE"}:
                tag = ai_data["tag"]  # AI refines the tag

        # Step 5: Build result
        impact = None
        if ai_data and tag not in ("HETT_RYKTE", "ÖVRIGT") and ai_data.get("impact_type"):
            impact = {
                "type": ai_data["impact_type"],
                "impact_toi": ai_data.get("impact_text"),
                "impact_points": ai_data.get("impact_text"),
            }

        results.append({
            "title": title,
            "body": (body or "")[:200],
            "source": source_name or art.get("query_label", "unknown"),
            "url": link,
            "date": pub_date,
            "tag": tag,
            "ai_analysis": ai_data if tag == "HETT_RYKTE" else None,
            "impact": impact,
        })

    return results


# ─── HTTP Entry Point ────────────────────────────────────────────────────────

@functions_framework.http
def run_scraper(request):
    """HTTP Cloud Function entry point."""
    logging.info("Starting Silly Season Scraper v2...")
    ai_cache = load_ai_cache()
    stats = {
        "gemini_calls": 0,
        "cache_hits": 0,
        "gemini_skipped_disabled": 0,
        "gemini_skipped_budget": 0,
    }

    # ── Fetch from all sources ──────────────────────────────────────────

    # Primary: Google News RSS with two complementary queries
    gn_official = fetch_google_news_rss(
        'site:bjorkloven.com (förlänger OR klar OR lämnar OR nyförvärv OR kontrakt OR värvar OR ansluter)',
        label="gn_official"
    )
    gn_transfer = fetch_google_news_rss(
        '"Björklöven" (förlänger OR klar för OR lämnar OR nyförvärv OR kontrakt OR värvar)',
        label="gn_transfer"
    )

    # Secondary: EliteProspects
    ep_items = fetch_eliteprospects()
    official_items = load_official_rendered_items()

    # Combine all raw articles
    all_raw = gn_official + gn_transfer + ep_items + official_items
    logging.info(
        "Raw articles: gn_official=%d, gn_transfer=%d, ep=%d, official=%d, total=%d",
        len(gn_official), len(gn_transfer), len(ep_items), len(official_items), len(all_raw)
    )

    # ── Deduplicate before processing ───────────────────────────────────

    deduped = deduplicate_articles(all_raw)
    logging.info("After dedup: %d articles (removed %d dupes)", len(deduped), len(all_raw) - len(deduped))

    # ── Process: filter, classify, enrich ───────────────────────────────

    articles = process_articles(deduped, ai_cache, stats)
    logging.info("Classified articles: %d", len(articles))

    # ── Final URL-based dedup ───────────────────────────────────────────

    seen_urls = set()
    unique_articles = []
    for art in articles:
        url = art.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_articles.append(art)
        elif not url:
            unique_articles.append(art)

    # ── Save ────────────────────────────────────────────────────────────

    save_to_gcs({"news_feed": unique_articles})
    save_ai_cache(ai_cache)

    logging.info(
        "Scraper v2 done. articles=%d gemini=%d cache_hits=%d skipped_disabled=%d skipped_budget=%d model=%s",
        len(unique_articles), stats["gemini_calls"], stats["cache_hits"],
        stats["gemini_skipped_disabled"], stats["gemini_skipped_budget"], GEMINI_MODEL
    )

    return json.dumps({
        "status": "success",
        "articles_found": len(unique_articles),
        "raw_fetched": len(all_raw),
        "after_dedup": len(deduped),
        "gemini_calls": stats["gemini_calls"],
        "cache_hits": stats["cache_hits"],
        "gemini_skipped_disabled": stats["gemini_skipped_disabled"],
        "gemini_skipped_budget": stats["gemini_skipped_budget"],
        "gemini_model": GEMINI_MODEL,
    }), 200, {'Content-Type': 'application/json'}
