# Nästa steg

Uppdaterad 2026-09-22, efter SHL-premiären.

Det här är överlämningen: var projektet står, hur det hänger ihop, och vad
som är värt att veta innan man rör något.

## Var vi står

SHL 2026/27 har börjat. **En match spelad**: Djurgården–Björklöven 0–3 borta
den 19 september, 14 100 åskådare. Laget ligger trea på målskillnad efter
första omgången. Nästa match är borta mot Örebro den 24 september.

Frontend är live på `sida377.se`. Backend är live på Cloud Run.

**Odeployat just nu:** `663d6ac` (cachen för spelarprofilernas målhändelser)
och feature 26 — seriens matcher, Swehockeys lagstatistik och
`/api/v1/league`. Ordningen spelar roll: vyerna först, sedan scrapern (som kör
en gång och fyller tabellerna), sist API:t.

```
cd ~/loven-stats-backend && git pull origin master && bash deploy.sh views && bash deploy.sh scraper && bash deploy.sh api
```

Förra säsongen för jämförelse — HA 25/26:s lagstatistik och dess 364 matcher.
Tidsbudgeten gör att det blir tre-fyra körningar; kör samma rad tills
`league_events` laddar 0 rader.

```
cd ~/loven-stats-backend && EVENTS_LIMIT=0 bash deploy.sh backfill
```

Frontend behöver aldrig deployas för hand. Netlify bygger på push till `main`.

## Systemet i korthet

Två repon, en datakälla.

| | repo | gren | deploy |
|---|---|---|---|
| Frontend | `samanakbarian/slutspel` | `main` | Netlify, automatiskt |
| Backend | `samanakbarian/loven-stats-backend` | `master` | `bash deploy.sh` för hand |

**Swehockey Stats är enda källan.** Ingen Sportradar, inget annat. Nyckeln som
låg i koden var en gammal trial-nyckel och är avskriven — den behöver inte
roteras.

Dataflödet är views hela vägen upp:

```
Cloud Function-scraper  →  raw_sports.*   (append-only, en generation per skörd)
                        →  core.*         (vy: QUALIFY scraped_at = MAX(...))
                        →  marts.*        (vy)
                        →  FastAPI på Cloud Run
                        →  React-SPA på Netlify
```

Inget steg är materialiserat. Rättar Swehockey ett protokoll slår det igenom
överallt vid nästa skörd, utan att något behöver byggas om. Det händer på
riktigt: Djurgårdens skott i premiären skrevs upp från 38 till 40 klockan
22:26 på matchkvällen, och sajten följde med. Flashscore gjorde det inte.

Skörden kör **00:30, 07:30, 18:30 och 22:30** svensk tid. Femton minuter
senare tvingas API-cacherna om. Hela seriens schema, tabell, spelar- och
målvaktsstatistik hämtas varje gång; matchhändelser, uppställningar och
protokoll bara för lagets egna matcher, och bara inom `SWEHOCKEY_REFRESH_DAYS`
(21). En rättelse som kommer senare än så når säsongssiffrorna men inte
matchrapporten. Det är ett medvetet val — se "Öppna punkter".

## Feature 26 och lagstatistiken

Seriens övriga matcher hämtas nu, händelsesidan en gång per match, i egna
tabeller (`swehockey_league_game_*`). Våra tabeller rörs inte: en hel körning
mot Swehockey gav dem rad för rad identiska före och efter.

Under arbetet dök Swehockeys egen lagstatistik upp — `/Teams/Statistics/`, nio
sidor per serie med powerplay, boxplay, skott, tekningar, utvisningar, ledning
och underläge. Den är facit och hämtas också (`swehockey_team_stats`). Mätt
över HA 25/26 går mål, skott för och emot, powerplaymål och insläppta i boxplay
ihop exakt med det vi räknar ur matcherna, för alla fjorton lag.

**Powerplaytillfällen går inte att räkna ur händelserna.** Swehockey slår ihop
överlappande utvisningar och stryker samtidiga på ett sätt som listan inte
visar; bästa regeln träffade 83 procent av matcherna. Ta dem ur lagstatistiken.
`/api/v1/analytics` räknar fortfarande varje utvisning som ett tillfälle, så
dess PP-procent ligger lägre än Swehockeys. Den är inte ändrad — det är nästa
sak att flytta över.

Frontend: kortet Serien under Statistik → Laget (vårt värde, placering och alla
lag på en linje per mått), Skott och tur (skottandel mot PDO för alla lag) och
fyra nya rader i Inför matchen. Alla väntar till tre omgångar spelats.

## Buggmönstret i seriestarten

Det här är den viktigaste lärdomen från premiärveckan, och den gäller alla
nya säsonger.

**Kod som fungerat i femtiotvå omgångar går sönder i den första.** Fyra fall
på tre dagar, alla i produktion, alla synliga för besökaren:

1. **Skiljetal på namn.** Tabellen sorterade lika poäng alfabetiskt. Med sex
   lag på tre poäng hamnade Björklöven tvåa när Swehockey hade dem trea.
   Samma fel i målvaktsligan: tre målvakter på hundra procent, och Örebros
   Arntzen ledde trots att laget förlorat 0–7.
2. **Rader utan placering försvann.** Swehockey skriver bara `Rk` på den
   första i varje poänggrupp. Parsern krävde ett tal där, så tretton av
   tjugotvå spelare saknades — inklusive två målskyttar. Efter en hel säsong
   har alla rank igen, så felet läker innan någon hinner leta.
3. **Trösklar kalibrerade för en säsong.** Tekningsrutan krävde tjugo
   tekningar totalt; en center tar så många på två matcher. Nu fem per match,
   vilket fungerar lika bra i omgång ett som femtio.
4. **Stickprov på ett.** Målbalans per femma räknade tre mål och såg ut som
   ett facit. Kortet visas nu först vid trettio mål.

**Regeln som föll ut:** varje sortering behöver ett skiljetal som betyder
något, och varje tröskel ska uttryckas per match i stället för som en
totalsumma. Vid lika i en sortering, fråga vad som faktiskt skiljer — inte
vad som råkar ligga först.

## Källan motsäger sig själv ibland

Swehockeys protokoll är inte alltid internt konsistent. Ett dokumenterat fall
från premiären: målet 0–2 skrivs på Kovacs med assist Niemelä, men
`Pos. Part.` listar varken honom eller assisterande. Deras egen
plus/minus-kolumn har samma lucka, så vår tolkning är korrekt — det är källan
som är fel.

`get_lines` hanterar det: när målskytten saknas bland spelarna på isen är
listan ingen giltig uppgift, och målet räknas till målskyttens femma. Vid lika
antal spelare vinner också målskyttens femma.

Tumregeln: **kontrollera mot Swehockeys egen sida innan du skyller på vår
kod**, och tvärtom — kontrollera vår parser mot sparad HTML innan du skyller
på Swehockey. Båda har haft fel den här veckan.

## Hur ägaren vill ha det

- **Kort.** Sidan är statistik, inte en uppsats. En not på två meningar är för
  lång om en räcker. Återkommande feedback genom hela projektet: "för
  verbose", "det känns AI-generellt", "ska bara vara en liten not".
- **Ingen AI-ton.** Inga frågeformade rubriker, inga brasklappar, ingen
  metodbilaga under ett diagram. Rubriken "Vem var på isen när det small?"
  byttes till "På isen vid mål" av precis det skälet.
- **Svenska överallt**, inklusive kod, kommentarer och commit-meddelanden.
- **Grunddesignen rörs inte.** Färgtoken, kortform och typografi ligger fast.
- **Deploykommandon som ett kopieringsbart block som börjar med `cd`.**
- Push direkt till `main` respektive `master`. Inga pull requests om det inte
  efterfrågas.

## Fallgropar i driften

- **Använd aldrig `gcloud --set-env-vars`.** Den ersätter hela uppsättningen
  och har en gång raderat `X_BEARER_TOKEN`. Skripten använder
  `--update-env-vars`.
- **Bearer-värden skrivs bara till temporära filer**, aldrig till terminalen.
- **Cachen ligger i processminnet, per instans.** `--max-instances 3` är satt
  just därför: fler instanser betyder fler kalla cachar. Värmningen var tionde
  minut håller en instans vid liv, men en andra samtidig besökare kan träffa
  en kall kopia och vänta tjugo till fyrtio sekunder. `--min-instances 1`
  löser det för ungefär sju dollar i månaden; ägaren har valt att vänta.
- **`/api/v1/match/{id}` går inte att värma** — nyckeln är en match av
  femtiotvå. Den lever med sin sextimmars-TTL.
- **`refresh=1` är taktbegränsad till 12 anrop i timmen.** Bra att veta när man
  felsöker och undrar varför svaren plötsligt ser konstiga ut.

## Så verifieras ändringar

Sandlådan når både Swehockey och produktion, men **webbläsaren når inte
API:t** — proxyns certifikat går inte igenom. Mönstret som fungerar:

1. Hämta riktiga svar med `curl` till `/tmp/.../stub_*/`.
2. Starta `npx vite preview --port 4173`.
3. Rendera med Playwright i 390 px och stubba `**/api/v1/**` från filerna.
4. Titta på bilden. Validatorn hittar färgfel, inte överlappande etiketter.

Parserändringar testas mot sparad HTML från Swehockey, inte mot nätet.
`python3 -c "import ast; ast.parse(...)"` innan push, alltid.

## Öppna punkter

**Medvetet inte gjorda:**

- `SWEHOCKEY_REFRESH_DAYS=21`. En rättelse som kommer senare än tre veckor
  efter matchen når säsongssiffrorna men inte matchrapporten, vilket kan ge
  två olika tal på sajten. Ägaren har sagt att det får vara så.
- `--min-instances 1`. Se ovan.
- Poängtaktskurvan (förslag B till Facit-kortet) — hör hemma under Utveckling
  när det finns tio–femton omgångar att rita.

**Kvar att göra:**

- Backparet i femmekortet hämtar positioner ur säsongsstatistiken. Den riktiga
  källan är uppställningssidans struktur — första raden är de tre forwardsen,
  andra backparet. Kräver en kolumn till i råtabellen och en omskördning.
- 88 requests från HeadlessChrome i Cloudflares loggar är oidentifierade.
- `/api/v1/analytics` specialteam ur lagstatistiken i stället för
  händelserna, se ovan.
- Matchrapporten kan sätta lagets siffror mot seriens matcher ("18 skott —
  färre än i nio av tio matcher i år") när det finns ett hundratal att jämföra
  med. Underlaget finns i `core.league_game_summary`.
- Backlogg: feature 27 (live), 33 (serveringslager), 34 (prediktioner),
  35 (matchdriven skörd), `SEC-002`–`SEC-006`, samt städlistan i
  `docs/STADLISTA.md`.

## Om du är ny i projektet

Läs i den här ordningen:

1. Det här dokumentet.
2. `docs/DATAPLATTFORM.md` — datamodellen och varför avdupliceringen finns.
3. `docs/SWEHOCKEY_STATS_SCRAPER.md` — vad som hämtas och hur ofta.
4. `docs/DEPLOY.md` — kommandona.
5. `docs/FEATURE_BACKLOG_2026.md` — 1 843 rader, slå upp vid behov.

Och innan du ändrar en siffra på sajten: öppna Swehockeys sida för samma
match och jämför. Det har löst fler frågor den här veckan än koden har.
