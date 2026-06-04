import io

text = io.open('c:/Users/saman/loven-stats-backend/api/main.py', encoding='utf-8').read()

cache_defs = """analytics_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
stats_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
silly_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching
xfeed_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching
"""

text = text.replace(cache_defs, "")

# Insert right after app = FastAPI(...) definition block
insert_marker = 'version="1.0.0"\n)\n\n'
text = text.replace(insert_marker, insert_marker + cache_defs + '\n')

io.open('c:/Users/saman/loven-stats-backend/api/main.py', 'w', encoding='utf-8', newline='').write(text)
print("Moved cache definitions")
