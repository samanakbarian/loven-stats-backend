# SHL som andra källa — datamodell

Backloggen: feature 37. Status: utredd, inte påbörjad. **Spärr: SHL:s villkor
är inte utredda.** Ingenting hämtas i produktion förrän det är klart att vi får.

## Varför

Swehockey ger resultat, händelser, uppställning och skott på mål. SHL:s sajt
(`www.shl.se/api`, byggd av Sportality, data från Statnet) har det vi saknar:

| data | Swehockey | SHL |
|---|---|---|
| istid per spelare och match | – | ja |
| skott med position på isen | – | ja, även missade och blockerade |
| missade och blockerade skott per spelare | – | ja |
| tacklingar, blockeringar | – | per spelare och per lag och period |
| tekningar per spelare | ja | ja |
| resultat, mål, assist, utvisningar | ja | ja |

Provat mot IFB–FBK 26 september 2026: skott på mål 25–28 i båda källorna.

## Principen

**Swehockey är facit. SHL berikar.**

- Allt som finns i båda tas från Swehockey. Serien, tabellen, målen och
  poängen på sajten ska se likadana ut oavsett om SHL-hämtningen gått.
- SHL används bara för det Swehockey saknar: istid, skottpositioner,
  missade och blockerade skott, tacklingar och blockeringar.
- Där källorna överlappar jämförs de, i avstämningen efter varje skörd
  (`_reconcile`). En avvikelse syns i körloggen; den skriver aldrig över.
- Allt nytt i marten är kolumner som får vara `NULL`. Faller SHL bort, eller
  gäller säsongen HockeyAllsvenskan som SHL:s sajt inte har, visas sidan som
  i dag utan de nya fälten.

## Identiteter

Källorna har egna nycklar för allt. Kopplingen är det svåra, och den byggs
som egna vyer så att den går att granska.

### Lag: `core.map_team`

Koderna skiljer sig: SHL skriver `OHK`, `VLH`, `SAIK`; Swehockey `ÖHK`,
`VÄX`, `SKE`. En handskriven tabell med fjorton rader per säsong, SHL-kod mot
Swehockeys lagnamn. Ingen automatisk gissning.

### Match: `core.map_game`

SHL:s `gameSourceId` är `20260926-IFB-FBK`: datum och de två lagkoderna. Med
`map_team` blir det en exakt koppling mot `core.schedule` på datum,
hemmalag och bortalag. Swehockeys `game_id` förblir nyckeln i marten; SHL:s
`gameUuid` är ett attribut.

Kontroller: kopplingen är ett till ett, och varje spelad match i serien har
en motsvarighet. SHL:s spelschema har 364 matcher 2026/27, som Swehockey.

### Spelare: `core.map_player`

**Inte på namn.** SHL skriver "Lucas Ekeståhl-Jonsson" och "Chris
DiDomenico", vi har "Ekeståhl-Jonsson, Lukas" och "Didomenico, Christopher".
Vår egen nyckel bär dessutom rester som "Forsberg, Fredrik (RW)".

Kopplingen görs per match på lag och tröjnummer, samma väg som boxscoren
redan knyts (`fact_player_game`): SHL:s `jerseyToday` mot Swehockeys
uppställning för samma `game_id`. Ur den byggs en stabil tabell SHL-`playerId`
→ vår `player_key`, där den vanligaste kopplingen över säsongens matcher
vinner. Spelare som kopplats till fler än en nyckel flaggas och syns i
avstämningen.

## Lager

Samma väg som allt annat: `raw_sports` → `core` → `marts`. API:t läser aldrig
`raw_sports`.

### raw_sports (append-only, samma metafält som i dag)

| tabell | korn | endpoint |
|---|---|---|
| `shl_schedule` | match | `/api/sports-v2/game-schedule` |
| `shl_game_events` | händelse | `/api/gameday/play-by-play/{gameUuid}` |
| `shl_game_player_stats` | spelare × match | `/api/gameday/player-stats/{gameUuid}` |
| `shl_game_team_stats` | lag × match × period | `/api/gameday/team-stats/{gameUuid}` |

Tre anrop per match, bara matcher som är slut och inte redan hämtade, och
omhämtning under samma fönster som Swehockeys (protokoll rättas i efterhand).

### core

Avduplicering som i dag, plus `map_team`, `map_game`, `map_player`.

### marts

- **`fact_player_game`** får nya kolumner: `toi_seconds`, `shots_missed`,
  `shots_blocked`, `hits`, `blocks`. Kopplade via `map_game` och
  `map_player`, `NULL` där SHL saknas.
- **`fact_team_game`** får `shot_attempts`, `hits`, `blocks` per match.
- **`fact_shot`**, nytt. Ett skottförsök per rad: `game_id`, `period`,
  `sekund`, lag, `player_key`, `x`, `y`, `utfall` (mål, räddat, miss,
  blockerat), `malsektion`, `spelform`, `stallning`.
  - Spelformen räknas ur Swehockeys utvisningar, inte ur SHL — samma
    definition som powerplay på resten av sajten.
  - Ställningen räknas ur målen i `game_events`.
  - `utfall` ur SHL:s `goalSection`: 1–9 i mål, 0 miss, −3 blockerat.
    Stämde mot skott på mål i provet, men ska kontrolleras över fler matcher.

## Öppna frågor innan byggstart

1. **Villkoren.** Sidan "Villkor och integritetspolicy" på shl.se, och helst
   ett mejl till SHL. Avgör allt annat.
2. **Koordinaterna.** x 5–521, y −142–140. Båda lagens skott ligger på samma
   sida i alla perioder, så de verkar redan speglade mot anfallsmålet. Prövas
   mot mål med känd position innan någon xG räknas.
3. **Blockerat.** Räknas −3 på skytten eller på laget som blockerar? Provet
   gav 5 för IFB och 3 för FBK; jämförs mot `BkS` per lag.
4. **Historiken.** Play-by-play finns från 2023/24; 2022/23 och äldre svarar
   tomt. Det räcker för en xG-modell och för att pröva skott i
   matchmodellen, men inte för matchmodellens nuvarande provår 2022/23.

## Ordning

Varje steg tas i drift för sig och kan stoppas utan att något annat påverkas.

1. **Beslut om villkoren.** Inget annat börjar före det.
2. **Rådata och kopplingar.** Skrapning till `raw_sports` som en isolerad
   sats (felar den stoppar den inte Swehockey), `map_*`-vyerna och
   avstämningen. Inget i marten än. Klart när alla spelade matcher och alla
   spelare kopplas och skott på mål stämmer per lag och match.
3. **Istid och tacklingar** i `fact_player_game` och på spelarsidan. Liten
   yta, stor nytta, inga modeller.
4. **`fact_shot`** och skottkartor.
5. **xG-modell** på 2023/24–2025/26, med samma disciplin som matchmodellen:
   inställning och prov på skilda säsonger.
6. **Skott i matchmodellen**, prövat mot dagens.

## Klart när

- Sajten visar samma resultat, tabell och poäng som i dag med SHL-hämtningen
  avstängd.
- Varje spelad SHL-match och varje spelare i uppställningen är kopplad, och
  avvikelser syns i avstämningen.
- Skott på mål per lag och match stämmer mellan källorna.
