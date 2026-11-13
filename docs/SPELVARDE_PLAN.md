# Spelvärde: plan

Uppdaterad 2026-11-13. Intern sida, `sida377.se/spelvarde`, inte länkad från
menyn och märkt `noindex`. Den här planen säger vad som finns, vad som ska
göras och hur vi vet om det fungerar.

## 1. Mål

Hitta spel där vår sannolikhet för ett utfall är högre än spelbolagets odds
säger, och kunna visa i efterhand att det inte var tur.

Det svåra är inte att räkna ut ett värde. Det är att modellen ska vara bättre
än marknaden. Spelbolagens marginal är 5–8 % per marknad, och de vet sådant
modellen inte vet, som skador och vem som står i mål.

## 2. Läget i dag

Klart och i drift sedan 2026-11-13:

- Matchmodellens väntade mål för nästa Björklövenmatch, omräknade till 15
  marknader: 1X2, vinnare, dubbelchans, antal mål, lagens mål, båda gör mål,
  handikapp och rätt resultat.
- Odds skrivs in för hand. Sidan visar rättvist odds, värde, spelbolagets
  marginal och marknadens sannolikhet utan marginal.
- Insats enligt Kelly, en fjärdedel som standard, högst 5 % av kassan.
- Underlag per marknad ur lagens matcher: hemma eller borta, säsongen och de
  tio senaste. Ett kort med form, mål, skott, specialteam, första målet,
  målvakter, vila och inbördes möten.
- Spellogg med slutodds och closing line value (CLV). Loggen ligger bara i
  webbläsaren.

Modellen (`api/matchmodell.py`): viktad Poisson på mål, skattad på SHL de tre
senaste säsongerna. Provad på 2022/23–2025/26: log-loss 1,046 mot 1,057 för
Elo och 1,060 för basfrekvensen. Välkalibrerad.

## 3. Hur professionella spelare arbetar

1. En egen styrka per lag, justerad för hemmaplan, vila och laguppställning,
   omräknad till sannolikheter.
2. Slutoddset hos ett skarpt bolag (Pinnacle) är facit. Den som över många
   spel tar bättre odds än slutoddset vinner på sikt. 1–2 % i snitt räknas
   som bra.
3. Fördelen finns oftast innan oddsen rört sig. I hockey flyttar beskedet om
   startande målvakt oddsen mest.
4. Allt loggas: taget odds, slutodds, resultat. Modellen bedöms på CLV över
   hundratals spel, inte på vinst och förlust.

## 4. Formen

Formen är viktig, men inte som resultaten de senaste matcherna. Det vi vet:

- **Modellen viktar redan nya matcher tyngre.** En match väger hälften efter
  240 dagar. Kortare halveringstid (120–360 dagar provades 27 september) gav
  sämre prognoser, inte bättre. Resultatform har alltså provats och förlorat.
- **Resultat över fem eller tio matcher är mest slump.** Hockey avgörs av få
  mål, och räddningsprocent och skottprocent svänger mycket över korta
  perioder. En förlustsvit med bra skottsiffror säger mer om tur än om form.
- **Marknaden överreagerar på sviter.** Ett lag med fem raka förluster får
  ofta för höga odds. Det är en möjlig källa till värde, inte något att följa.

Det som inte är provat är form mätt i **skott** i stället för mål: andel av
skotten de senaste matcherna. Skott svänger mindre än mål och säger mer om
hur laget faktiskt spelar. Det är steg 2 nedan.

## 5. Steg

Varje steg tas i drift för sig. Ett nytt mått i modellen tas bara med om det
slår dagens log-loss på samma provsäsonger.

### Steg 1. Mer matchdata bakåt (backend)

För SHL 2022/23–2025/26 har vi bara resultaten. Skott, powerplay och
målvakter per match saknas, så skottbaserade mått går inte att pröva.

- Hämta matchsammanfattning, målvakter och händelser för de fyra säsongerna
  från Swehockey med `bash deploy.sh backfill`
  (`BACKFILL_SEASONS`, `EVENTS_LIMIT=all`, `LEAGUE_LIMIT`). Funktionen har 540
  sekunders tidsgräns och tar ungefär en sekund per sida, så varje säsong
  behöver flera körningar.
- Klart när `check_coverage` visar alla spelade matcher med sammanfattning
  för de fyra säsongerna.

### Steg 2. Pröva nya mått i modellen (backend, `scripts/backtest_matchmodell.py`)

I ordning efter vad som väntas ge mest:

1. Lagstyrka ur skott (andel av skotten) vid sidan av mål.
2. Skottform: skottandelen de senaste 5–10 matcherna.
3. Egen styrka hemma och borta.
4. Specialteam: powerplay och boxplay.
5. Vila: dagar sedan förra matchen, match kvällen innan.
6. Målvaktens räddningsprocent, krympt mot seriens snitt.

Inställning på 2022/23–2023/24, bedömning på 2024/25–2025/26. Klart när
varje mått har ett log-loss-tal mot 1,046 och de som slår det är inbyggda.

### Steg 3. Spelloggen i BigQuery (backend och frontend)

Loggen ligger i en webbläsare och försvinner om den rensas. Den ska ligga i
en tabell, `raw_content.spel`, med taget odds, slutodds, modellens
sannolikhet och resultat, skriven via API:t bakom en nyckel.

Klart när ett spel som sparas på sidan går att läsa i BigQuery.

### Steg 4. Odds automatiskt (beslut om pengar)

The Odds API har SHL (`icehockey_sweden_hockey_league`). Historiska odds
kräver betalplan. Det ger:

- odds på sidan utan handpåläggning,
- slutoddset till CLV utan att någon skriver in det,
- det riktiga testet: modellens sannolikheter mot marknadens på tidigare
  säsonger.

Före beslut: kontrollera med en gratisnyckel hur långt bakåt SHL finns och om
Pinnacle ingår. Värt det först om steg 2 gett en bättre modell.

### Steg 5. Målvaktsbesked (utforskande)

Startande målvakt är det som flyttar oddsen mest. Undersök om lagen meddelar
det i tid, till exempel på X som vi redan hämtar, och hur ofta.

## 6. Hur vi vet att det fungerar

| mått | gräns | när |
|---|---|---|
| log-loss på provsäsongerna | lägre än 1,046 | efter steg 2 |
| CLV i snitt | över 0, helst 1–2 % | efter minst 100 loggade spel |
| kalibrering | 55 % blir ungefär 55 % | löpande |

Resultatet i kronor bedöms sist. Över hundra spel är det mest slump.

## 7. Risker och regler

- Sidan har inget lösenord. Den som har adressen kommer åt den.
- Odds hämtas aldrig genom att skrapa spelbolagens sidor.
- Värde över 10 % är oftare ett modellfel än ett bra spel.
- Görs sidan publik krävs en genomgång av svenska regler för
  spelmarknadsföring först.
