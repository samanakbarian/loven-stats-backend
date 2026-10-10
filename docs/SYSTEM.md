# Systemet

Uppdaterad 2026-10-10. Hur sida377.se hänger ihop i dag: komponenterna,
flödet, schemat och var allt körs. Planerna framåt ligger i
[HALLBAR_ARKITEKTUR.md](HALLBAR_ARKITEKTUR.md) och
[API_ARKITEKTUR.md](API_ARKITEKTUR.md), kraven i
[ICKE_FUNKTIONELLA_KRAV.md](ICKE_FUNKTIONELLA_KRAV.md).

## Översikt

```
Swehockey Stats (enda källan)
  │  HTML-sidor och protokollens PDF:er
  ▼
swehockey-stats-scraper        Cloud Functions Gen2, Python
  │  skriver bara det som ändrats (innehållshash, ETag)
  ▼
BigQuery
  raw_sports.*   append-only, en generation per skörd
  core.*         vyer: senaste generationen per rad
  marts.*        vyer: spelare och lag per match
  │
  ▼
loven-stats-api                Cloud Run, FastAPI, en instans
  │  cache i processminnet, gzip, nyckelkontroll, taktbegränsning
  │
  ├──────────────► loven-matchfilm        Cloud Run, Node + Playwright + ffmpeg
  │                  │  läser API:t som en besökare, renderar filmer
  │                  ▼
  │                GCS granskaren-d51a1-matchfilm (publik)
  │                  film/{game_id}.mp4|jpg|json
  │                  film/sasong/{säsong}.mp4|jpg|json
  ▼                  │
sida377.se  ◄────────┘   React-SPA på Netlify, bygger vid push till main
```

Nyheter och X-flödet går vid sidan av. `silly-season-scraper` (Cloud Function)
samlar nyheterna i en blob i GCS som `/api/v1/feed` läser. `/api/v1/x-feed`
hämtar klubbens inlägg från X och sparar dem i en egen cache i GCS.

## Komponenterna

| Komponent | Var | Kod | Driftsätts |
|---|---|---|---|
| Frontend | Netlify | `slutspel/frontend_v2` | automatiskt vid push till `main` |
| API | Cloud Run `loven-stats-api` | `api/` | `bash deploy.sh api` |
| Skörden | Cloud Functions `swehockey-stats-scraper` | `functions/` | `bash deploy.sh scraper` |
| Vyerna | BigQuery `core`, `marts` | `sql/` | `bash deploy.sh views` |
| Filmerna | Cloud Run `loven-matchfilm` | `film/` | `bash deploy.sh film` |
| Nyheterna | Cloud Functions `silly-season-scraper` | `functions/silly_scraper.py` | `bash deploy.sh news` |

Projektet är `granskaren-d51a1`, regionen `europe-west1`. Backend driftsätts
för hand från Cloud Shell, alltid från `master`:

```
cd ~/loven-stats-backend && git checkout master && git pull && bash deploy.sh api
```

`git checkout master` är med av ett skäl: Cloud Shell har en gång stått på en
annan gren och driftsatt parkerad kod.

## Schemat

Alla tider svensk tid (Cloud Scheduler, `Europe/Stockholm`).

| Tid | Jobb | Gör |
|---|---|---|
| 00:30, 07:30, 18:30, 22:30 | `swehockey-stats-scraper-job` | skörden |
| :45 efter varje skörd | `loven-api-refresh` | tvingar om API:ts cacher |
| :55 efter varje skörd | `loven-matchfilm-job` | matchfilmer och säsongsfilm |
| var tionde minut | `loven-api-warmup` | håller cachen varm |

Skörden hämtar hela seriens schema, tabell och statistik varje gång.
Matchhändelser, uppställningar och protokoll hämtas för lagets egna matcher
inom `SWEHOCKEY_REFRESH_DAYS` (21) dagar efter matchen, seriens övriga matcher
en gång.

## API:t

`api/main.py` (7 100 rader, 29 endpoints) plus `serien.py`, `gamescore.py`,
`matchmodell.py`. Skyddet framför BigQuery:

- **En instans** (`--max-instances 1`). Alla besökare träffar samma varma
  cache. Beslut 5 oktober; höjs med `MAX_INSTANCES=3 bash deploy.sh api` om
  det börjar gå trögt.
- **Cache i processminnet**, sex timmar för de flesta svar. Bara lyckade svar
  lagras (`cached_ok`); ett felsvar fastnar aldrig.
- **Nyckelkontroll** (`_okand_nyckel`). Okända säsonger och matcher avvisas
  innan de når BigQuery, så ett anrop med påhittade nycklar kostar ingenting.
  Felar uppslaget släpps anropet igenom hellre än att sajten stängs.
- **Taktbegränsning**: 120 anrop i minuten per IP, `refresh=1` 12 i timmen.
- **gzip** på svar över 1 000 byte.
- **CORS** för sida377.se, Netlifys förhandsvisningar och localhost. Bara GET,
  inga cookies.

Matchrapportens tre bärande frågor — händelser, schemarad och lagens
summering — måste lyckas. Felar någon av dem blir det ett felsvar, inte en
rapport utan mål som cachas i sex timmar (som hände match 1109953 den 9
oktober).

## Filmerna

`film/` är en egen Cloud Run-tjänst, privat, som bara schemaläggaren anropar
(`/kor`, med OIDC). Den läser API:t precis som sidan gör, så en film säger
aldrig något annat än sidan.

**Matchfilmen** (`film.html`, `ljud.mjs`): matchen som en Text-TV-sida på
cirka 42 sekunder. Görs för spelade matcher de senaste 14 dagarna
(`FILM_DAGAR`), en gång per match, och om bara när matchdatan eller filmens
kod ändrats.

**Säsongsfilmen** (`sasong.html`, `hockey8.js`, `ljud_sasong.mjs`):
tabellen omgång för omgång, Lövens placering som kurva, en åttabitarsduell mot
seriesnittet och säsongen i siffror. En per säsong, skrivs över när datan
ändrats, i praktiken en gång per omgång. Görs automatiskt bara för den aktiva
säsongen; äldre med `/kor?sasong=ha_2526`.

**Hur en film blir till:**

1. Data hämtas ur API:t.
2. Kontrollen (`kontroll.mjs`, `kontrolleraSasong`) stämmer av den. Hellre
   ingen film än en som säger fel:
   - matchfilmen: protokollet inte preliminärt, målen ger resultatet, skotten
     per period ger summan, Matchens bästa finns
   - säsongsfilmen: placeringarna går jämnt upp i varje omgång, Lövens poäng
     stämmer med tabellen, seriens siffror kompletta för alla spelade matcher
3. Fingeravtrycket (data + filmens kodversion) jämförs med det som ligger i
   bucketen. Lika: ingenting görs.
4. Sidan renderas ruta för ruta i Chromium (`window.render(t)`, 25 bilder i
   sekunden), ljudet syntetiseras som WAV, ffmpeg gör en H.264/AAC-mp4.
5. mp4 och stillbild laddas upp, JSON:en sist. JSON:en är signalen till
   sidan att filmen finns.

Allt ljud är syntetiserat i koden. Inget samplat material och ingen
upphovsrättsskyddad musik.

**På sidan** visar `FilmKort` (frontend) filmen som ett kompakt kort med
Spela, Spara och Dela. Finns ingen JSON visas ingenting.

## Frontend

React 19, Vite 8, TypeScript 6. Ingen state-hantering utöver React, inga
diagrambibliotek — diagrammen är handritad SVG. Huvudvyerna:

- **Matcher** och **Matchrapport** (`/matcher/:id`): rapporten, GameScore,
  lag mot lag, utvisningar, matchfilmen.
- **Statistik**: Laget, Spelare och Utveckling (säsongsfilmen, tabellen över
  tid, form, PDO, publik).
- **Trupp**, **Nyheter**, **X-flöde**, **Metod** (formlerna).

API-adressen och filmernas adress står i `src/config/api.ts`.

## Var data kan bli fel, och vad som fångar det

| Risk | Skydd |
|---|---|
| Swehockey rättar i efterhand | append-only + vyer: rättelsen slår igenom vid nästa skörd |
| Preliminärt protokoll | `provisional` i matchrapporten, markeras på sidan; ingen matchfilm |
| En BigQuery-fråga felar | bärande frågor ger felsvar, inget cachas |
| Seriens matcher kommer i olika skördar | säsongsfilmen väntar tills alla har siffror |
| Härledda tal glider från källan | avstämningen i skörden mot Swehockeys egna summor |
| Svar ändras av en kodändring | gyllene mästaren (`tests/gyllene_master.py`) |
| Sajten nere | röktestet (`tests/roktest.py`) |
