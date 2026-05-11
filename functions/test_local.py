"""
Local test for the rewritten silly scraper v2.

Tests:
1. Google News RSS fetching works
2. Mustonen förlängning is caught and classified correctly
3. Classification logic works for known articles
"""

import sys
import json

# Patch functions_framework and GCS for local testing
class FakeModule:
    @staticmethod
    def http(fn):
        return fn

sys.modules['functions_framework'] = FakeModule()

# Mock google.cloud.storage
class FakeBucket:
    def blob(self, name): return FakeBlob()

class FakeBlob:
    def exists(self): return False
    def upload_from_string(self, *a, **kw): pass
    def download_as_string(self): return b'{}'

class FakeStorageClient:
    def bucket(self, name): return FakeBucket()

class FakeStorage:
    Client = FakeStorageClient

import types
gcs_mod = types.ModuleType('google.cloud.storage')
gcs_mod.Client = FakeStorageClient
sys.modules['google.cloud'] = types.ModuleType('google.cloud')
sys.modules['google.cloud.storage'] = gcs_mod
sys.modules['google.cloud'].storage = gcs_mod

# Mock vertexai
sys.modules['vertexai'] = types.ModuleType('vertexai')
sys.modules['vertexai'].init = lambda **kw: None
sys.modules['vertexai.generative_models'] = types.ModuleType('vertexai.generative_models')
sys.modules['vertexai.generative_models'].GenerativeModel = None
sys.modules['vertexai.generative_models'].GenerationConfig = None

# Set AI_DISABLED so we don't call Gemini
import os
os.environ['AI_DISABLED'] = 'true'

# NOW import the scraper
from silly_scraper import (
    classify_article, has_bjorkloven_context, is_transfer_relevant,
    fetch_google_news_rss, deduplicate_articles, process_articles, normalize_title
)

print("=" * 60)
print("TEST 1: Classification of known articles")
print("=" * 60)

test_cases = [
    ("Joel Mustonen förlänger med Björklöven", "", "Björklöven", "KONTRAKTSFÖRLÄNGNING"),
    ("Klart: 33-åringen förlänger med Björklöven", "", "Folkbladet", "KONTRAKTSFÖRLÄNGNING"),
    ("Topi Niemelä klar för Björklöven", "", "Björklöven", "BEKRÄFTAT_NYFÖRVÄRV"),
    ("Björklöven värvar från ny konkurrent", "", "Expressen", "BEKRÄFTAT_NYFÖRVÄRV"),
    ("Nio spelare lämnar Björklöven", "", "Björklöven", "BEKRÄFTAD_FÖRLUST"),
    ("Lämnade Björklöven – klar för ny SHL-klubb", "", "Expressen", "BEKRÄFTAD_FÖRLUST"),
    ("Uppgifter: Björklöven landar drömvärvning", "", "VK", "HETT_RYKTE"),
    ("Gustaf Kangas förlänger med Björklöven", "", "Björklöven", "KONTRAKTSFÖRLÄNGNING"),
    ("Wallmark klar för Björklöven", "", "SR", "BEKRÄFTAT_NYFÖRVÄRV"),
    ("Oscar Tellström förlänger", "Oscar Tellström förlänger kontraktet med Björklöven", "Björklöven", "KONTRAKTSFÖRLÄNGNING"),
    # This should NOT match — not about Björklöven transfers
    ("Björklöven förlänger segersviten", "", "SVT", None),
]

passed = 0
failed = 0
for title, body, source, expected in test_cases:
    tag, confidence = classify_article(title, body, source)
    status = "✅" if tag == expected else "❌"
    if tag == expected:
        passed += 1
    else:
        failed += 1
    print(f"  {status} '{title[:50]}...' → {tag} (expected: {expected})")

print(f"\nResults: {passed} passed, {failed} failed\n")

print("=" * 60)
print("TEST 2: Google News RSS fetch")
print("=" * 60)

articles = fetch_google_news_rss(
    '"Björklöven" (förlänger OR klar för OR lämnar OR nyförvärv)',
    label="test"
)
print(f"  Fetched {len(articles)} articles from Google News RSS")

# Check if Mustonen is in there
mustonen_found = any("mustonen" in a["title"].lower() for a in articles)
print(f"  Mustonen förlängning found: {'✅ YES' if mustonen_found else '❌ NO'}")

print()
print("=" * 60)
print("TEST 3: Deduplication")
print("=" * 60)

deduped = deduplicate_articles(articles)
print(f"  Before dedup: {len(articles)}, after: {len(deduped)}")
print(f"  Removed {len(articles) - len(deduped)} duplicates")

print()
print("=" * 60)
print("TEST 4: Full pipeline (AI disabled)")
print("=" * 60)

stats = {
    "gemini_calls": 0,
    "cache_hits": 0,
    "gemini_skipped_disabled": 0,
    "gemini_skipped_budget": 0,
}

results = process_articles(deduped, {}, stats)
print(f"  Classified {len(results)} articles")

# Group by tag
tags = {}
for r in results:
    tag = r["tag"]
    tags[tag] = tags.get(tag, 0) + 1

print(f"  Tags: {json.dumps(tags, ensure_ascii=False)}")
print()

# Print the classified articles
for r in results[:15]:
    print(f"  [{r['tag'][:6]:>6}] {r['title'][:70]}  ({r['source']})")

mustonen_in_results = any("mustonen" in r["title"].lower() for r in results)
print(f"\n  Mustonen in final results: {'✅ YES' if mustonen_in_results else '❌ NO'}")

print()
if failed == 0 and mustonen_in_results:
    print("🎉 ALL TESTS PASSED!")
else:
    print(f"⚠️  {failed} classification tests failed, mustonen_in_results={mustonen_in_results}")
