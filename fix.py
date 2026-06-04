import io
import re

text = io.open('c:/Users/saman/loven-stats-backend/api/main.py', encoding='utf-8').read()

new_text = re.sub(
    r'"note": s\.get\("note"\) or "",\n        \}\)',
    r'"note": s.get("note") or "",\n            "age": s.get("age"),\n        })',
    text
)

if new_text != text:
    io.open('c:/Users/saman/loven-stats-backend/api/main.py', 'w', encoding='utf-8', newline='').write(new_text)
    print("Replaced note with age")
else:
    print("Still failed to replace note")
