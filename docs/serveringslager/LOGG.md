# Serveringslagret — logg

Läs det här först om du tar över. Planen med ID:n står i `PLAN.md`,
arkitekturen och skälen i `docs/HALLBAR_ARKITEKTUR.md`.

## Läget just nu

**Uppdaterad 2026-10-02.**

- **Gren:** `claude/hockey-app-frontend-redesign-5p5x1j` i båda repona,
  omstartad från `master` respektive `main` i dag. Allt serveringsarbete
  ligger där. Inget av det är i produktion.
- **Klart:** S1.2 (jämförelseverktyget). S1.1 (kandidatmiljön) är skriven
  men inte körd — den kräver `gcloud`, som bara finns i ägarens Cloud Shell.
- **Väntar på ägaren:** att köra `bash deploy.sh kandidat` en gång, så att
  kandidatflödet provas på riktigt. Se "Första provet" nedan.
- **Nästa:** S2.1 (loggrad per anrop). Får påbörjas nu — F1:s krav är att
  S1.1 och S1.2 finns, och det gör de.

### Utanför grenen, värt att veta

`139b80b` på `master` — `/statistics` kör sina fyra frågor parallellt —
pushades innan regeln om godkännande infördes. Om den är deployad går inte
att se utifrån: svaren är identiska oavsett, och en omräkning tog 2,3 s, vilket
passar båda fallen. Är den inte ute går den med nästa
`git pull origin master && bash deploy.sh api`. Föreslås bli första
ändringen som går genom kandidatflödet; se nedan.

`df31191` (preliminärt protokoll) **är** i drift — produktionens
matchrapporter bär `provisional` och `source_updated_at`.

### Första provet

Två saker blir prövade på en gång:

```bash
cd ~/loven-stats-backend && git fetch origin && git checkout claude/hockey-app-frontend-redesign-5p5x1j && git pull && bash deploy.sh kandidat
```

Grenen är i dag identisk med `master`. Det enda på `master` som kan vara
odeployat är `139b80b`, och den ändrar hur snabbt svaret byggs, inte vad det
innehåller. Jämförelsen ska därför vara helt grön. Är den det har både kandidatflödet
och parallelliseringen visat sig fungera, och ägaren kan välja att
promota.

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
