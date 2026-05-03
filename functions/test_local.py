import main
from unittest.mock import Mock
import json

# Vi skapar en låtsas-HTTP-request (mock) för att testa vår funktion lokalt
req = Mock()
req.get_json.return_value = None

# OBS: Detta är ett påhittat match-ID, så du kommer med största sannolikhet 
# att få ett "404 Not Found" från Sportradar om matchen inte finns.
# Byt ut detta mot ett riktigt match-ID när du har hittat ett!
match_id = 'sr:sport_event:41234567' 
req.args = {'match_id': match_id}

print(f"Testar att anropa Sportradar API för match {match_id}...\n")

try:
    # Anropa vår Cloud Function lokalt
    response, status_code, headers = main.fetch_sportradar_data(req)

    print(f"Status Code: {status_code}")
    print(f"Svar: {json.loads(response)}")
    print("\nOm allt gick bra bör du nu se en ny .json fil i den här mappen!")

except Exception as e:
    print(f"Något gick snett: {e}")
