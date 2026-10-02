# Serveringslagret — logg

Läs det här först om du tar över. Planen med ID:n står i `PLAN.md`,
arkitekturen och skälen i `docs/HALLBAR_ARKITEKTUR.md`.

## Läget just nu

**Uppdaterad 2026-10-02, kväll.**

- **Gren:** `claude/hockey-app-frontend-redesign-5p5x1j` i båda repona.
- **Klart:** S1.1, S1.2, och rättelsen av matchrapportens lagkoder.
- **Andra kandidaten granskad och redo:** 72 av 80 svar identiska med
  produktion, och de 8 som skiljer gör det bara i `team_codes`, i exakt det
  mönster rättelsen förutsade. Se posten nedan.
- **Väntar på ägaren:** beslut om `bash deploy.sh promote`. Det blir första
  gången `promote` körs skarpt. Kandidaten innehåller rättelsen och
  `139b80b`; båda verifierade.
- **Efter promote:** kör jämförelsen igen mot produktion själv med tom cache
  — den ska då vara helt grön, eftersom alla instanser fått samma ordning.
- **Nästa för mig:** S2.1 (loggrad per anrop).

### Utanför grenen, värt att veta

`master` är orörd sedan regeln om godkännande infördes. Grenen ligger två
commits före: F1-arbetet och lagkodsrättelsen. Vid promote körs koden från
grenen i produktion medan `master` ligger efter — det ska slås ihop till
`master` direkt efter, med ägarens godkännande, annars lägger nästa
`deploy.sh api` från `master` tillbaka den gamla ordningen.
---

## 2026-10-02, kväll

### Andra kandidaten — med rättelsen

Ägaren körde `deploy.sh kandidat` igen. Taggen `kandidat` pekar nu på den nya
revisionen; adressen är densamma. Kontrollerat först att rättelsen var med:
de tre matcher som tidigare gav motståndaren först svarar nu
`['IFB', 'DIF']`, `['IFB', 'HV71']`, `['IFB', 'SSK']`.

```
72 lika, 8 olika, 0 fel av 80
```

Inga skillnader utom `team_codes` — kontrollerat genom att filtrera bort dem
ur alla skillnadsrader: tomt. Och i alla åtta står produktionen med
motståndaren först och kandidaten med vårt lag först:

```
match/1005620  AIK   match/1005793  AIK   match/1005814  AIK   match/1005930  AIK
match/1005627  SSK   match/1005786  SSK   match/1005852  SSK   match/1005952  SSK
```

Att det är just AIK och SSK är hashfröet: med den produktionsinstans som
svarade hamnar 'AIK' och 'SSK' före 'IFB' i mängdens ordning. Första
kandidaten hade ett annat frö och gav ett annat mönster. Det är felet som
syns, inte rättelsen — och precis det utfall som förutsades innan körningen.

Kandidaten är granskad. Promote är ägarens beslut.

---

## 2026-10-02, eftermiddag

### Första kandidaten: `loven-stats-api-00170-tad`

Ägaren körde `deploy.sh kandidat`. Revisionen gick ut med noll procent av
trafiken, och kandidatens adress lästes rätt ur `status.traffic`:
`https://kandidat---loven-stats-api-ttpybm4dva-ew.a.run.app`. S1.1 är därmed
provad skarpt. `promote` och `backa` är fortfarande oprovade.

Ägarens skärm visade också avstämningsfilen från körningen (backlogg 46):
24 spelade matcher i 20961, alla med händelser, noll spelare som skiljer.

### Jämförelsen mot produktion

```
shl_2627  17 svar   13 lika   4 olika   0 fel
ha_2526   64 svar   34 lika  30 olika   0 fel
```

Alla 34 skillnader var samma sak, och inget annat skilde:

```
/api/v1/match/1109944   .team_codes[0]: 'IFB' → 'DIF'
                        .team_codes[1]: 'DIF' → 'IFB'
```

Elva säsongsendpoints identiska i båda säsongerna. Kontrollerat genom att
lista varje skillnadsrad och filtrera bort `team_codes`: tomt.

### Orsaken

`get_match` byggde lagkoderna ur en mängd:

```python
codes = [c for c in {e.get("team_code") for e in events} if c]
```

Python slumpar strängarnas hashvärden per process (`PYTHONHASHSEED`), och en
mängds ordning följer hashvärdet. Varje Cloud Run-instans svarade därför med
sin egen ordning för samma match. Felet har funnits sedan fältet infördes.
Ingen klient läser `team_codes`, så det har inte syntes — men ett svar som
beror på vilken server som svarade går varken att förberäkna eller jämföra.

Självtestet i förmiddags missade det, eftersom båda sidor läste samma cache.
Omräkningsprovet missade det, eftersom `match/{id}` saknar `refresh`. Det
var precis den begränsningen loggen pekade ut, och kandidaten — som startar
med tom cache och eget hashfrö — var det som täckte den.

Övriga mängder i `api/` söktes igenom. En till finns, `season_ids` i
`get_statistics`, men den håller heltal (vars hashvärde är talet självt, alltså
samma i alla processer) och går bara in i SQL, aldrig i ett svar.

### Rättelsen

`_lagkoder(events)`: vårt lag först, sedan resten i bokstavsordning.
`tests/test_lagkoder.py` kör funktionen i tolv processer med olika
`PYTHONHASHSEED` — det som skiljer två instanser åt — och kräver samma svar
i alla. Samma prov körs mot den gamla koden och kräver att den INTE ger
samma svar, så att testet bevisar något.

```
4 av 4 gick igenom          tests/test_lagkoder.py
13 av 13 gick igenom        tests/test_jamfor_svar.py
```

Svarets form ändras inte. Ordningen blir fast: `['IFB', motståndaren]`.

### Lärdom för resten av planen

Varje story i F4–F6 ska provas med kandidat, inte bara mot produktion med
sig själv. Ett svar kan vara stabilt inom en process och ändå skilja mellan
två — och det är just två processer serveringslagret består av:
förberäkningen och API:t.

---

## 2026-10-02

### S1.2 Jämförelseverktyget — klart

`tests/jamfor_svar.py` jämför två API-adresser svar för svar: säsongens
elva endpoints och varje spelad match. Tal med tolerans, listor strikt i
ordning, tidsstämplar bortplockade (listan står i `IGNORERA`, med skäl).
Håller sig på hundra anrop i minuten räknat över båda adresserna, under
taktbegränsningens 120.

Enhetstester, `tests/test_jamfor_svar.py`:

```
13 av 13 gick igenom
```

De täcker bland annat att en ändrad ordning i en lista fångas (tabellens
skiljetalsbugg), att `True` inte räknas som `1`, och att tidsstämplar
plockas bort på alla nivåer men att data lämnas orörd.

Validering mot produktion, produktion mot sig själv:

```
shl_2627: 5 spelade matcher    17 lika, 0 olika, 0 fel av 17
ha_2526: 52 spelade matcher    64 lika, 0 olika, 0 fel av 64
```

Det bevisar att verktyget inte ger falsklarm, men inte att en ny instans
räknar fram samma svar — båda sidor läste samma cache. Därför också ett
prov med omräkning på ena sidan (`refresh=1`, tre av tolv i timmen):

```
lika  standings?season=shl_2627    0 skillnader
lika  statistics?season=shl_2627   0 skillnader
lika  lines?season=ha_2526         0 skillnader
```

Omräkningen är deterministisk. Det är förutsättningen för att jämföra en
kandidat med tomma cachar mot produktion.

**Begränsning:** `match/{id}` har ingen `refresh`, så omräkningsprovet täcker
inte matchrapporten. Det får kandidaten visa — den startar med tom cache.

### S1.1 Kandidatmiljön — skriven, ej körd

Tre nya mål i `deploy.sh`:

- `kandidat` — `gcloud run deploy --no-traffic --tag kandidat`, skriver ut
  kandidatens adress och kommandot för att jämföra.
- `promote` — vägrar om kandidaten inte är senaste revisionen, märker den
  nuvarande som `forra`, flyttar trafiken med `--to-latest`.
- `backa` — flyttar trafiken till `forra`.

En fälla hanterad: efter `update-traffic --to-tags` är trafiken låst, och då
får en vanlig `deploy.sh api` ingen trafik alls — den går ut och ingenting
ändras. `promote` använder därför `--to-latest`, och `deploy.sh api` slutar
nu med `update-traffic --to-latest` så att den släpper ett lås som `backa`
lämnat.

Verifierat här: `bash -n deploy.sh`, och de tre inbäddade JSON-tolkarna mot
en uppbyggd `gcloud run services describe`-utdata — kandidatens adress,
kandidat/senaste/nuvarande-raden, och fallet utan kandidat. **Inte
verifierat:** gcloud-anropen själva. Det kräver ägarens Cloud Shell.

Dokumenterat i `docs/DEPLOY.md` under "Kandidat före produktion".
