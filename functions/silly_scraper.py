"""
Silly Season Scraper v2 — Björklöven transfer news aggregator.

Runs as a Google Cloud Function. Collects transfer-related news from:
  1. Google News RSS (primary — catches all media + official bjorkloven.com)
  2. EliteProspects transfers page (secondary — confirmed transactions)

Classifies articles as:
  KONTRAKTSFÖRLÄNGNING, BEKRÄFTAT_NYFÖRVÄRV, BEKRÄFTAD_FÖRLUST, HETT_RYKTE

Saves results to GCS as JSON for frontend consumption.
"""

from typing import Any

import functions_framework
import requests
from bs4 import BeautifulSoup
import json
import logging
import re
import hashlib
from datetime import datetime, timedelta, timezone
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

# ─── Nyhetsflödet ────────────────────────────────────────────────────────────
#
# Silly season-logiken ovanför söker på övergångsverb — "förlänger", "klar
# för", "nyförvärv" — och klassificeraren kastar allt som inte är en övergång.
# Det fungerar i juni. I september, mitt i seriestarten, ger samma sökning noll
# träffar, och nyhetssidan föll tillbaka på en handunderhållen baseline från
# juni: 86 dagar gammal när det upptäcktes.
#
# Det här flödet är motsatsen. Det söker brett på laget, behåller allt som är
# relevant, och märker upp det i stället för att slänga. Ingen LLM behövs —
# rubriken räcker för att skilja en match från en värvning, och det som inte
# går att avgöra hamnar under "klubb" i stället för att försvinna.

NEWS_BLOB = os.environ.get("NEWS_BLOB_NAME", "raw/news/feed_latest.json")

# Laget i rubriken. Bara att nämnas i brödtexten räcker inte — Google News
# returnerar en fjärdedel artiklar som handlar om någon annan och råkar nämna
# Björklöven på slutet.
_LAGET = re.compile(r"bj[oö]rkl[oö]ven|\bl[oö]ven\b", re.IGNORECASE)

# Ungdoms- och damlag prövas först, annars taggas en U18-match som "match" och
# hamnar bland A-lagets rader.
_UNGDOM = re.compile(r"\bU1[5-9]\b|\bU2[01]\b|\bJ1[89]\b|\bJ20\b|\bdam(er|lag)?\b", re.IGNORECASE)
_TRUPP = re.compile(
    r"f[oö]rl[aä]ng|klar f[oö]r|l[aä]mnar|nyf[oö]rv[aä]rv|kontrakt|v[aä]rvar|ansluter|"
    r"skada|skadad|l[aå]nas|[aå]terv[aä]nder|tr[aä]nare|sportchef|intresse fr[aå]n|provspel|"
    r"\bklart[:!]|officiellt|[oö]verens med|till bj[oö]rkl[oö]ven|till l[oö]ven|"
    r"sl[aä]pper.{0,12}spelare|lagkapten|v[aä]nder hem",
    re.IGNORECASE,
)
_MATCH = re.compile(
    r"seger|f[oö]rlust|besegra|vinst|kross|m[aå]l|straffar|match|premi[aä]r|po[aä]ng|"
    r"derby|oavgjort|chansl[oö]s|f[oö]rl[aä]ngning|period|lineup|uppst[aä]llning|"
    r"\bvann\b|\bf[oö]ll\b|\bslog\b|straffl[aä]gg|powerplay|h[oö]jdpunkter|"
    r"p[aå] tv\b|s[aä]nder|stream|\bslut:|debuter|skr[aä]ll|avgjorde|\bm[oö]t(er|s|te)\b",
    re.IGNORECASE,
)

# Rena mötesrubriker bär inget verb alls: "Brynäs IF - IF Björklöven". De känns
# igen på att båda sidor om strecket ser ut som lagnamn.
_STRECK = re.compile(r"\s[-–]\s|(?<=\w)–(?=\w)")


def _ar_mote(titel: str) -> bool:
    delar = _STRECK.split(titel, maxsplit=1)
    if len(delar) != 2:
        return False
    return all(d and _LAGFORM.search(d) for d in delar)


def _nyhetstagg(titel: str) -> str:
    """Vad raden handlar om. Ordningen avgör: ungdom före allt annat."""
    if _UNGDOM.search(titel):
        return "ungdom"
    if _TRUPP.search(titel):
        return "trupp"
    if _MATCH.search(titel) or _ar_mote(titel):
        return "match"
    return "klubb"


def _publicerad(text: str) -> str | None:
    """RFC 822 till ISO. Google News skriver 'Sun, 06 Sep 2026 12:00:00 GMT'."""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


# "IF", "AIK", "Luleå Hockey" — det som gör en ordföljd till ett lagnamn.
_LAGFORM = re.compile(r"\b(if|ik|hc|bk|sk|hk|hf|is|aik|hockey)\b", re.IGNORECASE)


def _utan_kalla(titel: str, kalla: str) -> str:
    """Google News hänger på ' - Källa' i rubriken. Källan visas separat.

    Alla 495 hämtade rubriker slutade med " - <source>", så suffixet stryks –
    men inte blint. En matchrubrik är själva mötet: "LINEUP: IF Björklöven -
    Skellefteå AIK", publicerad av Skellefteå AIK. Där är suffixet halva
    rubriken. Bindestrecket läses som ett möte bara när båda sidor ser ut som
    lagnamn; "Uddamålsseger mot Björklöven - Luleå Hockey" har inget lagnamn
    till vänster och kortas som vanligt.
    """
    if not kalla or not titel.endswith(f" - {kalla}"):
        return titel.strip()
    kvar = titel[: -len(kalla) - 3].strip()
    if _LAGFORM.search(kalla) and len(kalla.split()) <= 4 and _LAGFORM.search(kvar):
        return titel.strip()
    return kvar


# Fem frågor i stället för en. Mätt mot verkligt utfall 2026-09-07 gav den
# breda frågan 78 träffar; de fyra övriga la till 92, 64, 22 och 48 unika rader
# ovanpå det — 304 totalt. Google News kapar varje svar vid 100, så bredden
# kommer av flera frågor, inte av en bättre formulerad.
NYHETSFRAGOR = (
    ('"Björklöven"', "news_bred"),
    ("site:bjorkloven.com", "news_officiell"),
    ('"IF Björklöven" hockey', "news_hockey"),
    ("Björklöven SHL", "news_shl"),
    ("Björklöven Umeå hockey", "news_lokalt"),
)

# Av de 304 låg 65 inom en månad och 189 var äldre än ett kvartal — mest
# klubbsidans arkiv. Ett halvår tillbaka håller flödet aktuellt utan att gå
# tomt under uppehållet mellan säsongerna.
NYHET_MAX_DAGAR = 180
NYHET_MAX_RADER = 150


def bygg_nyhetsflode() -> list[dict[str, Any]]:
    """Aktuella nyheter om laget, brett hämtade och uppmärkta."""
    raw: list[dict[str, Any]] = []
    for fraga, etikett in NYHETSFRAGOR:
        raw += fetch_google_news_rss(fraga, label=etikett)

    grans = datetime.now(timezone.utc) - timedelta(days=NYHET_MAX_DAGAR)
    sedda_url: set[str] = set()
    sedda_titel: set[str] = set()
    out: list[dict[str, Any]] = []

    for art in raw:
        titel_rå = art.get("title") or ""
        if not _LAGET.search(titel_rå):
            continue
        url = art.get("link") or ""
        kalla = art.get("source_name") or ""
        titel = _utan_kalla(titel_rå, kalla)
        # Samma händelse kommer ofta från flera källor med snarlik rubrik.
        nyckel = re.sub(r"[^a-z0-9]+", "", titel.lower())[:60]
        if (url and url in sedda_url) or (nyckel and nyckel in sedda_titel):
            continue

        publicerad = _publicerad(art.get("pub_date") or "")
        # Utan datum går raden inte att placera i ett flöde som sorteras på tid.
        if not publicerad:
            continue
        try:
            if datetime.fromisoformat(publicerad) < grans:
                continue
        except ValueError:
            continue

        sedda_url.add(url)
        sedda_titel.add(nyckel)

        out.append({
            # Stabilt id över körningar, så klienten kan minnas vad som lästs.
            "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
            "type": "press",
            "ts": publicerad,
            "title": titel,
            "tag": _nyhetstagg(titel),
            "source": kalla,
            # Klubbens egen sida väger tyngst och markeras.
            "official": "bjorkloven.com" in url.lower() or kalla.lower() == "björklöven",
            "url": url,
        })

    out.sort(key=lambda a: a.get("ts") or "", reverse=True)
    out = out[:NYHET_MAX_RADER]
    logging.info("Nyhetsflode: %d rader av %d raa", len(out), len(raw))
    return out


def las_nyheter() -> dict[str, Any]:
    """Det som redan ligger i bloben. Tom dict om den saknas eller är trasig."""
    try:
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET_NAME).blob(NEWS_BLOB)
        if not blob.exists():
            return {}
        return json.loads(blob.download_as_text()) or {}
    except Exception:
        logging.exception("Kunde inte lasa det befintliga nyhetsflodet")
        return {}


def sla_ihop_nyheter(gamla: list[dict[str, Any]], nya: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union på artikel-id, nyast först, inom tidsfönstret.

    Google News kapar varje svar vid 100 och strypar den som frågar för ofta.
    En körning som får noll träffar är därför normal drift, inte ett fel — men
    den får inte radera det som redan hämtats. Unionen gör skörden additiv:
    en blockerad körning kostar ingenting, och flödet återhämtar sig av sig
    självt vid nästa lyckade.
    """
    per_id: dict[str, dict[str, Any]] = {a["id"]: a for a in gamla if a.get("id")}
    # Nya raden vinner: uppmärkningen kan ha förbättrats sedan förra körningen.
    per_id.update({a["id"]: a for a in nya if a.get("id")})

    grans = (datetime.now(timezone.utc) - timedelta(days=NYHET_MAX_DAGAR)).isoformat()
    kvar = [a for a in per_id.values() if (a.get("ts") or "") >= grans]
    kvar.sort(key=lambda a: a.get("ts") or "", reverse=True)
    return kvar[:NYHET_MAX_RADER]


# ── Klipp ────────────────────────────────────────────────────────────────────

# Klubbens egen kanal star pa bjorkloven.com. Den som ligger pa
# youtube.com/c/BjorklovenOfficiell heter i sjalva verket "Bjorkloven
# Inofficiell" och ar ett fanprojekt — den tas med, men markt som inofficiell
# sa att lasaren kan avgora sjalv.
YOUTUBE_KANALER = (
    ("UCFKSt_ESvC9VGWzxne3MI9Q", "Björklöven", True),
    ("UCFDPDW1nPNPOpkYSyiFygjQ", "Björklöven Inofficiell", False),
)

_ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "m": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def hamta_klipp() -> list[dict[str, Any]]:
    """Videor ur YouTubes RSS-flöden. Ingen API-nyckel, ingen kvot.

    Data API:t hade krävt nyckel, kvot och en hemlighet till att förvalta.
    Atom-flödet ger de femton senaste med titel, datum, video-id och miniatyr,
    vilket är precis vad ett flöde behöver.

    Titeln filtreras inte mot lagnamnet som nyheterna gör. Ett klipp som heter
    "Frasses vits - #2" handlar om Björklöven i kraft av var det ligger, inte
    vad det heter.
    """
    import xml.etree.ElementTree as ET

    ut: list[dict[str, Any]] = []
    for kanal_id, kalla, officiell in YOUTUBE_KANALER:
        xml = fetch_url(f"https://www.youtube.com/feeds/videos.xml?channel_id={kanal_id}")
        if not xml:
            logging.warning("Inget YouTube-flode for %s", kalla)
            continue
        try:
            rot = ET.fromstring(xml)
        except ET.ParseError:
            logging.exception("Kunde inte tolka YouTube-flodet for %s", kalla)
            continue

        for post in rot.findall("a:entry", _ATOM):
            vid = post.findtext("yt:videoId", namespaces=_ATOM)
            titel = (post.findtext("a:title", namespaces=_ATOM) or "").strip()
            publicerad = post.findtext("a:published", namespaces=_ATOM)
            if not (vid and titel and publicerad):
                continue
            grupp = post.find("m:group", _ATOM)
            bild = grupp.find("m:thumbnail", _ATOM) if grupp is not None else None
            ut.append({
                "id": f"yt-{vid}",
                "type": "video",
                "ts": publicerad,
                "title": titel,
                # Egen tagg: man filtrerar pa medium har, inte pa amne. "Visa
                # mig klippen" ar en fraga man faktiskt staller.
                "tag": "klipp",
                "source": kalla,
                "official": officiell,
                "url": f"https://www.youtube.com/watch?v={vid}",
                # RSS pekar pa i1-i4.ytimg.com i tur och ordning. i.ytimg.com
                # ar den kanoniska varden och blir densamma for alla rader,
                # vilket ger battre cachetraffar och farre varden att lita pa.
                "thumbnail": (re.sub(r"//i\d+\.ytimg\.com", "//i.ytimg.com", bild.get("url"))
                              if bild is not None and bild.get("url") else None),
            })

    # Fankanalen lagger upp samma intervju flera ganger — som teaser, i delar
    # och i sin helhet. Tre rader om Gote Walitalo i rad ar inte ett flode.
    # Nyaste vinner, aldre snarlika titlar faller bort.
    ut.sort(key=lambda a: a["ts"], reverse=True)
    sedda: set[str] = set()
    unika = []
    for k in ut:
        nyckel = re.sub(r"[^a-z0-9]+", "", k["title"].lower())[:50]
        if nyckel and nyckel in sedda:
            continue
        sedda.add(nyckel)
        unika.append(k)

    logging.info("Klipp: %d rader (%d fore avduplicering) fran %d kanaler",
                 len(unika), len(ut), len(YOUTUBE_KANALER))
    return unika


def spara_nyheter(items: list[dict[str, Any]]) -> None:
    """En blob som skrivs över. Historiken ligger i BigQuery, inte här."""
    if not items:
        # Skulle aldrig hända efter sammanslagningen, men en tom blob är det
        # enda som kan tomma nyhetssidan — så den skrivs inte.
        logging.warning("Inga nyheter att spara; behaller befintlig blob")
        return
    try:
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET_NAME).blob(NEWS_BLOB)
        blob.upload_from_string(
            json.dumps(
                {"updated_at": datetime.now(timezone.utc).isoformat(), "items": items},
                ensure_ascii=False,
            ),
            content_type="application/json",
        )
        logging.info("Sparade %d nyheter till gs://%s/%s", len(items), GCS_BUCKET_NAME, NEWS_BLOB)
    except Exception:
        logging.exception("Kunde inte spara nyhetsflodet")


NEWS_BQ_DATASET = os.environ.get("NEWS_BQ_DATASET", "raw_sports")
NEWS_BQ_TABLE = os.environ.get("NEWS_BQ_TABLE", "news_articles")


def landa_nyheter_i_bq(items: list[dict[str, Any]]) -> None:
    """Samma rader till raw_sports, append-only som allt annat.

    GCS-bloben skrivs över vid varje körning och är därmed ett ögonblick, inte
    en historik. Utan tabell finns det inget att koppla en nyhet till en spelare
    eller en match mot (backlogg 21 och 22), och ingen väg att svara på när en
    uppgift först dök upp. Serveringen läser fortfarande bloben — den här vägen
    är för analysen, så ett BigQuery-fel får inte fälla skörningen.
    """
    if not items:
        return
    try:
        from google.cloud import bigquery

        bq = bigquery.Client(project=PROJECT_ID)
        tabell = f"{PROJECT_ID}.{NEWS_BQ_DATASET}.{NEWS_BQ_TABLE}"
        schema = [
            bigquery.SchemaField("article_id", "STRING"),
            bigquery.SchemaField("published_at", "TIMESTAMP"),
            bigquery.SchemaField("title", "STRING"),
            bigquery.SchemaField("tag", "STRING"),
            # Utgivaren, inte scraperkällan. `source` betyder "swehockey" i de
            # andra raw-tabellerna och får inte betyda två saker.
            bigquery.SchemaField("publisher", "STRING"),
            bigquery.SchemaField("official", "BOOL"),
            bigquery.SchemaField("url", "STRING"),
            bigquery.SchemaField("scraped_at", "TIMESTAMP"),
        ]
        bq.create_table(bigquery.Table(tabell, schema=schema), exists_ok=True)

        skordat = datetime.now(timezone.utc).isoformat()
        rader = [{
            "article_id": a.get("id"),
            "published_at": a.get("ts"),
            "title": a.get("title"),
            "tag": a.get("tag"),
            "publisher": a.get("source"),
            "official": bool(a.get("official")),
            "url": a.get("url"),
            "scraped_at": skordat,
        } for a in items]

        fel = bq.insert_rows_json(tabell, rader)
        if fel:
            logging.error("BigQuery avvisade nyhetsrader: %s", fel[:3])
        else:
            logging.info("Landade %d nyheter i %s", len(rader), tabell)
    except Exception:
        logging.exception("Kunde inte landa nyhetsflodet i BigQuery")


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

    # Nyhetsflodet ar oberoende av silly season-logiken och far inte kunna
    # falla med den, at nagot hall.
    try:
        befintligt = las_nyheter()
        gamla = befintligt.get("items") or []
        # Skorden gar mot fem Google News-fragor. Var halvtimme blir 240 anrop
        # om dygnet, och det ar sa flodet blev strypt och tomt kl 09:01 den
        # 7 september. En timme mellan skordarna racker gott for nyheter och
        # halverar trycket; mellanliggande korningar later bloben vara.
        senast = befintligt.get("updated_at") or ""
        farsk = senast >= (datetime.now(timezone.utc) - timedelta(minutes=55)).isoformat()
        if farsk and gamla:
            logging.info("Nyhetsflodet skordades %s; hoppar over", senast)
        else:
            nya = bygg_nyhetsflode() + hamta_klipp()
            if not nya:
                logging.warning("Nyhetsskorden gav noll rader; behaller %d befintliga", len(gamla))
            else:
                sammanslaget = sla_ihop_nyheter(gamla, nya)
                logging.info("Nyhetsflode: %d nya, %d befintliga, %d efter sammanslagning",
                             len(nya), len(gamla), len(sammanslaget))
                spara_nyheter(sammanslaget)
                landa_nyheter_i_bq(nya)
    except Exception:
        logging.exception("Nyhetsflodet misslyckades; silly season ar oberort")

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
