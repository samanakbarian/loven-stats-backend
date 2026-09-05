# Nästa steg

Uppdaterad 2026-09-05.

## Var vi står

Frontend och backend är båda live. Inget väntar på deploy.

Allt är pushat: `slutspel` på `main` (Netlify bygger automatiskt),
`loven-stats-backend` på `master` (deployas manuellt).

### Klart och ute

- **Statistiken ombyggd till tre ytor** — Laget, Spelare, Utveckling. De
  gamla fem underflikarna är borta, inklusive dubbletten av Matcher och den
  nästlade analysmodulen.
- **Spelarprofiler** på `/statistik/spelare/:namn` med percentil mot serien,
  poängkurva och matcherna med poäng, där varje match länkar till
  matchrapporten.
- **Huvudbundlen 697 kB → 295 kB** (206 → 90 kB gzippat). Recharts används
  bara av analysmodulen och Ekonomi, som nu laddas lazy.
- **Diagrammen** i `frontend_v2/src/components/charts/` är handritad SVG mot
  temats tokens — inget diagrambibliotek i huvudvägen.

### Klart och deployat 2026-09-05

Revision `loven-stats-api-00099-jz4`. Verifierat mot skarpt API:

- `/api/v1/players` — 33 spelare, percentil mot 395 av seriens 509 spelare.
- `/api/v1/player/{namn}` — Ottosson: 41 poäng ur målhändelserna mot 41 i
  tabellen. Att de stämmer exakt visar att båda buggarna är borta: utan
  avdupliceringen hade händelserna räknats flera gånger, och utan
  namnrensningen hade assist med påklistrat `Pos` fallit bort.
- **EliteProspects-direktlänkar** — 25/25 i truppen, 31/33 i HA 25/26.
  `api/eliteprospects.py` slår upp spelarens EP-id via deras autocomplete och
  bygger `/player/{id}/{slug}`. Cachas ett dygn i minnet och persisteras i
  `raw_sports.eliteprospects_links`.
- **Rensade namn** i matchrapporten — `Olofsson, Jacob` i stället för
  `Olofsson, JacobPos`, `Crosschecking` i stället för
  `Crosschecking(10:07 - 12:07)`.

### Matchrapporterna

Matcher-fliken var låst till den aktiva säsongen, och eftersom SHL 26/27 inte
börjat fanns det inte en enda nåbar matchrapport i appen. Fliken har nu
säsongsväljare, och de 52 rapporterna från HA 25/26 går att öppna. Rapporten
har fått avsnitten **Matchbild** (tid i ledning, oavgjort, underläge, största
ledning) och **Specialteam** (powerplay, boxplay, utvisningsminuter).

## Spelarna på isen: hämtas nu

Målcellen på Swehockeys händelsesida bär `Pos. Part.` och `Neg. Part.` — alla
spelare på isen för respektive lag vid varje mål. Det låg oanvänt.

Händelseskrapningen är flyttad från `slutspel/scrapers/swehockey/upload_game_events.py`
(som läste lokala JSON-filer från en engångsskrapning och droppade tabellen
vid varje körning) in i Cloud Function-scrapern, som datatypen `game_events`.
Parsern ligger i `functions/game_events_parser.py` och är fri från nätverk och
BigQuery, så den går att testa mot sparade sidor.

Vad som nu hämtas per match:

- mål med målskytt, båda assisterande och deras tröjnummer
- **spelarna på isen**, `on_ice_for` och `on_ice_against` som tröjnummer
- utvisningar med typ och minuter
- målvaktsbyten och timeouter
- period ur sidans rubrikrader, så förlängning och straffar blir rätt
  i stället för gissade ur klockan

Två buggar rättade på vägen:

1. **Teckenkodningen.** Händelsesidan skickar `Content-Type: text/html` utan
   teckenuppsättning. HTTP:s standardvärde är då ISO-8859-1, men innehållet är
   utf-8 — så varje svensk bokstav blev mojibake: `Tellström` skrevs
   `TellstrÃ¶m`, och utvisningstypen `Okänd` blev `OkÃ¤nd` i analysmodulen.
   Schema- och truppsidorna deklarerar utf-8 och var därför oskadda.
2. **Hopklistrade namn.** Cellernas inre taggar strippades utan mellanrum.
   Parsern använder nu separator, så `Possler, GustavPos` är borta vid källan.
   API:ts `clean_person` städar äldre rader och kan tas bort när allt är
   omskrapat.

Händelsesidan hämtas en match i taget, cirka en sekund styck, och bara för
lagets egna matcher — ett femtiotal per säsong. En schemalagd körning tar de
tjugo senaste (`SWEHOCKEY_EVENTS_LIMIT`), en backfill tar alla via
`?events_limit=all`, vilket `bash deploy.sh backfill` gör.

### Känd lucka: slutspelet saknar match-id

Swehockey länkar inte matcherna från slutspelssidan. `Overview`, `GameCenter`
och `Live` för samma säsongsgrupp har inte heller några `/Game/Events/`-länkar.
Grundserien har 364 länkar, slutspelet noll.

Alla 13 slutspelsmatcher i HA 25/26 saknar därför `game_id` och får varken
matchrapport eller händelser — inklusive finalserien. Det går inte att lösa
utan att gissa id:n, vilket vore fel sätt. Om ett annat sidflöde hittas är det
bara `_extract_schedule_rows` som behöver ändras.

### Nästa steg på det här

Datat finns snart i `raw_sports.swehockey_game_events`. Kvar att bygga:

- on-ice för och emot per spelare, alltså ett riktigt plus/minus
- vilka kombinationer som producerar mål
- vilka som är på isen när det släpps in

## Kvarstående, oberoende av deployen

1. **Två spelare saknar EP-länk** i HA 25/26 — Theocharidis och Cairns.
   EP stavar deras namn annorlunda. Matchningen kräver att efternamn,
   förnamn och position stämmer innan den länkar direkt, och lämnar hellre
   en söklänk än en länk till fel spelare. Går att rätta för hand genom att
   lägga en rad i `raw_sports.eliteprospects_links` — senaste raden per
   `name_key` vinner.

### Sportradar-nyckeln: avskriven

Nyckeln som låg hårdkodad i `functions/main.py` var en gammal trial-nyckel
utan värde. Den är borta ur koden och behöver inte roteras.

## Nästa gång

Kör i tur och ordning:

```
cd ~/lsb && git pull -q && bash deploy.sh scraper && bash deploy.sh backfill
```

Första kommandot lägger ut den nya händelseskrapningen, det andra hämtar hem
hela HA 25/26 med spelarna på isen. Backfillen tar ett par minuter eftersom
händelsesidan hämtas en match i taget.

Därefter finns on-ice-datat i BigQuery och nästa steg är att bygga vidare på
det — plus/minus, kombinationer och vilka som är på isen vid insläppta mål —
samt att slipa statistiksidan.
