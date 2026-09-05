# Nästa steg

Handoff efter sessionen 2026-09-05. Allt nedan är pushat; inget arbete
ligger okommittat.

## Läget

| | Status |
|---|---|
| Frontend (`slutspel`, gren `main`) | **Deployad och live** på viskauppigen.netlify.app |
| Backend (`loven-stats-backend`, gren `master`) | Pushad men **inte deployad** |

Frontend auto-deployar via Netlify vid push till `main`. Backend gör det
inte än — se punkt 1.

## 1. Deploya backend — blockerar allt annat

Fyra saker väntar i `master` och syns inte i appen förrän API:t deployats:

- `GET /api/v1/match/{game_id}` — driver matchrapporten, som i dag alltid
  visar fel eftersom endpointen inte finns
- `GET /api/v1/standings` — utan den döljer Matcher-sidan tabellen helt
- `has_team_data` i `/api/v1/seasons` — utan den listar säsongsväljaren
  sju säsonger i stället för fyra
- `game_id` i schemascrapern — **rotorsaken** till att all
  händelsebaserad analys returnerar nollor

### Om att deploya härifrån

gcloud CLI **går** att installera i sessionen — nedladdningen är
verifierad (86 MB, HTTP 200). Men det löser inte problemet:

1. **Behörighet saknas, inte verktyg.** Ingen ADC, ingen nyckelfil, ingen
   metadataserver. `CLOUDSDK_AUTH_ACCESS_TOKEN=proxy-injected` är en
   platshållare från sandlådan.
2. **Containern är flyktig.** Den återtas efter inaktivitet, så all
   installation och inloggning måste göras om varje session.

Det finns ändå en fungerande väg om du vill deploya från sessionen:

```bash
# i sessionen
curl -sSL https://sdk.cloud.google.com | bash && exec -l $SHELL
gcloud auth login --no-launch-browser
```

Det skriver ut en URL och väntar på en kod. Du öppnar URL:en, godkänner,
klistrar tillbaka koden. Då kan vi köra deploy-kommandona direkt.
Nackdelen är att det autentiserar som *dig*, med dina fulla rättigheter,
och måste göras om varje gång.

### Rekommenderat i stället: GitHub Actions

`.github/workflows/deploy.yml` finns redan och deployar både API och
scraper vid push till `master`. Den behöver en engångsuppsättning —
fyra `gcloud`-steg plus två GitHub-secrets, allt i `docs/DEPLOY.md`.

Efter det deployar backend automatiskt, precis som frontend, och frågan
om credentials i sessionen försvinner permanent.

## 2. Rotera Sportradar-nyckeln

`functions/main.py:15` hade en API-nyckel hårdkodad som default-värde.
Borttagen ur koden, men **kvar i git-historiken**. Måste roteras hos
Sportradar. Se avsnittet längst ned i `docs/DEPLOY.md`.

Överväg att avregistrera den i stället: Sportradar-flödet används inte av
appen — koden pekar på `trial`-endpointen, är hårdkodad till HA 25/26 och
skriver till GCS i stället för BigQuery.

## 3. Verifiera matchrapporten mot skarp data

Frontend är testad mot en fixtur byggd ur en riktig Swehockey-matchsida
(Mora–Björklöven 1–2 efter straffar). Men **SQL:en i
`/api/v1/match/{game_id}` är oprövad** — den kunde inte köras utan
BigQuery-åtkomst.

Direkt efter deploy:

```bash
curl -sS "$API/api/v1/match/1005615" | head -c 400
```

Notera att `game_id` är null för SHL 26/27 tills matcherna spelats —
Swehockey länkar till matchhändelserna först då. Testa därför mot en
spelad HA-match.

## 4. Produktionssätt eventscrapern

`slutspel/scrapers/swehockey/upload_game_events.py` är ett handkört
skript som gör `delete_table()` följt av `create_table()` — den droppar
och återskapar hela eventtabellen vid varje körning, utan schemaläggning.
Den kommer inte att uppdatera sig själv under säsongen.

Behöver bli en schemalagd Cloud Function som appendar, i samma mönster
som `swehockey-stats-scraper`. Utan den fylls matchrapporten aldrig på
automatiskt.

## 5. Kvar i planen

Planen: https://claude.ai/code/artifact/f6f14075-0dcf-445e-8b95-7385b9104604

Byggt hittills: sanering, layoutfix, säsongshantering, fem flikar,
Matcher-sidan, Nyheter, matchrapporten, egenhostade typsnitt.

Inte påbörjat, från featurekatalogen i planens avsnitt 01:

- Spelarprofiler med formkurva och rullande snitt
- Ligapercentiler per spelare
- Elo och rullande form som egna vyer under Statistik
- Head-to-head inför nästa motståndare
- Säsongsjämförelse HA 25/26 → SHL 26/27

Notera också att analytics-endpointen redan beräknar ett tjugotal moduler
(`elo_history`, `pythagorean`, `chemistry`, `first_goal_impact`,
`game_state`) som ligger begravda bakom analysfliken. Flera av punkterna
ovan är därför mest frontend-arbete.

## Testrigg

Chromium når inte externa värdar genom sessionens proxy, bara curl gör
det. Lösningen som användes:

```bash
# brygga som vidarebefordrar API-anrop via curl
node bridge.mjs                      # lyssnar på 127.0.0.1:8787
cd frontend_v2
VITE_API_URL=http://127.0.0.1:8787 npm run build
npx serve -s dist -l 4173            # kör från frontend_v2/, annars 404
```

Sedan Playwright mot `127.0.0.1:4173` med
`proxy: { server: HTTPS_PROXY, bypass: '127.0.0.1,localhost' }`.
Utan bypass går även localhost genom proxyn och ger 405.

## Premiär

SHL 26/27 startar **19 september 2026**. Björklövens första match är
borta mot Djurgården. Från och med då börjar `game_id` fyllas för spelade
matcher, och matchrapporten får riktig data — förutsatt att punkt 1 och 4
är gjorda.
