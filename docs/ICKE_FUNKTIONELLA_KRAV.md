# Icke-funktionella krav

Skrivna 2026-10-10. Vad sajten ska klara utöver att visa rätt saker: hur
snabb, hur färsk, hur billig, hur säker. Varje krav har ett läge i dag och
ett sätt att mäta det. Där läget inte når kravet står vad som ska göras, och
ordningen samlas sist under "Vägen framåt".

Sajten är ett hobbyprojekt med 25–35 besökare om dygnet och toppar på
matchkvällar. Kraven är satta därefter: inget ska kosta dygnet runt för att
klara en last som inte finns, men ingenting får heller gå sönder den kväll
någon delar sajten brett.

## 1. Riktighet

Det viktigaste kravet. Sidan är statistik; ett fel tal är värre än inget tal.

| Krav | I dag | Mäts |
|---|---|---|
| Varje tal stämmer med Swehockey och SHL.se | stämt i alla avstämningar hittills (senast 8–9 okt) | avstämning mot SHL.se efter matchkväll; `_reconcile` i skörden |
| En rättelse hos källan slår igenom utan handpåläggning | ja, vid nästa skörd (append-only + vyer) | jämför efter känd rättelse |
| Preliminära uppgifter markeras som preliminära | ja, i matchrapporten | — |
| Ett tekniskt fel visas som fel, inte som nollor | ja för matchrapporten sedan 10 okt; övriga endpoints delvis | gå igenom `except Exception` (API_ARKITEKTUR) |
| Filmer säger aldrig något annat än sidan | ja: läser API:t, kontroll före rendering | `kontroll.mjs`, `kontrolleraSasong` |
| Officiella definitioner följs, avvikelser förklaras | ja (t.ex. game misconduct räknas som 20 min men förklaras) | Metod-sidan |

**Regler som följer av erfarenheten:**
- Varje sortering har ett skiljetal som betyder något.
- Trösklar uttrycks per match, inte som totalsummor.
- Kontrollera mot Swehockeys sida innan koden får skulden, och tvärtom.

## 2. Färskhet

| Krav | I dag | Mäts |
|---|---|---|
| Resultat och tabell på sajten inom 30 minuter efter skörden | 15 min (skörd :30, cacheomhämtning :45) | `source_updated_at`, sidans "Uppdaterad" |
| Matchfilm inom en filmkörning efter att protokollet är fastslaget | ja, körning :55 | `generated_at` i filmens JSON |
| Säsongsfilm inom en filmkörning efter att omgången är komplett | ja, sedan 10 okt | `generated_at` i `film/sasong/*.json` |
| Inaktuella uppgifter syns utifrån | delvis: "Uppdaterad" i sidhuvudet; inget larm | — |

**Saknas:** ett larm när något slutar uppdateras. Skörden, cachen och
filmerna kan alla tyst stå still. Se "Vägen framåt", punkt 2.

## 3. Prestanda

Mätt på mobil, från Sverige.

| Krav | I dag | Mäts |
|---|---|---|
| Varm sida: svar under 1 s | 0,2–0,8 s | curl mot API:t |
| Kall matchrapport: under 3 s | 2–6 s (BigQuery i svarsvägen) | första anropet efter deploy |
| Ingen kallstart på 20–40 s för besökare | ja så länge varmhållningen går | — |
| Sidans första laddning under 2 s på 4G | ja; 131 KB gzip JS, gzip på API-svar | Lighthouse |
| Filmerna startar direkt | ja: mp4 med `faststart`, stillbild först | — |

**Målbilden** är att ingen läsning når BigQuery: förberäknade svar som blobar,
byggda efter skörden ([HALLBAR_ARKITEKTUR.md](HALLBAR_ARKITEKTUR.md)). Då blir
kall och varm samma sak.

## 4. Tillgänglighet och drift

| Krav | I dag | Mäts |
|---|---|---|
| Sajten uppe matchkvällar | inget avbrott känt den här säsongen | röktest |
| Ett fel i en del tar inte ned resten | ja: kort i `Guard`, filmer frikopplade | — |
| Backend går att driftsätta och backa på minuter | ja: Cloud Run-revisioner, `deploy.sh` | — |
| Frontend som inte bygger märks | nej: Netlify står kvar på förra bygget | CI saknas |

**Kapacitet:** en API-instans tar 80 samtidiga anrop. Det räcker med god
marginal i dag. Beslut: börjar det gå trögt höjs antalet instanser
(`MAX_INSTANCES=3 bash deploy.sh api`). `--min-instances 1` är prövat och
avfärdat.

## 5. Kostnad

| Krav | I dag | Mäts |
|---|---|---|
| Allt under cirka 100 kr i månaden | cirka 60 kr | GCP-fakturan, `deploy.sh budget` |
| Kostnaden växer inte med påhittade anrop | ja: nyckelkontroll och taktbegränsning | — |
| Inget som kostar dygnet runt utan att behövas | ja: min-instances 0, inga databaser igång | — |

Filmtjänsten kostar bara när den renderar: cirka två minuter per match och
fem för säsongsfilmen, fyra körningar om dygnet, och det mesta hoppas över.

## 6. Säkerhet och integritet

| Krav | I dag |
|---|---|
| Inga hemligheter i koden eller i loggarna | ja; bearer-värden bara till temporära filer |
| API:t läser bara, sätter inga cookies, tar inga användardata | ja; bara GET, `allow_credentials=False` |
| Filmtjänsten nås bara av schemaläggaren | ja; privat, OIDC |
| Miljövariabler skrivs aldrig över | ja; `--update-env-vars`, aldrig `--set-env-vars` |
| Inga personuppgifter utöver offentlig spelarstatistik | ja |
| Besöksmätning utan cookies | Cloudflare Web Analytics |

Öppet i backloggen: `SEC-002`–`SEC-006`.

## 7. Upphovsrätt och varumärken

- Musik i filmerna är syntetiserad i koden. Ingen inspelad eller
  upphovsrättsskyddad musik, inte heller som åttabitarsversion av en känd
  melodi — filmerna delas fritt och sajten har ingen licens.
- Inga klubbloggor eller pressbilder. Lagfärgerna är egna tolkningar.
- Statistiken är offentlig fakta från Swehockey; källan anges.

## 8. Användbarhet

| Krav | I dag |
|---|---|
| Byggd för mobil först, 390 px | ja; alla ändringar provas i 390 px |
| Svenska överallt, kort, utan AI-ton | ja; ägarens uttryckliga krav |
| Kontrast enligt WCAG AA i mörkt tema | i stort; inte systematiskt mätt |
| Tangentbord och skärmläsare | grundläggande (`aria-label`, knappar är knappar); inte provat |
| Filmerna tar liten plats i flödet | ja: kompakt kort, helskärm vid uppspelning |

## 9. Underhållbarhet

| Krav | I dag |
|---|---|
| Ändringar provas innan de driftsätts | ja, för hand: stubbar, Playwright, gyllene mästaren |
| Automatiska kontroller vid varje push | **nej** — ingen CI i något av repona |
| En fil per ansvar | nej: `api/main.py` är 7 100 rader |
| Lint utan fel | nej: tio äldre fel i `Statistics.tsx`, ett i `Matchrapport.tsx` |
| Dokumentation som stämmer med koden | ja, efter den här genomgången |

## Vägen framåt

I prioritetsordning. Varje punkt går att göra och driftsätta för sig.

1. **CI i båda repona.** GitHub Actions: `tsc`, `eslint` och `npm run build`
   för frontend; `ruff`, `ast`-kontroll och enhetstester för backend. Först
   med de befintliga lint-felen som baslinje, så att inga nya tillkommer;
   sedan rättas de gamla. Det enda kravet ovan som saknas helt, och det
   billigaste att lägga till.
2. **Larm när något står still.** Röktestet utökas: senaste skörd, cachens
   ålder, och att varje spelad match har en film inom ett dygn. Larmar till
   ägaren. Det är den största tysta risken i dag.
3. **Mät läsvägen** (etapp 0 i HALLBAR_ARKITEKTUR): en loggrad per anrop med
   cacheträff, millisekunder och antal BigQuery-frågor. En matchkväll räcker
   för att veta vad som är långsamt på riktigt.
4. **Matchrapporten som blob** (etapp 1). Kall matchrapport under en halv
   sekund. Förberäkningen kan köras i samma steg som filmerna, efter skörden.
5. **API:ts lager** ([API_ARKITEKTUR.md](API_ARKITEKTUR.md)) längs vägen,
   endpoint för endpoint som de flyttar till blobar.
6. **Säsongssvaren som blobar** (etapp 2). Därefter kan varmhållningen tas
   bort.
7. **Tillgänglighet:** en genomgång med skärmläsare och kontrastmätning av
   färgtoken.

Två saker som medvetet inte görs: `--min-instances 1` och en egen
serveringsdatabas. Skälen står i HALLBAR_ARKITEKTUR.
