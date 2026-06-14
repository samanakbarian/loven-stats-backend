with open('api/main.py', 'rb') as f:
    content = f.read()

# Let's decode it safely
text = content.decode('utf-8', errors='replace')

replacements = {
    'Ã¶': 'ö',
    'Ã¤': 'ä',
    'Ã¥': 'å',
    'Ã\x96': 'Ö',
    'Ã\x84': 'Ä',
    'Ã\x85': 'Å',
    'ǟ\x9f': 'Ö',
    'ǟ': 'ö',
    'ǟ?z': 'ä',
    'ǟ?"': 'Ö',
    'ǟ?': 'å', # sometimes
    'ǟ?~': 'Å',
    '': 'ö',
    'ǽ??\'': '🏒',
}

# The corruption seems severe, let's just use regex to fix the common ones.
import re
text = text.replace('bjǟrklǟven', 'björklöven')
text = text.replace('Bjǟrklǟven', 'Björklöven')
text = text.replace('IF Bjrklven', 'IF Björklöven')
text = text.replace('Bjrklven', 'Björklöven')
text = text.replace('if bjrklven', 'if björklöven')

with open('api/main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Replaced known corruptions.")
