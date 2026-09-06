# Dataplattformen

*Senast verifierad 2026-09-06, mot körande produktion och en fullständigt
skrapad HockeyAllsvenskan 2025/26.*

Det här är den gällande beskrivningen av hur data hämtas, lagras, modelleras
och serveras. Där andra dokument säger något annat gäller det här.

> **`DATA_WAREHOUSE_DESIGN.md` beskriver en dbt-baserad modell som aldrig
> togs i bruk.** Katalogen `dbt/` finns kvar men innehåller inga dimensioner,
> dess faktatabeller bär inga mått, dess källor pekar på tabeller som inte
> existerar, och ingenting läser den. Transformationslagret är SQL-vyer i
> BigQuery, beskrivna nedan.

---

## 1. Lagren

```
Swehockey  ──HTML──▶  scraper ──▶  raw_sports.*     append-only, hash-diffad
           ──PDF ──▶                    │
                                        ▼
                                    core.*          avduplicerat, i vy
                                        │
                                        ▼
                                    marts.*         stjärnschema
                                        │
                                        ▼
                                    FastAPI  ──▶  frontend_v2
```

| lager | vad det är | ändras hur |
|---|---|---|
| `raw_sports` | källnära, append-only, bär hela historiken | skrivs av scrapern, aldrig av hand |
| `core` | samma tabeller, avduplicerade en gång | `sql/core_views.sql` |
| `marts` | konforma dimensioner och fakta med mått | `sql/marts.sql` |

`bash deploy.sh views` bygger `core` och `marts` i den ordningen och skriver ut
hur många rader som sorteras bort per tabell.

### Varför lagren finns

Avdupliceringen har missats två gånger: utvisningarna tredubblades när en
analysfråga summerade tre skörningar, och spelarnas matchlogg multiplicerade
poäng likadant. Ingen rad var trasig — bara läsningen. **API:t läser aldrig
`raw_sports`.** Alla 61 tabellreferenser i `api/main.py` pekar på `core`.

---

## 2. ETL-flödet

### 2.1 Vad som hämtas

| datatyp | källa | korn |
|---|---|---|
| `player_stats`, `goalie_stats` | HTML, säsongstabell | spelare × säsong |
| `standings` | HTML, serietabell | lag × säsong |
| `schedule` | HTML, spelschema | match (hela serien) |
| `roster` | HTML, `PlayersByTeam` | spelare × lag × säsong |
| `game_events` | HTML, `/Game/Events/` | händelse |
| `game_summary`, `game_goalies` | HTML, `/Game/Events/` | lag respektive målvakt × match |
| `game_lineups` | HTML, `/Game/LineUps/` | spelare × match × kedja |
| **`game_boxscore`** | **PDF, `MediaGameSummary`** | **spelare × match** |
| **`player_bio`** | **PDF, `OfficialTeamRoster`** | **spelare × säsong** |

Matchsidorna hämtas bara för lagets egna matcher. Schemat och tabellen
täcker hela serien.

### 2.2 Tre mekanismer håller volymen nere

**Inkrementellt urval.** Körningen frågar BigQuery vilka `game_id` som redan
finns och hämtar bara det som saknas, plus matcher spelade inom
`SWEHOCKEY_REFRESH_DAYS` (21 dagar) — Swehockey rättar rapporter i efterhand,
mätt oftast inom en vecka, i ett fall efter sexton dagar.

**Innehållshash.** Varje match och varje ögonblicksbild får en `content_hash`
över de parsade raderna, med härstamningsfälten undantagna. Är hashen
oförändrad skrivs ingenting. Tabellerna är append-only, så utan den hade fyra
körningar om dygnet i 21 dagar gett 84 generationer av varje match, och de
säsongsvisa bilderna hade skrivit 11 800 rader om dygnet.

**Villkorliga hämtningar för PDF.** En matchrapport är 1–2,6 MB. Utan
`If-None-Match` hade en match i refresh-fönstret hämtats om ~84 gånger,
alltså 150 MB per match. Azure svarar `304` med tom kropp, så en oförändrad
rapport kostar ett par hundra byte. ETaggen lagras på raderna
(`source_etag`) och läses tillbaka vid nästa körning.

| | utan | med |
|---|---|---|
| kontroll av en match i fönstret | 1,8 MB | ~300 byte |
| PDF-trafik per dygn under säsong | ~900 MB | ~7 kB + 3,6 MB/vecka |

### 2.3 Rå-PDF sparas

`gs://<bucket>/raw/web_scrapers/swehockey/reports/<typ>/<game_id>.pdf`

Stabil sökväg per match och rapporttyp, inte per körning: PDF:en hämtas bara
när den ändrats, och historiken bär de tolkade raderna. Poängen är att kunna
förbättra parsern och köra om utan att hämta 90 MB igen — vi har rättat tre
parserfel bara under utvecklingen av det här.

### 2.4 Schemaläggning

`bash deploy.sh schedule` sätter `30 0,7,18,22 * * *` i Europe/Stockholm.

| tid | fångar |
|---|---|
| 18:30 | eftermiddagsmatcher (avslag 15:15–16:00) |
| 22:30 | kvällsmatcher (avslag 19:00) |
| 00:30 | sena avslag och matcher som drog ut |
| 07:30 | nattens rättelser, tabellen inför dagen |

Mätt över arton matcher ligger rapporten uppe **137–195 minuter efter
nedsläpp**. Det tidigare schemat var `0 6 * * 1` — en tisdagsmatch hann bli
sex dygn gammal.

### 2.5 Kvalitetsgrind och avstämning

**Kvalitetsgrinden** (`validate_rows`) kontrollerar *form*: att rader finns,
att fält är ifyllda, att nycklar är unika. Den blockerar hela publiceringen
vid fel. Den säger ingenting om värden.

**Avstämningen** (`_reconcile`) jämför *tal som måste gå ihop*, efter
laddningen. Den fäller aldrig en körning — datat är redan skrivet — men
avvikelser hamnar i svaret, i `raw_ops.ingestion_runs` och som `ERROR` i
Cloud Logging.

| kontroll | jämför |
|---|---|
| `events_goals_match_results` | mål i händelselistan mot mål i resultaten |
| `summary_two_rows_per_game` | två lagrader per match |
| `goalie_saves_plus_goals_equal_shots` | räddningar + insläppta = skott emot |
| `penalties_events_match_summary` | händelsernas utvisningsminuter mot rapportens |
| `summary_shots_match_goalie_shots_against` | skott minus målvakternas skott emot = mål i tomt mål |

Larma på `textPayload:"Avstamningen gick inte ihop"` i Cloud Logging.

---

## 3. Datamodellen

### 3.1 core — en vy per råtabell

| vy | avdupliceras på |
|---|---|
| `core.game_events`, `game_team_summary`, `game_goalies`, `game_lineups`, `game_boxscore` | `game_id` |
| `core.schedule`, `standings`, `player_season_stats`, `goalie_season_stats`, `roster`, `player_bio` | `season_group_id` |
| `core.season` | — (MERGE-hanterad) |
| `core.standings_history` | `season_group_id` + dag |

`QUALIFY scraped_at = MAX(scraped_at) OVER (PARTITION BY ...)` behåller **hela**
den senaste generationen. `ROW_NUMBER` hade behållit en rad per match, vilket
är fel när en match har tjugo händelser.

### 3.2 marts — stjärnschema

**Dimensioner**

| dim | korn | bär |
|---|---|---|
| `dim_season` | säsongsgrupp | grundserie och slutspel var för sig |
| `dim_team` | lag | lagnamn, härledd lagkod, `is_bjorkloven` |
| `dim_player` | spelare | position, tröjnummer, **födelsedatum, ålder, kaptensbindel** |
| `dim_game` | match | hela serien, inte bara våra matcher |

**Fakta**

| fakta | korn | mått |
|---|---|---|
| `fact_player_game` | spelare × match | mål, assist, poäng, pim, on-ice för/emot, **skott, tekningar, officiellt +/−** |
| `fact_team_game` | lag × match | skott, räddningar, pim, pp%, pdo |
| `fact_goalie_game` | målvakt × match | skott emot, räddningar, sv%, **speltid, hållen nolla** |
| `fact_lineup_slot` | spelare × match × kedja | — |
| `fact_player_season` | spelare × säsong | Swehockeys totaler, som facit |
| `fact_standings_snapshot` | lag × säsong × dag | placering |

### 3.3 Nycklar

Normaliserade naturliga värden, inte hashade surrogat. Swehockey har inget
spelar-id, så namnet är den enda identitet som finns, och ett surrogat hade
bara lagt ett joinsteg mellan felsökaren och datat. Kontrollerat mot
HA 25/26: 541 truppspelare, **noll äkta namnkrockar**.

Tre normaliseringar som datat kräver:

- **`**` markerar övergång.** Säsongstabellerna skriver `Hellberg, Hannes**`
  för spelare som bytt klubb; matchtabellerna gör det inte. Utan strippning
  hittar en övergångsspelares matcher aldrig sin säsongsstatistik. 23 spelare
  i HA 25/26.
- **Lagkod finns bara i händelserna.** `dim_team` härleder kopplingen genom
  att knyta varje händelse till en spelare i matchens uppställning. 14 koder,
  noll tvetydiga.
- **PDF:erna skriver `EFTERNAMN Förnamn`**, HTML skriver `Efternamn, Förnamn`.
  Parsern skriver om, annars går raderna inte att joina.

### 3.4 Två plus/minus, med flit

`fact_player_game` bär både `official_plus_minus` (Swehockeys, ur rapporten)
och `plus_minus_on_ice` (vårt, ur händelsernas `Pos./Neg. Part.`). De skiljer
sig med ungefär sexton procent åt båda hållen, konsekvent, över hela säsongen.
Skillnaden beror på ett underlag vi inte ser på händelsesidan.

**Kalibrera inte bort skillnaden med en faktor.** De redovisas bredvid
varandra.

---

## 4. Vad Swehockey inte har

Kolumnerna står i mallen men är tomma genom hela serien, i **både SHL och
HockeyAllsvenskan**:

- **Hits**
- **Blocks**
- **Shifts**
- **Speltid för utespelare** — målvakter har den, utespelare inte

Försök inte rätta fram dem ur PDF:erna. De finns inte.

Längd, vikt, skytte och nationalitet finns inte heller i någon Swehockey-källa
och kommer från EliteProspects-länken.

Rapporten saknas dessutom för säsongens allra första matcher — två av
femtiotvå i både SHL och HA. `has_report` i `fact_player_game` skiljer
"noll skott" från "ingen rapport".

---

## 5. Endpoints

Alla under `/api/v1/`. `?season=<key>` väljer säsong, `?refresh=1` kringgår
sextimmarscachen.

| endpoint | ger |
|---|---|
| `seasons` | säsongsregistret, med vilka som har lagdata |
| `standings` | serietabellen |
| `statistics` | säsongsöversikt: poängliga, målvakter, facit |
| `players` | spelarlista med percentiler |
| `player/{namn}` | en spelares **fullständiga** matchlogg, situationer, sviter, kedjekompisar |
| `goalies` | målvakter med matchlogg |
| `roster` | truppen, med EliteProspects-länkar |
| `onice` | on-ice mål för och emot per spelare |
| `shots` | skottandel och PDO |
| `analytics` | sammansatta moduler för statistiksidan |
| `projection` | simulerad slutplacering (Monte Carlo, σ = 55) |
| `match/{game_id}` | matchrapport med spelarna på isen |
| `games/{game_id}/momentum` | målskillnad genom matchen |
| **`lines`** | **kedjornas utfall: mål för och emot med kedjan på isen** |
| **`table-history`** | **tabellplacering per omgång, härledd ur resultaten** |
| **`opponents`** | **facit per motståndare; `venue=home\|away`, `last=N`** |
| **`swings`** | **vändningar och tapp: ställning efter två perioder mot slutresultat** |
| `lovenlaget`, `silly-season`, `x-feed`, `financials` | innehåll utanför matchdatat |

### Spelarens matchlogg

`/api/v1/player/{namn}` bygger på `marts.fact_player_game` och täcker **alla**
matcher spelaren var med i. Tidigare kom loggen ur målhändelserna, så en
57-poängare fick 34 rader av 51 och en back med femton poäng nästan
ingenting. Nollmatcherna är halva bilden — utan dem går varken form, svackor
eller sviter att läsa.

Svaret bär `report_coverage`. Skott och tekningar finns bara för matcher med
matchrapport, och `has_report` på varje rad skiljer "noll skott" från "ingen
rapport". **Skjutprocenten räknas bara över matcher som har en rapport** —
säsongens alla mål delat med skotten från halva säsongen gav 91 %.

### Determinism

Listor som kapas vid topp N har deterministiska sekundärnycklar, med namnet
sist. BigQuery garanterar ingen radordning utan `ORDER BY`, och utan
sekundärnyckel gav samma anrop olika svar: `top_goalies` sorteras på spelade
matcher, och alla åtta bytta positioner var exakta oavgjorda.

---

## 6. Verifiering

Tre rutiner, alla körbara utan tillgång till BigQuery.

**`tests/marts_extract.py` + `tests/marts_validate.py`** skrapar en riktig
säsong, kör samma mart-SQL i DuckDB och jämför mot Swehockeys officiella
tabell. Facit för HA 25/26, Björklövens 33 utespelare: **noll avvikelser** på
mål, assist, poäng och utvisningsminuter.

Det testet hittade tre riktiga fel: tomma-mål-mål som målregexen tappade
(fjorton mål), straffavgöranden som saknades helt (fyra mål), och
assistgivare som blev utan lag.

**`tests/api_compare.py`** jämför varje endpoint fältvis mot ett facit i
`tests/api_baseline/`, med `refresh=1` så cachen inte döljer skillnader. Sex
fält som varierar mellan två identiska anrop är undantagna — uppmätt, inte
antaget. Kontrollen skiljer omsortering från verklig skillnad.

```bash
python3 tests/api_compare.py --save tests/api_baseline   # före ändring
python3 tests/api_compare.py --check tests/api_baseline  # efter deploy
```

`--save` skriver över versionshanterade filer; kör den bara när facit
medvetet ska flyttas fram, och committa resultatet.

**Avstämningen i scrapern** körs vid varje produktionskörning, se 2.5.

---

## 7. Att köra

```bash
bash deploy.sh api        # FastAPI till Cloud Run
bash deploy.sh scraper    # Cloud Function + en körning
bash deploy.sh views      # core- och marts-vyerna
bash deploy.sh schedule   # Cloud Scheduler
bash deploy.sh backfill   # hämta om avslutade säsonger
bash deploy.sh restore-env # återställ miljövariabler från äldre revision
```

Använd aldrig `--set-env-vars` i egna kommandon: den raderar allt som inte
står med. `X_BEARER_TOKEN` försvann den vägen och X-flödet slutade fungera.
Skripten använder `--update-env-vars`.
