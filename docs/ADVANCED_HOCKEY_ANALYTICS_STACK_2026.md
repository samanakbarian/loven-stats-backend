# Advanced Hockey Analytics Stack 2026

Senast uppdaterad: 2026-06-04
Gäller för: `loven-stats-backend` och `slutspel/frontend_v2`

## Syfte

Det här dokumentet sammanställer vad avancerad hockeyanalys-mjukvara innehåller
2026 och översätter det till vad Löven Stats Hub bör bygga. Ambitionen är hög:
vi ska ha datagrund, scouting, matchanalys, machine learning, simuleringar och
produktvyer. Det ska däremot byggas i rätt ordning, så varje lager förstärker
nästa.

## Externa referenser

Researchen bygger på publika produkt- och metodkällor:

- Sportlogiq iCE: AI/video/data-plattform för coaching, scouting, line
  combinations, matchup-analys och video-länkade metrics. Källa:
  https://www.sportlogiq.com/hockey/
- Sportlogiq/LiveBarn 2025-26: iCE Elite-data för xG, shot locations,
  zone entries/exits och 500+ metrics. Källa:
  https://www.sportlogiq.com/2025/09/02/sportlogiq-and-livebarn-expand-partnership/
- NHL EDGE: publik puck- och player-tracking med zonkartor, visualiseringar och
  player comparisons. Källa: https://www.nhl.com/nhl-edge/
- MoneyPuck: xG, flurry-adjusted logic, expected rebounds och "Deserve To Win"
  via simulering av shot-level xG. Källa: https://moneypuck.com/about.htm
- Evolving-Hockey/HockeyStats-liknande metodlager: xG, RAPM, WAR/GAR/xGAR,
  win odds och transparens runt modellmetodik. Källa:
  https://hockeystats.com/methodology
- HockeyStats win odds: Monte Carlo-simulering av matcher, säsong och slutspel.
  Källa: https://hockeystats.com/methodology/win-odds
- fastRhockey: open-source access till NHL/PWHL data och XGBoost-baserade
  xG-modeller. Källa: https://fastrhockey.sportsdataverse.org/
- RinkNet: hockey operations/scouting, rapporter, drafts och trades. Källa:
  https://www.rinknet.com/
- Reap Analytics: video-till-data för hastighet, position, zone entries/exits,
  time-on-ice, possession och puck tracking från vanlig HD-video. Källa:
  https://reapanalytics.com/

## Slutsats

De bästa systemen är inte "en dashboard". De är ett integrerat hockey intelligence
system:

1. Datainsamling från event, tracking, video, roster, ekonomi och scouting.
2. Normaliserad identitet för lag, spelare, matcher, säsonger och källor.
3. Modellager för xG, possession value, player impact, win probability,
   simuleringar och roster/scouting-värde.
4. Produktlager som gör datan användbar: matchförklarare, scoutingkort,
   lagbygge, opponent prep, trendvarningar och simulationsvyer.
5. Driftlager som mäter freshness, datakvalitet, modellversioner och fel.

För vårt projekt betyder det att `loven-stats-backend` ska bli
produktionskällan och modellmotorn, medan `slutspel/frontend_v2` ska vara
kontrollrummet som prioriterar insikter.

## Capability Map

### 1. Data Foundation

Måste finnas:

- Multi-season backfill för HA och SHL.
- Match-, event-, skott-, lag-, spelar- och säsongsdimensioner.
- Stabil player identity/crosswalk över Swehockey, Sportradar och framtida
  EliteProspects.
- Source lineage per datapunkt: källa, scrape/run-id, timestamp, confidence.
- Freshness och data quality per endpoint.

Status i projektet:

- `raw_sports.swehockey_seasons` finns och används av `GET /api/v1/seasons`.
- `GET /api/v1/statistics` och `GET /api/v1/analytics` kan filtrera på säsong.
- dbt har staging/marts/serving, men API:t använder fortfarande mycket
  ad hoc-logik direkt mot `raw_sports.*`.

Nästa steg:

- Genomför backfill.
- Inför `raw_ops.data_quality_runs`.
- Migrera API till `serving_*` där det ger stabilare kontrakt.

### 2. Match Intelligence

Måste finnas:

- Matchcenter med timeline, goals, penalties, shots, special teams och perioder.
- Momentumkurva.
- "Varför vann/förlorade vi?" med regelbaserade insikter.
- Deserve-to-win-liknande modul när xG finns.
- Opponent prep inför nästa match.
- Head-to-head mot kommande motståndare.

Status i projektet:

- `slutspel/frontend_v2` har matchkomponenter men store är mockad.
- FastAPI saknar skarpa `/api/v1/matches` och `/api/v1/matches/{id}`.
- `api/main.py` har en kommenterad momentum-endpoint-stub.

Nästa steg:

- Bygg `GET /api/v1/matches`, `GET /api/v1/matches/{game_id}`,
  `GET /api/v1/matches/{game_id}/momentum` och
  `GET /api/v1/matches/{game_id}/explain`.

### 3. Shot Quality och xG

Måste finnas:

- xG-light v1: heuristik på skotttyp, avstånd, vinkel, period och strength state.
- xG v2: tränad modell när tillräckligt många skott med koordinater finns.
- Flurry adjustment.
- Expected rebounds och rebound-risk.
- GSAx för målvakter.
- Shot maps och rinkvisualisering.

Status i projektet:

- Warehouse-designen har `shot_distance_m`, `shot_angle`, `xg`,
  `xg_model_version`.
- dbt har `stg_shot_features` och `fact_shot_features`.
- Operativ Swehockey-data räcker sannolikt inte för full xG.

Nästa steg:

- Exponera xG som `data_quality=missing_shot_coordinates` tills data finns.
- Förbered `xg_model_version=heuristic_v1`.
- Använd Sportradar/tracking om tillgängligt för koordinater.

### 4. Possession, Territory och Tracking

Måste finnas på sikt:

- Zone entries/exits.
- Controlled/uncontrolled entries.
- Puck possession.
- Zone time.
- Forecheck/backcheck-pressure.
- Spelarhastighet, distans, acceleration.
- Shift quality och fatigue proxies.
- Expected Possession Value, EPV, när event/tracking-data räcker.

Status i projektet:

- Inte implementerat i produktionsflöde.
- Kan bara byggas fullt med Sportradar/Sportlogiq/tracking/video-data.

Nästa steg:

- Dokumentera datakrav.
- Börja med proxy-metrics från events: skotttryck per period, målchanser,
  utvisningar, faceoffs och territorial signal om data finns.

### 5. Player Evaluation

Måste finnas:

- Spelarprofil med multi-season utveckling.
- Rolltaggar: målskytt, playmaker, tvåvägsspelare, PP-specialist, defensiv back,
  formstark, riskprofil.
- Liga-percentiler per position och minsta GP.
- Age curve och utvecklingsfas.
- Player impact: on-ice/off-ice, RAPM-lite, WOWY, GAR/WAR-inspirerat index.
- NHLe/league translation för scouting om vi tar in andra ligor.

Status i projektet:

- `GET /api/v1/analytics` har redan `age_curve`.
- Player identity saknas som stabil dimension.
- Frontend saknar riktig spelarprofilvy med historik.

Nästa steg:

- Skapa player identity/crosswalk.
- Bygg `GET /api/v1/players`, `GET /api/v1/players/{player_id}` och
  `GET /api/v1/players/{player_id}/history`.

### 6. Team Strength och Lineup Intelligence

Måste finnas:

- Team strength rating: Elo/Glicko-liknande v1.
- Special teams rating.
- Formjusterad styrka.
- Line combinations och par-analys.
- Matchup-karta: vilka femmor/par som fungerar mot vilka motståndare.
- Skadeläge/roster availability när data finns.

Status i projektet:

- SHL-preseasonmoduler finns delvis i `GET /api/v1/analytics`.
- Rosterläge finns i silly/lovenlaget men inte som full serving-driven endpoint.

Nästa steg:

- Bygg ratingmodell först på lagresultat och målskillnad.
- Lägg till xG/team-strength när shot quality finns.
- Koppla roster-effekt till simulatorn.

### 7. Machine Learning

Vi ska ha ML, men varje modell ska ha tydlig fråga, data, fallback och
modellversion. Modellutveckling utan datakvalitet skapar falsk precision.

Första ML-portfölj:

1. xG-light -> xG v2.
2. Win probability per match.
3. Team strength rating.
4. Season projection / Monte Carlo.
5. Player development forecast.
6. Player role classifier.
7. Trend anomaly detector.
8. Match summary redactor, först regelbaserat, sedan AI på strukturerat underlag.

Rekommenderad modellstack:

- Baslinjer: logistisk regression, Poisson/Skellam, Elo, ridge regression.
- Trädmodeller: XGBoost/LightGBM för xG, rebound-risk och win probability.
- Bayesianska modeller: hierarkisk team/player strength när vi har fler
  säsonger.
- Simulering: Monte Carlo med kalibrerade teamstyrkor och distributionsantaganden.
- AI-text: Gemini enbart på precomputed structured insights, med cache.

Obligatoriska modellkrav:

- `model_name`
- `model_version`
- `trained_at`
- `training_data_window`
- `features`
- `calibration_metrics`
- `backtest_metrics`
- `known_limitations`
- `data_quality`

### 8. Simuleringar

Vi ska ha tre nivåer:

#### Sim v1: Match odds

Input:

- Team rating.
- Home/away.
- Recent form.
- Goals for/against.
- Special teams.
- Goalie proxy om tillgänglig.

Output:

- `home_win_prob`
- `away_win_prob`
- `regulation_win_prob`
- `ot_prob`
- `score_distribution`
- `confidence`

#### Sim v2: Säsongssimulering

Input:

- Återstående schema.
- Match odds per match.
- Tabellregler.
- Osäkerhet i team strength.

Output:

- Projected points P10/P50/P90.
- Rank distribution.
- Top 6-chans.
- Play-in/playout-risk.
- SHL-etableringsrisk.

#### Sim v3: Roster/scenario simulator

Input:

- Spelare in/ut.
- Roll, position, projected impact.
- Budget/economy constraints.
- Ice time allocation.

Output:

- Förändring i projected points.
- Förändring i top 6/playout-risk.
- Luckor i truppbygget.
- Value-for-money.

Status i projektet:

- `GET /api/v1/analytics` har redan en SHL-projected-table-modul.
- Den ska utvecklas till en separat simulationsmotor med körlogg och backtesting.

### 9. Scouting och Roster Intelligence

Måste finnas:

- Spelarkort med roll, trend, ålder, risk och jämförelse.
- Candidate board för potentiella nyförvärv.
- Player similarity.
- Kontraktsläge.
- Budgettryck.
- Roster-gap per position.
- "Om vi värvar X" scenario.

Status i projektet:

- Silly season och Lövenläget har grundsignaler.
- Ekonomikollen finns.
- Ingen riktig scoutingmodell ännu.

Nästa steg:

- Börja med roster-gap + player role tags.
- Lägg sedan till candidate scoring när extern spelarhistorik finns.

### 10. Produktvyer vi bör ha

För fans:

- Lövenläget: viktigaste signalen nu.
- Matchcenter: momentum, förklaring, live/post-game.
- Statistik: säsong, jämför, spelare, målvakter, lag.
- Simuleringar: SHL-projektion, tabellrisk, scenario.
- Ekonomi: SHL-gap, budgettryck, trupp-effekt.
- Silly: impact per nyhet/rykte.

För intern/ops:

- Scraperstatus.
- Data quality.
- Modellkörningar.
- Backfill-status.
- Cache/freshness.

För scouting:

- Player board.
- Jämför spelare.
- Rollfilter.
- Similar players.
- Scenario builder.

## Föreslagen målarkitektur

### Data

- `raw_sports.*`
- `raw_content.*`
- `raw_roster.*`
- `raw_financials.*`
- `raw_ops.*`
- framtida `raw_tracking.*`
- framtida `raw_video_events.*`

### dbt

- `staging`: typning och normalisering.
- `intermediate`: feature engineering och identity resolution.
- `marts/core`: facts och dims.
- `marts/analytics`: modellfeatures och aggregat.
- `marts/product`: Lövenläget, simulation snapshots, player cards.
- `serving`: stabila API-vyer.

### ML/Simulation

- `models/` eller separat `ml/` modul.
- Batch jobs för träning/backtesting.
- BigQuery för feature tables.
- Python för simulatorer och modellträning.
- GCS/BigQuery för model run artifacts.
- API serverar bara senaste godkända snapshot eller kör lättviktsberäkningar.

### API

Prioriterade nya kontrakt:

- `GET /api/v1/current-state`
- `GET /api/v1/matches`
- `GET /api/v1/matches/{game_id}`
- `GET /api/v1/matches/{game_id}/momentum`
- `GET /api/v1/matches/{game_id}/explain`
- `GET /api/v1/season-compare`
- `GET /api/v1/players`
- `GET /api/v1/players/{player_id}`
- `GET /api/v1/players/{player_id}/history`
- `GET /api/v1/simulations/shl`
- `GET /api/v1/simulations/scenario`
- `GET /api/v1/ops/data-quality`
- `GET /api/v1/ops/model-runs`

## Implementation Roadmap

### Fas A: Data och kontrakt

- Historisk backfill.
- Data quality run log.
- Current-state-kontrakt.
- Match-kontrakt.
- Player identity.

### Fas B: Analytics v1

- Season compare.
- Rolling form.
- Match momentum.
- Matchförklarare.
- Player roles.
- League percentiles.

### Fas C: ML v1

- xG-light.
- Team strength rating.
- Match win probability.
- Säsongssimulering med P10/P50/P90.
- Modellregister och backtesting.

### Fas D: Scouting och scenario

- Player development forecast.
- Player similarity.
- Roster scenario simulator.
- Value-for-money.
- Candidate board.

### Fas E: Tracking/video

- Shot maps.
- Zone entries/exits.
- Possession.
- EPV.
- Video-linked clips om datakälla/licens finns.

## Viktiga produktprinciper

- Visa aldrig modelloutput utan `data_quality`.
- Skilj på faktisk data, modellestimat och redaktionell tolkning.
- ML ska förstärka regelbaserad analys, inte ersätta den.
- Simuleringar ska visa osäkerhet, inte bara ett punktestimat.
- Allt som påverkar fanens slutsats ska ha källa eller evidence.
- Interna ops-vyer ska byggas före publika avancerade modellvyer om datakvalitet
  är osäker.

## Första konkreta tickets

1. `ARCH-001` Skapa `raw_ops.model_runs` och `raw_ops.data_quality_runs`.
2. `DATA-001` Backfill HA 2022/23-2024/25.
3. `API-001` FastAPI current-state-kontrakt.
4. `API-002` Matchcenter endpoints.
5. `ANALYTICS-001` Season compare endpoint.
6. `ANALYTICS-002` Rolling form med `window`.
7. `ANALYTICS-003` Match momentum v1.
8. `ANALYTICS-004` Matchförklarare v1.
9. `ML-001` Modellregister och modellmetadata-schema.
10. `ML-002` Team strength rating v1.
11. `SIM-001` SHL Monte Carlo simulator v1.
12. `PLAYER-001` Player identity crosswalk.
13. `PLAYER-002` Player profile/history endpoint.
14. `SCOUT-001` Player role tags v1.
15. `WEB-001` Simuleringsvy i frontend v2.

