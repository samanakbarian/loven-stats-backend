# Nästa steg

Uppdaterad 2026-09-05.

## Var vi står

Frontend är live. Backend har en deploy som väntar.

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

### Klart men inte deployat

Kräver `bash deploy.sh api` i Cloud Shell:

- `/api/v1/players` — truppens utespelare med percentil mot hela serien.
- `/api/v1/player/{namn}` — en spelares säsong, poäng match för match.
- **Direktlänkar till EliteProspects.** `api/eliteprospects.py` slår upp
  spelarens EP-id via deras autocomplete och bygger `/player/{id}/{slug}`.
  Cachas ett dygn i minnet och persisteras i
  `raw_sports.eliteprospects_links`. Utfall mot skarp data: 25/25 för
  truppen 26/27, 31/33 för HA 25/26.

Frontend klarar sig utan dem: spelarytan faller tillbaka på poängligan från
`/api/v1/statistics` när `/api/v1/players` svarar 404. Det är verifierat.

## Blockering: Cloud Shell har tappat sin gcloud-inloggning

`gcloud auth list` är tom i en ny session. `gcloud config set project`
fungerar ändå, eftersom det bara är lokal konfiguration — därför syns felet
först när en deploy startar.

Att logga in från telefonen är svårt: sessionen startas om medan man är inne
på verifieringslänken, och då är prompten borta.

### Att prova, i tur och ordning

1. **tmux**, så att prompten överlever att appen lämnas:

   ```
   tmux new -s d
   gcloud auth login --no-launch-browser
   ```

   Gå ut, öppna länken, kopiera koden. Tillbaka i Cloud Shell:
   `tmux attach -t d`, klistra in koden.

2. **Från en dator** — `shell.cloud.google.com`, logga in en gång, kör
   deployen. Enklast om en dator finns till hands.

3. **Full hemkatalog** kan vara grundorsaken. Cloud Shells hemkatalog
   ligger kvar mellan sessioner, och är den full går gcloud-konfigurationen
   sönder permanent — då hjälper varken omstart eller inloggning förrän det
   är rensat:

   ```
   cd ~/lsb && git log --oneline -1 && gcloud auth list && df -h ~ | tail -1
   ```

### Permanent lösning, om vi vill bli av med problemet

Sätt upp en **Cloud Build-trigger** så att en push till `master` deployar
backend automatiskt, precis som Netlify gör med frontend. Kräver att
behörigheter klickas igenom en gång i Google Cloud Console — men efter det
behöver Cloud Shell aldrig röras igen. Inte påbörjat; vänta på besked.

## Kvarstående, oberoende av deployen

1. **Rotera Sportradar-nyckeln.** Den låg hårdkodad i `functions/main.py`
   och är borta ur koden, men finns kvar i git-historiken. Nyckeln måste
   bytas hos Sportradar — att ta bort raden räcker inte.

2. **Gör händelse-scrapern produktionsklar.**
   `slutspel/scrapers/swehockey/upload_game_events.py` droppar fortfarande
   tabellen vid varje körning. Den ska appenda som de andra, med
   avduplicering på senaste `scraped_at`.

3. **Två spelare saknar EP-länk** i HA 25/26 — Theocharidis och Cairns.
   EP stavar deras namn annorlunda. Matchningen kräver att efternamn,
   förnamn och position stämmer innan den länkar direkt, och lämnar hellre
   en söklänk än en länk till fel spelare. Går att rätta för hand genom att
   lägga en rad i `raw_sports.eliteprospects_links` — senaste raden per
   `name_key` vinner.

## Nästa gång

Börja med att få igenom `bash deploy.sh api`. Därefter är
spelarprofilerna och EP-länkarna live, och nästa naturliga steg är att välja
mellan Cloud Build-triggern och punkt 1–2 ovan.
