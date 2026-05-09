import functions_framework
import requests
from bs4 import BeautifulSoup
import json
import logging
from datetime import datetime
import os
import hashlib
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

logging.basicConfig(level=logging.INFO)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
PROJECT_ID = "granskaren-d51a1"
LOCATION = "europe-west1" # Eller us-central1 om det strular
CACHE_BLOB_NAME = "raw/silly_season/article_ai_cache.json"
MAX_CACHE_ITEMS = 20000
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AI_DISABLED = os.environ.get("AI_DISABLED", "false").lower() == "true"
MAX_GEMINI_CALLS_PER_RUN = int(os.environ.get("MAX_GEMINI_CALLS_PER_RUN", "5"))
MAX_ITEMS_PER_SOURCE_DEFAULT = int(os.environ.get("MAX_ITEMS_PER_SOURCE_DEFAULT", "12"))
MAX_ITEMS_PER_SOURCE = {
    "bjorkloven.com": int(os.environ.get("MAX_ITEMS_BJORKLOVEN", str(MAX_ITEMS_PER_SOURCE_DEFAULT))),
    "MrMadhawk (Expressen)": int(os.environ.get("MAX_ITEMS_EXPRESSEN", "12")),
    "HockeySverige": int(os.environ.get("MAX_ITEMS_HOCKEYSVERIGE", str(MAX_ITEMS_PER_SOURCE_DEFAULT))),
    "HockeyNews": int(os.environ.get("MAX_ITEMS_HOCKEYNEWS", str(MAX_ITEMS_PER_SOURCE_DEFAULT))),
    "EliteProspects": int(os.environ.get("MAX_ITEMS_ELITEPROSPECTS", str(MAX_ITEMS_PER_SOURCE_DEFAULT))),
}

BJORKLOVEN_KEYWORDS = ['björklöven', 'bjorkloven', 'löven', 'björklövens', 'visionite arena', 'lövenbloggen']
STRICT_BJORKLOVEN_TOKENS = ['bjorkloven', 'björklöven', '/bjorkloven', '/björklöven']
TRANSFER_KEYWORDS = {
    'BEKRÄFTAT_NYFÖRVÄRV': ['nyförvärv', 'klar för', 'skrivit på', 'signerar', 'värvning', 'ansluter'],
    'BEKRÄFTAD_FÖRLUST': ['lämnar', 'tackar av', 'inte förlänger', 'klar för annan', 'säljer'],
    'KONTRAKTSFÖRLÄNGNING': ['förlänger', 'nytt kontrakt', 'skriver nytt'],
    'HETT_RYKTE': ['rykte', 'ryktas', 'intresse', 'uppges', 'spekuleras'],
}

def is_relevant(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in BJORKLOVEN_KEYWORDS)

def is_relevant_strict(title="", body="", link=""):
    haystack = f"{title} {body} {link}".lower()
    return any(token in haystack for token in STRICT_BJORKLOVEN_TOKENS)

def classify_tag(text):
    text_lower = text.lower()
    for tag, keywords in TRANSFER_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return tag
    return 'ÖVRIGT'

RUMOR_HINTS = ['rykte', 'ryktas', 'uppges', 'kopplas', 'intresse', 'jagas', 'kan värva', 'kan varva']
CONFIRMED_SIGNING_HINTS = ['klar för', 'klar for', 'signerar', 'skrivit på', 'skrivit pa', 'nyförvärv', 'nyforvarv', 'ansluter']
CONFIRMED_LOSS_HINTS = ['lämnar', 'lamnar', 'tackar av', 'inte förlänger', 'inte forlanger', 'klar för annan', 'klar for annan']
EXTENSION_HINTS = ['förlänger', 'forlanger', 'nytt kontrakt', 'skriver nytt']

def preclassify_without_ai(source, title="", body="", link=""):
    text = f"{title} {body}".lower()
    if not is_relevant_strict(title=title, body=body, link=link):
        return None
    if source == "bjorkloven.com":
        if any(k in text for k in CONFIRMED_SIGNING_HINTS):
            return "BEKRÄFTAT_NYFÖRVÄRV"
        if any(k in text for k in CONFIRMED_LOSS_HINTS):
            return "BEKRÄFTAD_FÖRLUST"
        if any(k in text for k in EXTENSION_HINTS):
            return "KONTRAKTSFÖRLÄNGNING"
    if any(k in text for k in RUMOR_HINTS):
        return "HETT_RYKTE"
    return None

def fetch_url(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Kunde inte hämta {url}: {e}")
        return None

def normalize_text(value):
    return (value or "").strip().lower()

def make_fingerprint(source, title, body, url):
    payload = "||".join([
        normalize_text(source),
        normalize_text(url),
        normalize_text(title),
        normalize_text(body),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def load_ai_cache():
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(CACHE_BLOB_NAME)
        if not blob.exists():
            return {}
        data = json.loads(blob.download_as_string())
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        logging.warning(f"Kunde inte läsa AI-cache: {e}")
        return {}

def save_ai_cache(cache):
    try:
        if len(cache) > MAX_CACHE_ITEMS:
            sorted_items = sorted(
                cache.items(),
                key=lambda kv: kv[1].get("updated_at", ""),
                reverse=True
            )[:MAX_CACHE_ITEMS]
            cache = dict(sorted_items)
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(CACHE_BLOB_NAME)
        blob.upload_from_string(json.dumps(cache, ensure_ascii=False), content_type='application/json')
    except Exception as e:
        logging.warning(f"Kunde inte spara AI-cache: {e}")

def analyze_with_gemini(text):
    """Använder Vertex AI (GenerativeModel) för att klassificera och analysera nyheter."""
    try:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        model = GenerativeModel(GEMINI_MODEL)
        
        prompt = f"""Analysera följande hockeynyhet/rykte med fokus på IF Björklöven:
"{text}"

Din uppgift är att avgöra om nyheten handlar om Björklövens LAGBYGGE (spelare in, ut, förlängningar, rykten om detta).
Exempel på vad som INTE är Björklövens lagbygge: En spelare går till ett annat lag men artikeln nämner att han "spelat i Björklöven tidigare", eller att ett annat lag värvar och Björklöven nämns i förbigående. Sådana ska taggas "ÖVRIGT".

Returnera ENBART ett giltigt JSON-objekt med följande nycklar:
"tag": Välj exakt EN av [BEKRÄFTAT_NYFÖRVÄRV, BEKRÄFTAD_FÖRLUST, KONTRAKTSFÖRLÄNGNING, HETT_RYKTE, ÖVRIGT]. Välj ÖVRIGT om det inte primärt handlar om en spelare till/från Björklöven.
"sentiment_pct": Siffra 0-100 för hur bra fansen tycker detta är (50 för neutralt/övrigt).
"pros": Lista med 1-2 korta strängar om fördelar (om tillämpligt).
"cons": Lista med 1-2 korta strängar om nackdelar (om tillämpligt).
"impact_type": "positive", "negative" eller null.
"impact_text": Kort text om påverkan, t.ex. "+25 poäng" eller "-18 min/match", eller null.
"""

        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        data = json.loads(response.text)
        # Ensure tag is valid
        if data.get("tag") not in TRANSFER_KEYWORDS.keys() and data.get("tag") != "ÖVRIGT":
            data["tag"] = "ÖVRIGT"
        return data
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return {"tag": "ÖVRIGT", "sentiment_pct": 50, "pros": ["Analys misslyckades"], "cons": [], "impact_type": None, "impact_text": None}

import concurrent.futures

def get_ai_analysis_cached(fingerprint, text, ai_cache, stats):
    cached = ai_cache.get(fingerprint)
    if cached and isinstance(cached, dict):
        stats["cache_hits"] += 1
        return cached.get("analysis", {"tag": "ÖVRIGT", "sentiment_pct": 50, "pros": [], "cons": [], "impact_type": None, "impact_text": None})
    stats["gemini_calls"] += 1
    analysis = analyze_with_gemini(text)
    ai_cache[fingerprint] = {
        "analysis": analysis,
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }
    return analysis

def get_ai_analysis_with_budget(fingerprint, text, ai_cache, stats):
    if AI_DISABLED:
        stats["gemini_skipped_disabled"] += 1
        return {"tag": "ÖVRIGT", "sentiment_pct": 50, "pros": [], "cons": [], "impact_type": None, "impact_text": None}
    if stats["gemini_calls"] >= MAX_GEMINI_CALLS_PER_RUN:
        stats["gemini_skipped_budget"] += 1
        return {"tag": "ÖVRIGT", "sentiment_pct": 50, "pros": [], "cons": [], "impact_type": None, "impact_text": None}
    return get_ai_analysis_cached(fingerprint, text, ai_cache, stats)

def get_ai_analysis_preferring_rumors(fingerprint, text, ai_cache, stats):
    text_lower = (text or "").lower()
    if any(k in text_lower for k in RUMOR_HINTS):
        stats["preclassified"] = stats.get("preclassified", 0) + 1
        return {"tag": "HETT_RYKTE", "sentiment_pct": 50, "pros": [], "cons": [], "impact_type": None, "impact_text": None}
    return get_ai_analysis_with_budget(fingerprint, text, ai_cache, stats)

def process_article(item, source, ai_cache, run_seen, stats):
    # Helper to process a single article to allow parallel processing
    if source == "bjorkloven.com":
        text = item['text']
        link = item['link']
        title = item['text']
        body = ""
    elif source == "MrMadhawk (Expressen)":
        title = item['title']
        body = item['body']
        link = item['link']
        text = title + " " + body
    elif source == "HockeySverige":
        title = item['title']
        body = ""
        link = item['link']
        text = title
    elif source == "HockeyNews":
        title = item['title']
        body = ""
        link = item['link']
        text = title
    elif source == "EliteProspects":
        title = item['title']
        body = item['body']
        link = item['link']
        text = body

    dedupe_key = f"{normalize_text(source)}::{normalize_text(link)}::{normalize_text(title)}"
    if dedupe_key in run_seen:
        return None
    run_seen.add(dedupe_key)

    fingerprint = make_fingerprint(source, title, body, link)
    ai_data = get_ai_analysis_preferring_rumors(fingerprint, text, ai_cache, stats)
    tag = ai_data.get("tag", "ÖVRIGT")
    
    impact = None
    if tag != "HETT_RYKTE" and tag != "ÖVRIGT" and ai_data.get("impact_type"):
         impact = {
             "type": ai_data.get("impact_type"),
             "impact_toi": ai_data.get("impact_text"),
             "impact_points": ai_data.get("impact_text")
         }
         
    return {
        "title": title,
        "body": body[:200] if body else "",
        "source": source,
        "url": link,
        "date": datetime.now().isoformat(),
        "tag": tag,
        "ai_analysis": ai_data if tag == "HETT_RYKTE" else None,
        "impact": impact
    }

def scrape_bjorkloven_official():
    url = 'https://www.bjorkloven.com/nyheter'
    html = fetch_url(url)
    items_to_process = []
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    for item in soup.select('article, .news-item, a'):
        title_el = item.select_one('h2, h3, .title')
        text = title_el.get_text(strip=True) if title_el else item.get_text(strip=True)
        link = item.get('href', '') if not title_el else (item.find('a').get('href', '') if item.find('a') else '')
        if len(text) > 20 and is_relevant(text):
            full_link = f"https://www.bjorkloven.com{link}" if link and not link.startswith('http') else link
            items_to_process.append({"text": text, "link": full_link})
            
    return items_to_process

def scrape_mrmadhawk():
    url = 'https://www.expressen.se/sok/?q=Björklöven'
    html = fetch_url(url)
    items_to_process = []
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    for item in soup.select('a.list-page__item__link'):
        title_el = item.select_one('h2')
        if not title_el: continue
        title = title_el.get_text(strip=True)
        body = item.select_one('p').get_text(strip=True) if item.select_one('p') else ""
        link = item.get('href', '')
        if is_relevant(title + " " + body):
            full_link = f"https://www.expressen.se{link}" if not link.startswith('http') else link
            items_to_process.append({"title": title, "body": body, "link": full_link})
            
    return items_to_process

def scrape_hockeysverige():
    url = 'https://hockeysverige.se/senaste-nytt/'
    html = fetch_url(url)
    items_to_process = []
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    raw_candidates = 0
    for item in soup.select('article, .post, .entry'):
        title_el = item.select_one('h2, h3, a.entry-title')
        if not title_el: continue
        title = title_el.get_text(strip=True)
        link = title_el.get('href', '') if title_el.name == 'a' else (title_el.find('a').get('href', '') if title_el.find('a') else "")
        raw_candidates += 1
        if len(title) > 10 and is_relevant_strict(title=title, link=link):
            items_to_process.append({"title": title, "link": link})
    logging.info("HockeySverige parsed=%s accepted=%s", raw_candidates, len(items_to_process))
    return items_to_process

def scrape_hockeynews():
    url = 'https://www.hockeynews.se/'
    html = fetch_url(url)
    items_to_process = []
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    raw_candidates = 0
    for item in soup.select('article, .post, .entry'):
        title_el = item.select_one('h2, h3, a, .entry-title, [class*=\"title\"]')
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 10:
            continue
        link = title_el.get('href', '') if title_el.name == 'a' else (title_el.find('a').get('href', '') if title_el.find('a') else "")
        raw_candidates += 1
        if is_relevant_strict(title=title, link=link):
            items_to_process.append({"title": title, "link": link})
    logging.info("HockeyNews parsed=%s accepted=%s", raw_candidates, len(items_to_process))
    return items_to_process

def scrape_eliteprospects():
    url = 'https://www.eliteprospects.com/transfers'
    html = fetch_url(url)
    items_to_process = []
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    for row in soup.select('div[class*="TransactionsTable_row"]'):
        text = row.get_text(strip=True)
        if is_relevant(text):
            items_to_process.append({
                "title": f"EP TRANSFER: {text[:50]}...", 
                "body": text, 
                "link": "https://www.eliteprospects.com/transfers"
            })
            
    return items_to_process

def save_to_gcs(data):
    file_name = f"raw/silly_season/scraped_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(file_name)
        blob.upload_from_string(json.dumps(data, ensure_ascii=False), content_type='application/json')
    except Exception as e:
        logging.error(f"GCS error: {e}")

@functions_framework.http
def run_scraper(request):
    logging.info("Startar Silly Season Scraper (VertexAI SDK, Parallellt)...")
    ai_cache = load_ai_cache()
    run_seen = set()
    stats = {
        "gemini_calls": 0,
        "cache_hits": 0,
        "gemini_skipped_disabled": 0,
        "gemini_skipped_budget": 0,
        "preclassified": 0
    }
    all_articles = []
    bjorkloven_items = scrape_bjorkloven_official()
    mrmadhawk_items = scrape_mrmadhawk()
    hockeysverige_items = scrape_hockeysverige()
    hockeynews_items = scrape_hockeynews()
    eliteprospects_items = scrape_eliteprospects()
    logging.info(
        "Scrape candidates per source: bjorkloven=%s expressen=%s hockeysverige=%s hockeynews=%s eliteprospects=%s",
        len(bjorkloven_items),
        len(mrmadhawk_items),
        len(hockeysverige_items),
        len(hockeynews_items),
        len(eliteprospects_items),
    )

    def process_source(items, source_name, executor):
        processed = [a for a in executor.map(lambda i: process_article(i, source_name, ai_cache, run_seen, stats), items) if a]
        limit = MAX_ITEMS_PER_SOURCE.get(source_name, MAX_ITEMS_PER_SOURCE_DEFAULT)
        capped = processed[:limit]
        logging.info("Source %s accepted=%s capped=%s limit=%s", source_name, len(processed), len(capped), limit)
        return capped

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        # Keep source order deterministic but cap each source so new hits from other sources surface.
        all_articles.extend(process_source(bjorkloven_items, "bjorkloven.com", executor))
        all_articles.extend(process_source(mrmadhawk_items, "MrMadhawk (Expressen)", executor))
        all_articles.extend(process_source(hockeysverige_items, "HockeySverige", executor))
        all_articles.extend(process_source(hockeynews_items, "HockeyNews", executor))
        all_articles.extend(process_source(eliteprospects_items, "EliteProspects", executor))

    unique_articles = {a['url']: a for a in all_articles if a.get('url')}.values()
    save_to_gcs({"news_feed": list(unique_articles)})
    save_ai_cache(ai_cache)
    logging.info(
        f"Silly scraper klar. Articles={len(unique_articles)} "
        f"Gemini calls={stats['gemini_calls']} cache hits={stats['cache_hits']} "
        f"skipped_disabled={stats['gemini_skipped_disabled']} "
        f"skipped_budget={stats['gemini_skipped_budget']} "
        f"preclassified={stats['preclassified']} model={GEMINI_MODEL}"
    )
    return json.dumps({
        "status": "success",
        "articles_found": len(unique_articles),
        "gemini_calls": stats["gemini_calls"],
        "cache_hits": stats["cache_hits"],
        "gemini_skipped_disabled": stats["gemini_skipped_disabled"],
        "gemini_skipped_budget": stats["gemini_skipped_budget"],
        "preclassified": stats["preclassified"],
        "gemini_model": GEMINI_MODEL
    }), 200, {'Content-Type': 'application/json'}
