# Hållbar arkitektur

Skriven 2026-10-02, efter två veckors SHL i drift. Bygger vidare på backlogg
33 och ersätter den inte — 33 ställde diagnosen före premiären, det här är
planen nu när det finns verklig last att utgå från.

## Svaret kort

**Datalagret håller. Läsvägen gör det inte.**

Det som skriver data skalar med hur mycket som *ändras*. Det som läser data
skalar med hur många som *tittar* — och med hur många serverinstanser som
råkar vara kalla. Det första är rätt. Det andra blir dyrare och långsammare
precis när sajten lyckas.

## Det som håller

**Skörden skriver bara det som ändrats.** Matchtabellerna hoppar över
oförändrade matcher på innehållshash (`_unchanged`), ögonblicksbilderna —
tabell, schema, trupp, spelarstatistik — gör samma sak per säsongsgrupp
(`_snapshot_unchanged`), och protokollens PDF:er hämtas villkorligt med ETag.
Hasharna läses ur BigQuery i början av varje körning, så jämförelsen
överlever att Cloud Functions startar kallt. Innan det infördes skrevs 11 800
identiska rader om dygnet; nu skrivs en generation när en match spelats eller
rättats.

**Råtabellerna är append-only och vyerna avduplicerar.** En rättelse hos
Swehockey slår igenom överallt vid nästa skörd utan att något byggs om. Det
har hänt på riktigt två gånger: Djurgårdens skott i premiären och
premiärprotokollet igen nio dagar senare.

**Tillväxten är inte ett problem.** Med förändringsdetekteringen blir det
några hundra generationer per säsong av ögonblicksbilderna, alltså några
hundra tusen rader. BigQuery märker det inte. Partitionering vore snyggt men
löser ingenting som gör ont i dag.

**Det finns skyddsnät.** Gyllene mästaren (`tests/gyllene_master.py`)
jämför svar mot en sparad baslinje, röktestet kör tre gånger om dygnet, och
avstämningen i körningen (backlogg 46) jämför härledda tal mot källans egna.

## Det som inte håller

### 1. BigQuery står i svarsvägen

BigQuery är en analysmotor. Varje fråga har en fast kostnad på ett par
tiondelar för jobbskapande och planering, oavsett hur lite den läser. Mätt:

| Anrop | Kall | Varm |
|---|---|---|
| `/statistics` (fyra frågor i följd, före 2 okt) | 4,02 s | 0,76 s |
| `/player/{namn}` | 2,0–3,5 s | 0,2–0,5 s |
| ny instans som startar från noll | 20–40 s | — |

Framför BigQuery sitter bara en cache **i processminnet, per instans**. Den
försvinner när instansen skalas ned och delas inte mellan instanser. Därav:

- **Värmningsjobbet var tionde minut** — det finns bara för att hålla en
  cache vid liv som sitter på fel ställe.
- **Två instanser kan visa olika siffror.** Under en förmiddag i september
  visade tabellen den rättade siffran medan spelarsidan visade den gamla.
- **Kostnaden växer med besökarna.** Vid åttio besökare om dygnet märks det
  inte. En kväll när någon delar sajten brett startar tre instanser med tom
  cache och ställer samma frågor samtidigt.
- **`--min-instances 1`** skulle dölja symtomet för sju dollar i månaden, men
  inte ta bort det.

### 2. En fil på 6 952 rader

`api/main.py` rymmer 29 endpoints, cachning, taktbegränsning, CORS och all
affärslogik. Varje ändring rör samma fil. Två buggar den här säsongen satt i
hjälpfunktioner som delades av flera endpoints utan att det syntes — tabellens
skiljetal låg i `_league_table` och slog igenom på tre ställen.

### 3. Varje svar finns i två versioner

Värmningen, cachen och API:t räknar samma svar på var sitt sätt i tiden.
Ingenting i arkitekturen säger att ett svar är *färdigt*. Preliminärmarkören
från 2 oktober är ett lapptäcke över just det.

## Målet: svar som byggs en gång, efter skörden

```
skörd (Cloud Function)
  │  vet redan vilka matcher och säsonger som ändrats — innehållshasharna
  ▼
förberäkning (eget steg, efter skörden)
  │  bygger svaren för det som ändrats, med SAMMA kod som API:t använder
  ▼
GCS: serving/v{N}/match/{id}.json
     serving/v{N}/sasong/{nyckel}/statistics.json
     serving/v{N}/sasong/{nyckel}/standings.json   …
  ▼
API:t läser bloben — faller tillbaka på BigQuery när den saknas
  ▼
frontend (oförändrad)
```

Det som följer av det:

- **Antalet BigQuery-frågor slutar växa med besökarna.** De ställs fyra gånger
  om dygnet av förberäkningen, inte en gång per kall instans.
- **Värmningsjobbet kan tas bort.** En blob behöver ingen värmning.
- **Alla instanser ser samma svar**, eftersom de läser samma fil.
- **En spelad match är i praktiken oföränderlig.** Bloben byggs om bara när
  hashen ändras, alltså vid en rättelse.

Prejudikatet finns redan: `/api/v1/feed` läses ur en blob i GCS och svarar på
0,85 s med bearbetning inräknad, i samma container som `/statistics`.

### Tre regler som gör det hållbart

**En byggare per svar.** Funktionen som bygger matchrapportens svar anropas
både av förberäkningen och av API:ts reservväg. Då kan de inte glida isär,
och det finns bara ett ställe att rätta en bugg på.

**Versionen i sökvägen.** `serving/v3/...`. Ändras svarets form — en ny
kolumn, en rättad beräkning — höjs versionen och allt byggs om. Gamla blobar
blir liggande oanvända i stället för att servera fel form. Det löser problemet
backlogg 33 pekade ut, att en innehållshash inte märker när *koden* ändrats.

**Varje blob bär sin egen ålder.** `generated_at` och Swehockeys
`source_updated_at` följer med till frontend. Röktestet larmar om en blob är
äldre än sex timmar under en matchdag. Blobar som tyst slutar uppdateras är
den största risken med det här, och den ska synas utifrån.

## Migreringen

Varje etapp går att deploya ensam, går att backa genom att ta bort bloben,
och kräver att gyllene mästaren visar identiska svar före och efter.

### Etapp 0 — Mät

Strukturerad loggrad per anrop: endpoint, cache träff eller miss, millisekunder,
antal BigQuery-frågor. Utan den går det inte att visa att något blev bättre,
och dimensioneringen av resten ska styras av verkliga tal, inte av gissningar.
En matchkväll räcker som mätunderlag.

### Etapp 1 — Matchrapporten

Backlogg 33 valde rätt: `/api/v1/match/{id}` är dyrast per anrop, har en
cachenyckel per match så missen är normalfallet, och en spelad match ändras
inte. Den vinner mest och är enklast att göra statisk.

- Bryt ut byggaren ur `get_match` till `api/svar/match.py`.
- Förberäkningen skriver `serving/v1/match/{id}.json` för varje match vars
  innehållshash ändrats i körningen.
- `get_match` läser bloben först.
- Kör förberäkningen som ett eget steg efter skörden, inte inuti den —
  skrapern har 300 sekunders tidsgräns och ska inte dela den.

**Klart när:** en kall matchrapport svarar under en halv sekund, och gyllene
mästaren är identisk för alla matcher i HA 25/26 och SHL 26/27.

### Etapp 2 — Säsongssvaren

`statistics`, `standings`, `players`, `goalies`, `lines`, `onice`, `shots`,
`analytics`, `next-match`, `table-history`. En blob per säsong och endpoint,
ombyggd när säsongens ögonblicksbild ändrats.

**Klart när:** värmningsjobbet är borttaget och ingen säsongssida tar mer än
en sekund kall.

### Etapp 3 — Spelarprofilerna

En blob per spelare och säsong. Ett trettiotal per säsong; byggs om efter
varje skörd där spelarens lag spelat.

### Etapp 4 — `main.py` i delar, längs vägen

Inte som ett eget projekt. Varje endpoint som flyttar till en blob flyttar
samtidigt sin byggare till `api/svar/`. När etapp 3 är klar är `main.py`
routing, mellanlager och reservvägar — några hundra rader.

### Etapp 5 — Valfritt: servera blobarna direkt

När allt läsbart är blobar kan frontend hämta dem från en publik bucket bakom
Cloud CDN, utan att gå via Cloud Run alls. Då försvinner kallstarten helt.
Gör det bara om etapp 0–3 visar att Cloud Run fortfarande är flaskhalsen.

## Medvetet inte

- **Memorystore eller Redis.** Löser delningen mellan instanser men kostar
  dygnet runt och tar inte bort kostnaden per fråga.
- **En serveringsdatabas (Postgres).** Datan är oföränderlig efter att
  matchen spelats. Filer passar den bättre än en databas som måste hållas
  igång.
- **Skriva om från början.** Datalagret är det svåra och det fungerar.
- **dbt nu.** Det har aldrig körts i produktion. En migration dit är ett eget
  spår och ska inte blandas ihop med läsvägen.
- **Partitionering av råtabellerna.** Inte förrän en mätning visar att
  skanningen kostar något. I dag gör den inte det.

## Risker

- **Tyst inaktuella blobar.** Hanteras med `generated_at` i varje svar och
  ett larm i röktestet. Det är den viktigaste punkten i hela planen.
- **Förberäkningen fallerar halvvägs.** Varje blob skrivs för sig; en misslyckad
  match lämnar den gamla bloben kvar och API:t faller tillbaka på BigQuery för
  den som saknas. Inget blir sämre än i dag.
- **Två implementationer glider isär.** Förhindras av regeln om en byggare per
  svar, och kontrolleras av gyllene mästaren i varje etapp.
