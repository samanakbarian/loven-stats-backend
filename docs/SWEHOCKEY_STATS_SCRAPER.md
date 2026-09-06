# Swehockey Stats Scraper

Cloud Function Gen2: `swehockey-stats-scraper`.

## Scope

Scrapar `stats.swehockey.se` med:

- `SWEHOCKEY_TEAM_ID` (default `1139`)
- `SWEHOCKEY_SEASON_GROUP_ID` (fallback `20961`, SHL 2026/27)

Datatyper:

- spelarstatistik
- målvaktsstatistik
- tabellställning
- matchschema

Scrapern hämtar först samtliga datatyper, kör kvalitetskontroller och publicerar
sedan endast om hela den kritiska kvalitetsgrinden passerar.

## GCS raw output

Path:

- `raw/web_scrapers/swehockey/<datum>/<run_id>/<season_group_id>/<typ>.json`

Objekten är immutable per körning och innehåller `run_id`, källa, käll-URL,
säsongsgrupp och `scraped_at`.

## BigQuery raw tables

Dataset: `raw_sports` (skapas automatiskt vid behov)

Tabeller:

- `swehockey_player_stats`
- `swehockey_goalie_stats`
- `swehockey_standings`
- `swehockey_schedule`

Write mode:

- `WRITE_APPEND` (deduplicering sker i dbt)

Metadata per rad:

- `run_id`
- `scraped_at` (TIMESTAMP)
- `source = "swehockey"`
- `source_url`

Kör- och kvalitetsmetadata lagras i:

- `raw_ops.ingestion_runs`
- `raw_ops.data_quality_runs`

## Deploy (Cloud Functions Gen2)

```bash
gcloud functions deploy swehockey-stats-scraper \
  --gen2 \
  --region=europe-west1 \
  --runtime=python311 \
  --source=functions \
  --entry-point=run_swehockey_stats_scraper \
  --trigger-http \
  --allow-unauthenticated \
  --memory=1024Mi \
  --timeout=300s \
  --set-env-vars="GCP_PROJECT=granskaren-d51a1,GCS_BUCKET=loven-stats-raw-data-prod,SWEHOCKEY_TEAM_ID=1139,SWEHOCKEY_SEASON_GROUP_ID=20961"
```

## Scheduler: fyra körningar om dygnet

```bash
bash deploy.sh schedule    # sätter eller uppdaterar jobbet
```

Schemat är `30 0,7,18,22 * * *` i Europe/Stockholm:

| tid | vad den fångar |
|---|---|
| 18:30 | eftermiddagsmatcher (avslag 15:15–16:00) |
| 22:30 | kvällsmatcher (avslag 19:00) |
| 00:30 | sena avslag och matcher som drog ut |
| 07:30 | rättelser som kom under natten, och tabellen inför dagen |

### Varför just de tiderna

Swehockey publicerar matchrapporten en stund efter slutsignal. Mätt över arton
matcher i HA 25/26, ur sidornas egna `Last update`-stämplar, låg rapporten uppe
**137–195 minuter efter nedsläpp** — ungefär en kvart till tre kvart efter
slutsignal. Åtta av matcherna fick dessutom senare rättelser, oftast inom en
vecka, i ett fall efter sexton dagar.

Med avslag 15:15, 16:00, 19:00 och 20:30 räcker det alltså med en körning
runt tre timmar efter varje vanligt avslag. Det tidigare schemat var
`0 6 * * 1` — en gång i veckan, måndag morgon. En tisdagsmatch hann bli sex
dygn gammal innan siffrorna dök upp.

Fyra körningar kostar nästan ingenting nu när matchsidorna hämtas
inkrementellt: en körning utan nya matcher rör bara schema, tabell,
spelarstatistik och trupp.

## Inkrementell hämtning

Matchsidorna (`/Game/Events/`, `/Game/LineUps/`) hämtas en match i taget och är
det enda som skalar med antalet matcher. Körningen frågar därför först
BigQuery vilka `game_id` som redan finns:

```sql
SELECT DISTINCT game_id FROM swehockey_game_events WHERE season_group_id IN (...)
```

och hämtar bara

- matcher som saknas, och
- matcher spelade inom `SWEHOCKEY_REFRESH_DAYS` (21 dagar), eftersom rapporten
  kan ha rättats i efterhand.

`events_limit` är kvar som säkerhetsventil, inte som urvalsregel.
`?events_limit=all` stänger av filtret helt och tar säsongens alla matcher —
det är vad backfill gör.

### Skriv bara när rapporten faktiskt ändrats

Tabellerna är append-only, så en omhämtning **lägger till** rader — den ersätter
inga. Att bara begränsa vilka matcher som hämtas räcker därför inte: med fyra
körningar om dygnet i 21 dagar hade varje match kunnat få 84 generationer av
samma rader. Talen hade blivit rätt ändå, eftersom läsningarna deduplicerar på
`MAX(scraped_at)`, men bara så länge varje läsning kommer ihåg det. Det var
precis det antagandet som brast när utvisningarna tredubblades.

Varje match får därför en `content_hash`: en SHA-1 över matchens parsade rader,
per tabell, med härstamningsfälten (`scraped_at`, `run_id`, `source_url`)
undantagna. Körningen läser hashen för den senaste generationen

```sql
SELECT game_id,
       ARRAY_AGG(content_hash ORDER BY scraped_at DESC LIMIT 1)[OFFSET(0)] AS h
FROM <tabell> WHERE season_group_id IN (...) GROUP BY game_id
```

och hoppar över matchen om den nya hämtningen ger samma hash. En match som inte
ändrats kostar alltså en HTTP-hämtning men noll rader. Antalet generationer per
match blir därmed antalet gånger Swehockey faktiskt rättat rapporten — mätt över
en säsong en till tre, inte 84.

Det gäller även backfill: en omkörning mot en avslutad säsong skriver bara de
matcher vars innehåll skiljer sig, vilket är exakt vad man vill efter en
parserrättning.

### Ögonblicksbilderna, som är den tunga delen

Matchtabellerna är inte där volymen ligger. Schema, tabell, trupp och
spelarstatistik hämtas som en **hel bild per säsongsgrupp** och skrevs om i sin
helhet vid varje körning:

| tabell | rader per säsongsgrupp |
|---|---|
| `swehockey_schedule` | 364 |
| `swehockey_roster` | 541 |
| `swehockey_player_stats` | 509 |
| `swehockey_goalie_stats` | 47 |
| `swehockey_standings` | 14 |
| **summa** | **1 475** |

Med två aktiva säsongsgrupper och fyra körningar om dygnet blir det 11 800
rader om dygnet, drygt **fyra miljoner om året**, där de allra flesta är
ordagranna kopior. Bilden ändras i praktiken bara när en match spelats.

Samma hash gäller därför dem, fast en per `(tabell, säsongsgrupp)` i stället för
per match. Är bilden oförändrad hoppas både GCS-blobben och BigQuery-laddningen
över. Kvalitetsgrinden körs ändå — det som hämtats valideras, oavsett om det
skrivs.

Under säsong skrivs de alltså ungefär två gånger i veckan, inte tjugoåtta.

### Första körningen efter driftsättning

Kolumnen `content_hash` finns inte i tabellerna än. Hash-frågan misslyckas då,
loggas som `INFO`, och allt skrivs om en gång — varpå kolumnen finns och nästa
körning kan jämföra. `deploy.sh` skriver ut `oförändrad, inget skrivet` för de
datatyper som hoppats över.

## Avstämning efter varje körning

Kvalitetsgrinden kontrollerar **form**: att rader finns, att fält är ifyllda,
att nycklar är unika. Den säger ingenting om **värden**. När utvisningarna
tredubblades var varje rad välformad — det fanns bara tre generationer av dem,
och en läsning som glömt avduplicera summerade alla tre.

`_reconcile()` jämför i stället tal som måste gå ihop, hämtade från olika håll
i datalagret:

| kontroll | jämför |
|---|---|
| `events_goals_match_results` | mål i händelselistan mot mål i matchresultaten |
| `summary_two_rows_per_game` | matchsammanfattningen ska ha två lagrader per match |
| `goalie_saves_plus_goals_equal_shots` | räddningar + insläppta = skott emot |
| `penalties_events_match_summary` | händelsernas utvisningsminuter mot rapportens PIM |
| `summary_shots_match_goalie_shots_against` | lagens skott mot målvakternas skott emot |

Avstämningen **fäller aldrig en körning** — datat är redan skrivet när den
körs. Avvikelser hamnar i svaret (`reconciliation`, `reconciliation_failed`), i
`raw_ops.ingestion_runs` och som `ERROR` i Cloud Logging. `deploy.sh scraper`
och `deploy.sh backfill` skriver ut dem.

Larma på detta i Cloud Logging:

```
resource.type="cloud_run_revision"
severity=ERROR
textPayload:"Avstamningen gick inte ihop"
```

## dbt

Staging:

- `stg_successful_ingestion_runs`
- `stg_swehockey_player_stats`
- `stg_swehockey_goalie_stats`
- `stg_swehockey_standings`
- `stg_swehockey_schedule`

Source + freshness/tests:

- `models/staging/core/_core_sources.yml`
- `models/staging/core/schema_ops.yml`

Freshness:

- `warn_after: 3h`
- `error_after: 12h`

Facts integration:

- `stg_swehockey_player_stats` -> `fact_event_players`
- `stg_swehockey_goalie_stats` -> `fact_goalie_game_stats`
- `stg_swehockey_standings` -> `fact_team_standings_snapshot`

## Driftstatus 2026-06-14

- Deployad Gen2-revision: `swehockey-stats-scraper-00011-hot`
- Region: `europe-west1`
- Scheduler-jobb aktivt: `swehockey-stats-scraper-job`
- Direkt produktionsanrop: HTTP 200, 756 laddade rader, 0 fel.
- Manuellt scheduler-anrop: HTTP 200, 756 laddade rader, 0 fel.
- Aktiva ingestion-grupper:
  - SHL 2026/27: `20961`
  - HockeyAllsvenskan 2026/27: `20962`
- Spelar- och målvaktsstatistik är ännu tom före säsongsstart och loggas därför
  som `WARNING`, inte som blockerande fel.
- Tabell och schema passerar kritiska kontroller.

## On-ice: vad `Pos. Part.` ger, och vad den inte ger

Målcellen på `/Game/Events/{game_id}` bär `Pos. Part.` och `Neg. Part.` — de
spelare som stod på isen för det görande respektive det släppande laget.
Scrapern lägger dem i `on_ice_for` och `on_ice_against` som tröjnummer, och
`/api/v1/onice` räknar mål för och emot per spelare ur dem.

### Talen är inte tabellens plus/minus

Swehockeys spelartabell har egna kolumner `+`, `-` och `+/-`. De stämmer inte
med vad `Pos. Part.` ger. Mätt över hela HA 25/26, Björklövens 27 utespelare:

| | mina summor | Swehockeys | kvot |
|---|---|---|---|
| `+` | 606 | 704 | 1,162 |
| `-` | 318 | 366 | 1,151 |

Avvikelsen är konsekvent åt båda hållen, ungefär 16 procent. Den beror alltså
inte på att fel situationer räknas — plus- och minussidan skulle då dra åt
olika håll. För enskilda spelare stämmer ena sidan ibland exakt: Cronholm har
`-` 19 i tabellen och 19 hos oss, men `+` 50 mot våra 40.

Vad som prövats utan att förklara skillnaden:

- bara mål vid lika styrka
- lika styrka plus mål i underläge (närmast, medelavvikelse 1,71 mot 2,50)
- alla situationer, och varianter där för- och emotsidan räknas olika
- straffslagsmål in- och exkluderade

Slutsatsen är att Swehockey räknar on-ice på ett underlag vi inte ser på
händelsesidan. **Kalibrera inte bort skillnaden med en faktor.** Talen
redovisas i stället bredvid varandra: `gf_on`/`ga_on`/`diff` från händelserna,
och `official_plus_minus` från tabellen.

### Vad våra tal ändå ger som tabellens inte gör

- uppdelning på lika styrka mot alla situationer
- andel av lagets mål spelaren var med på
- vilka par som oftast står på isen tillsammans när laget gör mål
- samma nedbrytning per match

### Slutspelet saknar match-id

Swehockey länkar inte matcherna från slutspelssidan, och varken `Overview`,
`GameCenter` eller `Live` för samma säsongsgrupp har `/Game/Events/`-länkar.
Grundserien har 364 länkar, slutspelet noll. Slutspelsmatcher får därför
varken matchrapport eller händelser.


## Slutplaceringsmodellen: hur intervallen kalibrerades

`/api/v1/projection` räknar Elo ur spelade matcher och simulerar resten. Poäng
enligt svensk praxis: 3 för vinst i ordinarie tid, 2 efter förlängning, 1 för
förlust efter förlängning. Hur ofta matcher går till förlängning hämtas ur
säsongens egna matcher, inte antas.

En simulering som behandlar Elo som exakt känd ger **för smala intervall**.
Backtest mot HA 25/26 vid fyra tidpunkter, med facit inom p10–p90 (ett
80-procentsintervall ska träffa ≈11,2 av 14 lag):

| ratingosäkerhet σ | efter 91 | 182 | 260 | 320 | snitt |
|---|---|---|---|---|---|
| 0 (ingen) | 9 | 11 | 9 | 10 | 9,8 |
| 25 | 9 | 12 | 9 | 10 | 10,0 |
| 40 | 9 | 13 | 11 | 10 | 10,8 |
| **55** | 9 | 13 | 12 | 11 | **11,2** |
| 70 | 11 | 13 | 12 | 11 | 11,8 |

σ = 55 träffar målet. Varje simulering drar därför en egen Elo per lag ur en
normalfördelning kring skattningen.

Kalibreringen bygger på en säsong, 56 observationer. Den bör göras om när fler
säsonger finns i datalagret — höj inte σ på känsla.

### Före seriestart säger modellen ingenting

Utan spelade matcher delar alla lag rating 1500, och siffrorna beskriver bara
spelschemat. Svaret bär ett `reliability`-fält: `none` utan spelade matcher,
`low` under fyra omgångar, annars `ok`. Frontend ska säga det rakt ut i
stället för att visa en prognos som ser mer sannolik ut än den är.

## Lagren: raw_sports → core → (marts)

```
raw_sports   append-only historik, en generation per faktisk ändring
   ↓         avdupliceras en gång, i vy
core         aktuellt tillstånd — det enda API:t får läsa
   ↓         (ännu inte byggt)
marts        stjärnschema: dim_* och fact_*
```

### core

`bash deploy.sh views` skapar dem ur `sql/core_views.sql` och skriver ut hur
många rader som sorteras bort per tabell.

| vy | bastabell | avdupliceras på |
|---|---|---|
| `core.game_events` | `swehockey_game_events` | `game_id` |
| `core.game_team_summary` | `swehockey_game_summary` | `game_id` |
| `core.game_goalies` | `swehockey_game_goalies` | `game_id` |
| `core.game_lineups` | `swehockey_game_lineups` | `game_id` |
| `core.schedule` | `swehockey_schedule` | `season_group_id` |
| `core.standings` | `swehockey_standings` | `season_group_id` |
| `core.player_season_stats` | `swehockey_player_stats` | `season_group_id` |
| `core.goalie_season_stats` | `swehockey_goalie_stats` | `season_group_id` |
| `core.roster` | `swehockey_roster` | `season_group_id` |
| `core.season` | `swehockey_seasons` | — (MERGE-hanterad) |
| `core.standings_history` | `swehockey_standings` | `season_group_id` + dag |

Nyckeln följer hur scrapern skriver: matchtabellerna en hel match i taget,
ögonblicksbilderna en hel bild i taget. `QUALIFY scraped_at = MAX(scraped_at)
OVER (PARTITION BY ...)` behåller **hela** den senaste generationen —
`ROW_NUMBER` hade behållit en rad per match, vilket är fel när en match har
tjugo händelser.

### Varför lagret finns

Avdupliceringen har missats två gånger: utvisningarna tredubblades när
analysfrågan summerade tre skörningar, och spelarnas matchlogg multiplicerade
poäng likadant. Ingen rad var trasig — bara läsningen. Med `core` går felet
inte att göra, och avstämningskontrollerna blir ett skyddsnät i stället för
enda försvaret.

### Tabellplacering över tid kan inte hämtas ur historiken

`core.standings_history` fungerar framåt, men **inte bakåt**. Avslutade
säsonger har skrapats om i efterhand, och varje sådan generation bär
sluttabellen med ett färskt `scraped_at`. För redan spelade säsonger måste
ställningen i stället härledas ur matchresultaten i `core.schedule` — vilket
är exakt och går att göra för alla lag och alla omgångar.

## marts: stjärnschemat

`bash deploy.sh views` bygger `core` och `marts` i den ordningen.

| dimension | korn |
|---|---|
| `dim_season` | säsongsgrupp (grundserie och slutspel var för sig) |
| `dim_team` | lag |
| `dim_player` | spelare |
| `dim_game` | match — hela serien, inte bara våra matcher |

| fakta | korn | mått |
|---|---|---|
| `fact_player_game` | spelare × match | mål, assist, poäng, pim, on-ice för/emot |
| `fact_team_game` | lag × match | skott, räddningar, pim, pp%, pdo, mål för/emot |
| `fact_goalie_game` | målvakt × match | skott emot, räddningar, insläppta, sv% |
| `fact_lineup_slot` | spelare × match × kedja | — |
| `fact_player_season` | spelare × säsong | Swehockeys egna totaler, som facit |
| `fact_standings_snapshot` | lag × säsong × dag | tabellplacering |

### Nycklar

Nycklarna är normaliserade naturliga värden, inte hashade surrogat. Swehockey
har inget spelar-id, så namnet är den enda identitet som finns, och ett
surrogat hade bara lagt ett joinsteg mellan felsökaren och datat. Kontrollerat
mot HA 25/26: 541 truppspelare, **noll äkta namnkrockar**.

Säsongstabellerna märker spelare som bytt klubb under säsongen med `**` —
`Hellberg, Hannes**`. Matchtabellerna gör det inte. Markören strippas i
`dim_player` och `fact_player_season`; utan det hittar en övergångsspelares
matcher aldrig sin säsongsstatistik. 23 spelare i HA 25/26.

Lagkoden (`IFB`, `MoDo`) finns bara i händelsetabellen, lagnamnet bara i de
övriga. `dim_team` härleder kopplingen: knyt varje händelse till en spelare i
matchens uppställning, och därmed till lagnamnet. Över HA 25/26 gav det 14
koder, noll tvetydiga.

### fact_player_game är hela poängen

Swehockey ger säsongstotaler och en händelselista — aldrig raden däremellan.
Nästan varje fråga appen ställer om form, motståndare eller kedjor behöver just
det kornet, och det räknades tidigare fram på nytt i Python vid varje anrop.

### Validering

`tests/marts_extract.py` + `tests/marts_validate.py` kör samma SQL i DuckDB mot
en riktigt skrapad säsong och jämför mot Swehockeys officiella tabell. Facit
för HA 25/26, Björklövens 33 utespelare:

| | avvikelser |
|---|---|
| mål | 0 av 33 |
| assist | 0 av 33 |
| poäng | 0 av 33 |
| utvisningsminuter | 0 av 33 |

`in_lineup` betyder "stod i uppställningen", inte "spelade". Swehockeys
uppställningssida listar 20–22 spelare per lag där 22 klätt om, och utelämnar
ibland målvakten. Använd `fact_player_season.games_played` för antal spelade
matcher.

### Vad avstämningen faktiskt fångade

Första skarpa körningen gav två avvikelser. Båda var riktiga, och den ena var
kontrollens eget fel:

**`events_goals_match_results` 289 mot 283.** Kontrollen drog bort ett mål för
varje match avgjord på straffar, eftersom avgörandet inte fanns i
händelselistan. Sedan lärde sig parsern att läsa `Game Winning Shot` — och då
räknades målen bort två gånger. Avdraget är borttaget.

**`summary_shots_match_goalie_shots_against` 13 mot 0.** Inte ett fel: ett
skott i tomt mål räknas för laget men mot ingen målvakt. Skillnaden per match
är exakt antalet mål i tomt mål. Mätt över HA 25/26: tretton matcher med
skillnad, alla tretton med tomma-mål-mål, och skillnaden lika med antalet
sådana mål i var och en. Kontrollen jämför nu mot det talet i stället för mot
noll.

Poängen med en avstämning är just det här — den påstår ingenting om att datat
är rätt, den visar var två oberoende tal inte går ihop, och sedan får man
förklara varför.
