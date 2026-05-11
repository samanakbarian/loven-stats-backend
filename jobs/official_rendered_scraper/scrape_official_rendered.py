import json
import os
from datetime import datetime, timezone

from google.cloud import storage
from playwright.sync_api import sync_playwright

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
OUTPUT_BLOB = os.environ.get(
    "OFFICIAL_RENDERED_BLOB_NAME",
    "raw/silly_season/official_rendered_latest.json",
)
TARGET_URL = os.environ.get("OFFICIAL_NEWS_URL", "https://www.bjorkloven.com/nyheter")

KEYWORDS = [
    "förlänger", "forlanger", "förlängde", "forlangde",
    "klar för", "klar for", "nyförvärv", "nyforvarv",
    "lämnar", "lamnar", "kontrakt", "utlåning", "utlaning",
]


def is_relevant(title: str, link: str) -> bool:
    hay = f"{title} {link}".lower()
    return any(k in hay for k in KEYWORDS)


def scrape() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        anchors = page.locator("a")
        count = anchors.count()
        items = []
        seen = set()
        for i in range(count):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            title = (a.inner_text() or "").strip()
            if not href or len(title) < 8:
                continue
            if href.startswith("/"):
                href = f"https://www.bjorkloven.com{href}"
            if "bjorkloven.com" not in href:
                continue
            if not is_relevant(title, href):
                continue
            key = f"{title.lower()}::{href.lower()}"
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "body": "",
                "link": href,
                "source": "OfficialRendered (Bjorkloven)",
                "date": datetime.now(timezone.utc).isoformat(),
            })
        browser.close()
        return items


def upload(items: list[dict]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "news_feed": items,
    }
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(OUTPUT_BLOB)
    blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")


if __name__ == "__main__":
    data = scrape()
    upload(data)
    print(json.dumps({"status": "ok", "items": len(data)}))
