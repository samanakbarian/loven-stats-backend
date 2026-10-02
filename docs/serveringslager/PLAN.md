# Serveringslagret — plan

Arkitekturen och skälen står i `docs/HALLBAR_ARKITEKTUR.md`. Det här är
arbetet uppdelat i features, stories och underärenden. Läget och vad som
gjorts står i `LOGG.md` i samma mapp.

ID:na är stabila. Loggen och commit-meddelandena hänvisar till dem.

## Spelregler

**Inget prodsätts utan ägarens godkännande.** Konkret:

- **Allt arbete sker på grenen `claude/hockey-app-frontend-redesign-5p5x1j`**
  i båda repona. Aldrig direkt på `master` eller `main`. Frontend deployas
  automatiskt från `main`, och ägaren kör ofta `git pull origin master &&
  deploy.sh` för andra rättningar — halvfärdigt arbete på `master` hade gått
  ut med dem.
- **Backend provas som kandidat, inte i produktion.** `deploy.sh kandidat`
  lägger ut en Cloud Run-revision utan trafik, med en egen adress. Ägaren
  kör den. Produktion byts först med `deploy.sh promote`, som också är
  ägarens.
- **Blobläsningen är avslagen tills ägaren slår på den**, per endpoint, med
  miljövariabeln `SERVERING_BLOBBAR`. Att slå av den är att backa.
- **Frontend provas på Netlifys förhandsadress för grenen**, inte på
  `sida377.se`.

**Klart betyder** (gäller varje story):

1. Syntaxkontroll och typkontroll går igenom.
2. Nya rena funktioner har enhetstester som körts.
3. Jämförelseverktyget (S1.2) visar identiska svar mellan produktion och
   kandidat för allt storyn rör — eller varje skillnad är förklarad i loggen
   och godkänd.
4. Gyllene mästaren går igenom.
5. Loggen är uppdaterad.
6. Pushat till grenen. Inte till `master`, inte till `main`.
7. Ägaren har godkänt. Först då deployas eller promotas det.

---

## F1 — Grind och skyddsnät

Kommer först. Inget i F3–F6 får påbörjas innan S1.1 och S1.2 är klara, för
utan dem går det inte att visa att något blev rätt.

**S1.1 Kandidatmiljö på Cloud Run**
- a. `deploy.sh kandidat` — revision med `--no-traffic --tag kandidat`
- b. `deploy.sh promote` — flyttar all trafik till senaste revisionen
- c. `deploy.sh backa` — flyttar trafiken tillbaka till förra revisionen
- d. Dokumentera i `docs/DEPLOY.md`

**S1.2 Jämförelseverktyg för hela säsonger**
- a. Hämta alla matcher per säsong ur `/api/v1/statistics`
- b. Jämför två API-adresser endpoint för endpoint, med tolerans för flyttal
  och strikt ordning i listor
- c. Håll sig under taktbegränsningen, 120 anrop i minuten
- d. Rapport per endpoint, och felkod när något skiljer
- e. Validera verktyget självt: produktion mot produktion ska ge noll
  skillnader

**S1.3 Avstämning blob mot BigQuery**
- a. Sätt att tvinga reservvägen för en enskild förfrågan, taktbegränsat
- b. Verktyget jämför blob mot reservväg för samma entitet

**S1.4 Larm för gamla blobar**
- a. Röktestet läser `generated_at` och larmar om en blob är för gammal
  under en matchdag

## F2 — Mätning (etapp 0)

**S2.1 Loggrad per anrop**
- a. Mellanlager som mäter tiden per anrop
- b. Räkna BigQuery-frågor per anrop
- c. Cache träff eller miss
- d. Skriv som strukturerad JSON till Cloud Logging
- e. Färdig fråga i Logs Explorer, dokumenterad

**S2.2 Baslinje**
- a. Mät en matchkväll och logga utfallet i `LOGG.md`

## F3 — Serveringslagrets grund

**S3.1 Läsa och skriva blobar** — `api/servering.py`
- a. Sökvägar `serving/v{N}/…`
- b. `generated_at` och `source_updated_at` i varje blob
- c. Läsning med tidsgräns och tyst reserv vid fel

**S3.2 Flagga per endpoint** — `SERVERING_BLOBBAR`, avslagen som standard

**S3.3 Förberäkningssteget**
- a. Eget steg efter skörden, inte inuti skrapern
- b. Vilka matcher och säsonger ändrades i körningen
- c. Kommando för att bygga allt från början

## F4 — Matchrapporten (etapp 1)

**S4.1 Bryt ut byggaren** ur `get_match` till `api/svar/match.py`. Ren
omflyttning — jämförelseverktyget ska visa noll skillnader.

**S4.2 Förberäkningen skriver matchblobar** för ändrade matcher, plus full
uppbyggnad.

**S4.3 `get_match` läser bloben** när flaggan är på.

**S4.4 Verifiering** — kandidat, alla matcher identiska, kall rapport under en
halv sekund.

**S4.5 Godkännande och produktion** — ägarens.

## F5 — Säsongssvaren (etapp 2)

En story per endpoint eller grupp, samma mönster som F4. Sista storyn tar
bort värmningsjobbet.

## F6 — Spelarprofilerna (etapp 3)

## F7 — `main.py` i delar (etapp 4)

Inget eget projekt. Varje endpoint som flyttar till en blob flyttar sin
byggare till `api/svar/` i samma story.

## F8 — Servera direkt från CDN (etapp 5, valfri)

Bara om F2:s mätningar efter F6 visar att Cloud Run är flaskhalsen.
