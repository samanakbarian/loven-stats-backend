import io
import re

text = io.open('c:/Users/saman/loven-stats-backend/api/main.py', encoding='utf-8').read()

# Add caches
cache_definitions = """analytics_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
stats_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
silly_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching
xfeed_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching
"""

text = re.sub(r'analytics_cache = TTLCache\(maxsize=10, ttl=21600\).*?\n', cache_definitions, text)

# Add @cached to get_statistics_snapshot
text = re.sub(
    r'@app\.get\("/api/v1/statistics"\)\ndef get_statistics_snapshot',
    r'@app.get("/api/v1/statistics")\n@cached(cache=stats_cache)\ndef get_statistics_snapshot',
    text
)

# Add @cached to get_silly_season
text = re.sub(
    r'@app\.get\("/api/silly-season"\)\ndef get_silly_season',
    r'@app.get("/api/silly-season")\n@cached(cache=silly_cache)\ndef get_silly_season',
    text
)

# Add @cached to get_silly_ops
text = re.sub(
    r'@app\.get\("/api/silly-season/ops"\)\ndef get_silly_ops',
    r'@app.get("/api/silly-season/ops")\n@cached(cache=silly_cache)\ndef get_silly_ops',
    text
)

# Add @cached to get_x_feed
# Wait, get_x_feed takes force_refresh. cachetools handles kwargs, but if force_refresh=True, it will cache under a different key.
text = re.sub(
    r'@app\.get\("/api/v1/x-feed"\)\ndef get_x_feed',
    r'@app.get("/api/v1/x-feed")\n@cached(cache=xfeed_cache)\ndef get_x_feed',
    text
)

io.open('c:/Users/saman/loven-stats-backend/api/main.py', 'w', encoding='utf-8', newline='').write(text)
print("Caches injected")
