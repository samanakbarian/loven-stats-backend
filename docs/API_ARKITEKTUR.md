# API:ts arkitektur — mål och väg dit

Backloggen: feature 36. Status: planerad, inte påbörjad.

## Läget i dag

`api/main.py` är 6 700 rader med 29 endpoints och 87 funktioner. Varje
endpoint bygger sin SQL som f-sträng, räknar, formar svaret och fångar sina
egna fel. Det fungerar, men:

- Beräkningarna går inte att testa utan BigQuery, så de testas inte.
- "Är det Björklöven?" avgörs på tre sätt (`BJK_HOME`, `BJK_CODES`,
  `LIKE '%ifb%'` i SQL åtta gånger). Tabell, form och säsongsuppslag räknas på
  flera ställen.
- 62 `except Exception`. Sidan kraschar inte, men fel försvinner tyst.
- Svarens form finns bara i koden. Frontend skriver av den för hand, och ett
  fält som byter namn märks först i webbläsaren.
- (Åtgärdat 2026-09-24: 107 lösa skript i roten, `dbt/` och tio inaktuella
  dokument är borttagna.)

Datalagret (raw → core → marts) är sunt och berörs inte.

## Målbilden

Onion-tanken utan dess ceremoni: en ren kärna som inte vet om BigQuery eller
HTTP, med data och webb ytterst. Inga entitetsklasser, repository-gränssnitt
eller use case-klasser — API:t läser, räknar och svarar, och har inga
domänregler att skydda. Vanliga funktioner räcker.

```
api/
  main.py            create_app(): middleware, CORS, felhanterare, routrar
  installningar.py   Settings (pydantic-settings), alla miljövariabler
  beroenden.py       Depends: BigQuery-klient, inställningar, cache
  lag.py             en definition av "vårt lag" och av säsongen
  routrar/           HTTP: parametrar, cache, svarsmodell, statuskoder
    matcher.py  spelare.py  serien.py  prognos.py  floden.py  drift.py
  modeller/          Pydantic-svarsmodeller, en fil per router
  berakning/         ren Python: inga importer av fastapi, bigquery, os
    tabell.py  form.py  percentiler.py  mot_serien.py  matchmodell.py
  data/              all SQL, en funktion per fråga, returnerar rader
    matcher.py  spelare.py  serien.py  sasong.py
tests/
  enhet/             berakning/, utan nätverk
  kontrakt/          svarsmodellerna mot sparade svar
  gyllene/           sparade svar per endpoint, jämförs efter varje steg
```

**Beroenden går bara inåt.** `routrar` anropar `data` och `berakning`.
`berakning` importerar ingen av dem. `data` vet ingenting om HTTP. En
importregel i CI (`import-linter` eller ett enkelt test) håller gränsen.

## Praxis som ska följas

**FastAPI**
- En `APIRouter` per område, prefix `/api/v1`. `main.py` bara sätter ihop
  appen. Sökvägarna ändras inte — frontend berörs inte av flytten.
- BigQuery-klienten och inställningarna kommer via `Depends`, en instans per
  process. I test byts de via `app.dependency_overrides`, utan monkeypatch.
- Varje endpoint har `response_model`. OpenAPI-schemat blir kontraktet, och
  frontendens typer genereras ur det (`openapi-typescript`) i stället för att
  skrivas för hand. Fält som kan saknas är `Optional` i modellen, inte i en
  kommentar.
- Endpoints förblir `def`, inte `async def`: BigQuery-klienten är synkron och
  FastAPI kör dem då i trådpoolen. Parallella frågor via `fraga_parallellt`.

**Data**
- Värden in i SQL går alltid som `ScalarQueryParameter`, aldrig som f-sträng.
  Bara tabellnamn får formateras in, och de kommer från inställningarna.
- All SQL bor i `data/`. Längre frågor som egna `.sql`-filer bredvid.
- Ett säsongsuppslag och en lagdefinition, i `lag.py`, som alla använder.

**Beräkning**
- Rena funktioner med typer in och ut (`TypedDict` eller `dataclass`). Samma
  indata ger samma svar; ingen klocka, inget nätverk, ingen global cache.
- `mypy --strict` på `berakning/` och `data/`.

**Fel och loggning**
- Egna undantag (`DataSaknas`, `KallaNere`) som en felhanterare gör om till
  rätt statuskod. Inget `except Exception` som sväljer.
- Delar av ett svar som får falla bort (kontext, jämförelse mot serien) gör
  det uttryckligen: loggas som varning och markeras i `meta.degraded`.
- Strukturerad loggning i JSON för Cloud Logging, med anropets id.

**Cache**
- En cachemekanism, som dekorator på routernivå, med endpointens namn i
  nyckeln och TTL ur inställningarna. `refresh=1` och taktbegränsningen
  fungerar som i dag.

**Verktyg och CI**
- `ruff` för lint och formatering, `mypy`, `pytest`. Samma kontroller lokalt
  (pre-commit) och i GitHub Actions vid varje push till master.
- Beroenden låsta med versioner. `google-genai` tas bort.
- Svenska namn och kommentarer, som i resten av projektet.

## Vägen dit

Ett steg i taget. Varje steg deployas för sig, och de sparade svaren ska vara
identiska före och efter. Ingen ny funktion under flytten.

1. **Skyddsnät.** Spara svaren från alla endpoints för tre säsonger
   (`tests/gyllene/`), med samma jämförelse som `tests/gyllene_master.py` gör
   för analytics. CI med ruff och pytest.
2. **Skelett.** `installningar.py`, `beroenden.py`, `lag.py`, `create_app()`.
   Routrarna in en i taget, koden flyttas oförändrad.
3. **Datalagret.** SQL ut ur routrarna till `data/`, med parametrar.
4. **Beräkningarna.** Ut i `berakning/` med enhetstester. `/api/v1/analytics`
   sist — den är 1 600 rader och har redan sin gyllene mästare.
5. **Svarsmodeller.** `response_model` på alla endpoints, typer genereras till
   frontend.
6. **Städning.** `/api/silly-season` när Nyheter inte längre faller tillbaka
   på den. Rotskripten, dokumenten och `/api/v1/financials` är redan borta.

## Klart när

- `main.py` under 150 rader. Ingen fil i `routrar/` över 400.
- Ingen SQL med inbakade värden. Ingen `except Exception` utan loggning.
- `berakning/` täckt av enhetstester; CI grön på varje push.
- Alla sparade svar identiska med dagens.
- Frontendens typer genereras ur OpenAPI-schemat.
