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

## Fyndet: spelarna på isen finns redan i källan

Målcellen på Swehockeys händelsesida ser ut så här:

```
71. Possler, Gustav  (1)  10. Nilsson, Marcus  26. Ottosson, Axel
Pos. Part.: 10 , 26 , 31 , 33 , 64 , 71
Neg. Part.: 16 , 26 , 29 , 31 , 54 , 59
```

`Pos. Part.` och `Neg. Part.` är **alla sex spelarna på isen** för respektive
lag vid varje mål. Scrapern kastar bort dem idag. Med dem går det att räkna
per match och över säsongen:

- on-ice för och emot per spelare, alltså ett riktigt plus/minus
- vilka kombinationer som producerar mål
- vilka som är på isen när det släpps in

Det är den enskilt största datakällan vi har oanvänd, och precis det som
efterfrågats som "djupgående analys per match".

Samma hopklistring är orsaken till namnbuggen: taggarna strippas utan
mellanrum, så sista assisten blir `Possler, GustavPos`. API:t rensar det vid
utläsning sedan 2026-09-05 (`clean_person` i `api/main.py`), men källan bör
sluta skapa problemet.

### Vad som krävs

`slutspel/scrapers/swehockey/upload_game_events.py` behöver skrivas om. Den
ska ändå göras om — den droppar tabellen vid varje körning. Samla ihop:

1. Appenda i stället för att droppa, med avduplicering på senaste
   `scraped_at` per `game_id`, som de andra tabellerna.
2. Sluta klistra ihop celltexten; separera taggar med mellanslag före
   parsningen.
3. Fånga `Pos. Part.` och `Neg. Part.` som två nya kolumner med tröjnummer.
4. Kör om alla matcher i HA 25/26 så historiken får fälten.

Först därefter kan API och frontend bygga på det.

## X-flödet: miljövariabler raderade av deploy.sh

`deploy.sh` använde `--set-env-vars`, som sätter *hela* uppsättningen
miljövariabler på Cloud Run och tar bort allt som inte står i kommandot.
Första körningen raderade därmed `X_BEARER_TOKEN` med flera, och `/api/v1/x-feed`
svarar sedan dess `"error": "missing_token"` med noll tweets.

Skriptet använder nu `--update-env-vars`, som lägger till utan att radera. De
variabler som redan försvunnit hämtas tillbaka från en äldre Cloud
Run-revision:

```
cd ~/lsb && git pull -q && bash deploy.sh restore-env
```

Den letar igenom de 40 senaste revisionerna efter en med `X_BEARER_TOKEN`,
kopierar dess variabler till den nuvarande och kontrollerar sedan att
X-flödet ger tweets igen. Värdena skrivs till en temporärfil och aldrig till
terminalen. Variabler som hämtas ur Secret Manager kan inte återställas den
vägen och listas separat.

**Kört 2026-09-05.** Återställde tolv variabler från revision
`loven-stats-api-00093-b7q`, däribland `X_BEARER_TOKEN` och `GEMINI_API_KEY`.
X-flödet ger 30 inlägg igen.

Svaret cachas en timme i en GCS-blobb, så direkt efter en återställning
serveras fortfarande felet som skrevs medan tokenet saknades. Kontrollen
använder därför `?force_refresh=true`.

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
