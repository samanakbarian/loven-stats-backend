# Datamodellen

*Officiell beskrivning av `core` och `marts`: vad som finns, vad varje tabell
är per rad, hur nycklarna hänger ihop, och vart modellen är på väg.
`DATAPLATTFORM.md` beskriver hämtningen och körningen; det här dokumentet
beskriver datat. Gäller från 2026-09-28.*

Status: **läget i dag** är beskrivet i avsnitt 3–4. **Målbilden** i avsnitt
5–7 är beslutad (backlogg 38). Steg 1 i avsnitt 7 är i drift sedan
2026-09-28: `core.match_*`, `fact_goal`, `fact_penalty`, `check_coverage` och
`check_player_scoring`. Avstämningen var tom för 2026/27 efter rättningarna i
uppställningens parser och seriens omhämtningsfönster. Steg 2 är byggt:
`swehockey_league_game_lineups`, `core.match_lineups`, på isen och plus/minus
för seriens spelare i `fact_player_game`, `fact_lineup_slot` för hela serien
och `check_player_plus_minus`. Provräknat i förväg på 2026/27 (24 matcher):
321 av 333 spelares plus/minus stämmer exakt med Swehockeys; resten skiljer
redan mellan Swehockeys statistik och deras egna matchprotokoll. Avsnitt 4
är ännu inte omskrivet efter det.

---

## 1. Principer

1. **Swehockey är facit.** Resultat, mål, poäng och tabell kommer därifrån.
   Andra källor (SHL, backlogg 37) berikar med det Swehockey saknar och stäms
   av där de överlappar. De skriver aldrig över.
2. **Hela serien, samma djup.** Varje match räknas på samma sätt oavsett om
   Björklöven spelar. "Vårt lag" är ett filter, inte en egen modell.
3. **Kornet står först.** Varje tabell säger vad en rad är. En fråga som
   summerar över fel korn är det vanligaste felet vi haft (utvisningarna
   tredubblades, poängen multiplicerades).
4. **Mått räknas en gång.** Härledda tal — plus/minus ur på-isen-listor,
   mål ur skott och räddningar, powerplaytillfällen — räknas i `marts`, inte
   i varje endpoint.
5. **Allt som berikar får vara `NULL`.** En match utan uppställning eller en
   säsong utan SHL-data ska ge tomma fält, inte fel eller nollor.
6. **Aldrig koppling på namn mellan källor.** Namn stavas olika; koppling
   sker på nycklar eller tröjnummer inom en match.

---

## 2. Lagren

```
raw_sports   källnära, append-only, en tabell per källa och datatyp
    │
core         senaste generationen per nyckel, källans kolumner orörda
    │
marts        dimensioner och fakta, konforma nycklar, härledda mått
    │
api          läser marts; core bara där marts saknar något (se 4.3)
```

`raw_sports` ändras bara av skrapan. `core` och `marts` är vyer i
`sql/core_views.sql` och `sql/marts.sql`, byggda med `bash deploy.sh views`.

---

## 3. Nycklar

| begrepp | nyckel | källa | kommentar |
|---|---|---|---|
| säsong | `season_group_id` | Swehockey | grundserie och slutspel har var sitt id; `dim_season` knyter dem med `season_key` (`shl_2627`) |
| lag | `team_key` = Swehockeys lagnamn | spelschema, tabell | lagkoden (`IFB`, `ÖHK`) är ett attribut; SHL:s koder skiljer sig och kopplas i `map_team` |
| match | `game_id` | Swehockey | heltal, samma i schema, händelser, uppställning och rapporter |
| spelare | `player_key` = "Efternamn, Förnamn" | Swehockey | se 3.1 |

### 3.1 Spelarnyckeln

`player_key` är namnet som Swehockey skriver det, rensat från transfermarkering
(`*`). Det är den svagaste nyckeln i modellen:

- samma person kan stå med olika stavning mellan sidor ("Lukas"/"Lucas");
- rester som "Forsberg, Fredrik (RW)" förekommer i källan;
- två spelare med samma namn i serien skulle krocka.

**Beslut:** nyckeln behålls, men all rensning görs på ett ställe
(`core.player_alias`, en tabell över kända varianter → kanonisk nyckel) och
all koppling mellan källor går via tröjnummer inom en match. En surrogatnyckel
införs först om en namnkrock faktiskt uppstår; den kostar mer än den ger så
länge den inte behövs.

---

## 4. Läget i dag

### 4.1 core

| vy | korn | täcker |
|---|---|---|
| `schedule` | match | hela serien |
| `standings`, `standings_history` | lag × ögonblicksbild | hela serien |
| `team_stats` | lag × avsnitt × mått | hela serien (Swehockeys lagstatistik, facit) |
| `player_season_stats`, `goalie_season_stats` | spelare × säsong | hela serien |
| `roster` | spelare × lag × säsong | hela serien |
| `game_events`, `game_team_summary`, `game_goalies` | händelse / lag × match / målvakt × match | **bara våra matcher** |
| `league_game_events`, `league_game_summary`, `league_game_goalies` | som ovan | **bara seriens övriga** |
| `game_lineups` | spelare × match × femma | bara våra |
| `game_boxscore` | spelare × match (PDF) | bara våra |
| `player_bio` | spelare × säsong (PDF) | bara vårt lag |

Uppdelningen i `game_*` och `league_game_*` kom med backlogg 26: ett tiotal
frågor i API:t läser `game_*` utan lagfilter och hade fått med seriens
matcher.

### 4.2 marts

| vy | korn | täcker |
|---|---|---|
| `dim_season`, `dim_team`, `dim_game` | säsong / lag / match | hela serien |
| `dim_player` | spelare | ur truppen, hela serien |
| `fact_player_game` | spelare × match | **bara våra matcher** |
| `fact_team_game`, `fact_goalie_game`, `fact_lineup_slot` | lag / målvakt / plats × match | **bara våra** |
| `fact_player_season` | spelare × säsong | hela serien |
| `fact_standings_snapshot` | lag × dag | hela serien |

### 4.3 Var API:t läser

API:t läser i dag mest `core` direkt: `schedule` (14 frågor),
`player_season_stats` (15), `standings` (13), `game_events` (10). `marts`
används i sju frågor. Seriens övriga matcher läses bara via
`league_game_summary` och i `serien.py`. Flytten till `marts` hör ihop med
API:ts nya struktur (backlogg 36): datalagret där ska läsa `marts`.

---

## 5. Målbilden

### 5.1 En uppsättning matchtabeller för hela serien

`raw_sports` behåller sina två uppsättningar — historiken ska inte skrivas om.
I `core` slås de ihop:

| ny vy | = | kolumn som skiljer |
|---|---|---|
| `core.match_events` | `game_events` ∪ `league_game_events` | `is_ours` |
| `core.match_team_summary` | `game_team_summary` ∪ `league_game_summary` | `is_ours` |
| `core.match_goalies` | `game_goalies` ∪ `league_game_goalies` | `is_ours` |
| `core.match_lineups` | `game_lineups` ∪ `league_game_lineups` (ny) | `is_ours` |

`is_ours` sätts ur `dim_team`, inte ur vilken rå-tabell raden kom från.
De gamla `core.game_*`-vyerna står kvar oförändrade tills ingen fråga läser
dem, och tas sedan bort. Ingen fråga byter betydelse under tiden.

### 5.2 marts över hela serien

| vy | korn | ändring |
|---|---|---|
| `fact_team_game` | lag × match | hela serien |
| `fact_goalie_game` | målvakt × match | hela serien |
| `fact_lineup_slot` | spelare × match × plats | hela serien, när uppställningen hämtas |
| `fact_player_game` | spelare × match | hela serien; mål och assist direkt, på isen och plus/minus där uppställning finns |
| `fact_goal` (ny) | mål | skytt, assist, spelform, ställning, spelare på isen för båda lagen |
| `fact_penalty` (ny) | utvisning | lag, spelare, minuter, orsak, om den gav mål |
| `fact_shot` (ny, backlogg 37) | skottförsök | bara om SHL-källan godkänns |

Varje fakta har `is_ours_game` och `team_key`, så "våra" är ett `WHERE`.

### 5.3 Vad som måste hämtas

| data | varför | kostnad | beslut |
|---|---|---|---|
| **uppställning, alla matcher** | på isen och plus/minus för alla spelare: händelserna anger bara tröjnummer, uppställningen ger namnen. Behövs också för att koppla SHL-data. | 1 sida per match, som händelsesidan | **hämtas** |
| spelarbiografi, alla lag | ålder och kapten för motståndarna | 1 PDF per lag och säsong | senare, lågt värde |
| matchrapportens PDF, alla matcher | skott, tekningar och officiellt +/− per spelare | 1–2,6 MB per match | **väntar på SHL-beslutet**: SHL:s API ger samma sak och istid med ett litet anrop per match |

Uppställningen hämtas som `league_game_lineups`, med samma tidsbudget, tak
och omhämtningsfönster som seriens händelser. Våra matcher hämtas alltid
först.

---

## 6. Kvalitet

Avstämningen (`_reconcile`) utökas till hela serien. Varje kontroll jämför
två oberoende vägar till samma tal:

| kontroll | vägar |
|---|---|
| mål per lag och match | händelsernas mål mot spelschemats resultat |
| skott per lag och match | matchsammanfattningen mot Swehockeys lagstatistik, per säsong |
| **mål och assist per spelare** | summan i `fact_player_game` mot `player_season_stats`, för alla spelare i serien |
| plus/minus per spelare | härlett ur på isen mot Swehockeys officiella, där uppställning finns |
| täckning | andel spelade matcher med händelser, sammanfattning och uppställning |

Den tredje är den viktigaste: den prövar hela kedjan från händelse via
spelarnyckel till spelare, och ger en lista över namnvarianter som ska in i
`player_alias`.

---

## 7. Ordning

Varje steg tas i drift för sig och ändrar inget befintligt svar.

1. **`core.match_*` och `marts` för hela serien** ur det som redan finns:
   lag, målvakter, mål, utvisningar, spelarnas mål och assist. Avstämningen
   av mål och assist per spelare mot säsongsstatistiken. Ingen ny hämtning.
2. **Uppställning för alla matcher.** Skrapan, `league_game_lineups`, och
   därefter på isen och plus/minus för alla i `fact_player_game`.
3. **API:t läser marts.** Görs inom backlogg 36; varje flyttad fråga jämförs
   mot sparade svar.
4. **De gamla `core.game_*` och `core.league_game_*` tas bort** när ingen
   läser dem.
5. **SHL-berikning** enligt `SHL_KALLA.md`, om villkoren tillåter. Annars
   matchrapportens PDF för alla matcher.

## 8. Öppna frågor

- Swehockey har inga spelar-id:n: truppen och matchsidorna länkar inte till
  enskilda spelare (kontrollerat 2026-09-28). En stabil nyckel kan bara komma
  från SHL:s `playerId`, om den källan godkänns. Till dess gäller namnet med
  `player_alias`.
- Slutspel och kval: samma modell, men "hela serien" är där bara lagen som
  spelar. Påverkar snitt och placeringar, inte tabellerna.
- HockeyAllsvenskan har samma källa och samma modell, men SHL-berikningen
  finns inte där. Allt från SHL är därför valfritt per säsong.
