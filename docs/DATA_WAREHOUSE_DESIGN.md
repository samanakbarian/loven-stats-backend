# 📐 Data Warehouse Design — Löven Stats Hub

*Senast uppdaterad: 2026-05-03*  
*Detta är den officiella referensdokumentationen för hela datamodellen i BigQuery.*

---

## 1. Översikt

Vi bygger ett modernt Data Warehouse i **Google BigQuery** med **dbt** som transformationslager.  
Modellen är designad för att stödja allt från enkel statistik (mål, assist, poäng) till avancerad hockey-analytik (Corsi, Fenwick, xG) och AI (sentimentanalys, spelarvärderings-modeller via Gemini).

### Nyckelprinciper

1. **Lägsta granularitet:** `fact_match_events` lagrar varje enskild händelse (skott, mål, utvisning). Det går alltid att aggregera uppåt — aldrig att bryta ner.
2. **Stjärnschema (Kimball):** Fakta-tabeller omgivna av dimensioner. Snabba JOINs, lätta att förstå.
3. **Multi-source:** Sportradar (live-data), EliteProspects (karriärdata), scrapers (nyheter) och manuell baseline samlas i ett enhetligt schema.
4. **Multi-league:** Stödjer SHL, HockeyAllsvenskan, J20, och potentiellt andra ligor.
5. **AI-redo:** Dedikerade AI-tabeller som använder BigQuery ML (Gemini) direkt i SQL.

---

## 2. Datakällor

| Källa | Typ | Data | Inmatningsfrekvens |
|-------|-----|------|-------------------|
| **Sportradar** | REST API | Matchhändelser, resultat, tabeller, trupper | Var 2:a min (live), dagligen (tabeller) |
| **EliteProspects** | REST API (betalt) | Spelarprofiler, kontrakt, löner, karriärhistorik, draft | Dagligen |
| **Web Scrapers** | Cloud Functions | Nyheter, rykten, foruminlägg | Var 30:e min |
| **Manuell Baseline** | JSON i GCS | Silly Season-data, kända kontrakt | Manuellt |

### Player ID Crosswalk

Sportradar och EliteProspects har olika ID-system. Vi löser detta med en `player_id_crosswalk`-tabell som mappar mellan dem:

```
Sportradar: sr:player:1882926 (Malmstrom, Anton)
EliteProspects: 584723
Surrogate PK: player_bjo_malmstrom_anton
```

---

## 3. BigQuery Datasets

| Dataset | Syfte | Materialiseringstyp |
|---------|-------|---------------------|
| `raw_sportradar` | Rå JSON från Sportradar, external tables mot GCS | External / Table |
| `raw_eliteprospects` | Rå JSON från EliteProspects API | Table |
| `raw_content` | Skrapade artiklar, foruminlägg | Table |
| `loven_staging` | dbt staging: 1:1-mappning, renaming, typning | View |
| `loven_marts` | dbt marts: Stjärnschema (facts + dims) | Table / Incremental |
| `loven_ai` | AI-modeller och AI-genererade tabeller | Table |

---

## 4. Stjärnschema

### 4.1 Fakta-tabeller

#### `fact_match_events`
*Varje play-by-play händelse som en rad. Hjärtat i all analytik.*

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `event_id` | STRING | PK — Sportradar event ID |
| `match_id` | STRING | FK → dim_matches |
| `season_id` | STRING | FK → dim_seasons |
| `event_type` | STRING | `GOAL`, `SHOT`, `BLOCKED_SHOT`, `MISSED_SHOT`, `PENALTY`, `FACEOFF`, `HIT`, `PERIOD_START`, `PERIOD_END` |
| `event_timestamp` | TIMESTAMP | Exakt tidpunkt |
| `period` | INT64 | 1, 2, 3, 4 (ÖT), 5 (Straffar) |
| `match_clock` | STRING | "14:23" — klockan i perioden |
| `match_minute` | INT64 | Total spelad minut |
| `team_id` | STRING | FK → dim_teams (laget som skapade händelsen) |
| `team_qualifier` | STRING | `home` / `away` |
| `player_id` | STRING | FK → dim_players (primär aktör) |
| `player_name` | STRING | Denormaliserat för snabba queries |
| `player_role` | STRING | `scorer`, `assist`, `secondary_assist`, `penalized` |
| `secondary_player_id` | STRING | Första assist |
| `tertiary_player_id` | STRING | Andra assist |
| `home_score` | INT64 | Löpande ställning vid händelsen |
| `away_score` | INT64 | Löpande ställning vid händelsen |
| `strength_state` | STRING | `5v5`, `5v4`, `4v5`, `5v3`, `4v4`, `3v3`, `EN` |
| `is_power_play` | BOOL | Sant om det scorande laget har numerärt överläge |
| `is_empty_net` | BOOL | Sant om tomt mål |
| `is_shot_attempt` | BOOL | Sant = Corsi-händelse (skott/block/miss/mål) |
| `is_unblocked` | BOOL | Sant = Fenwick-händelse (skott/miss/mål) |
| `is_shot_on_goal` | BOOL | Sant = SOG (skott/mål) |
| `is_goal` | BOOL | Sant = mål |
| `shot_distance_m` | FLOAT64 | Avstånd till mål (m) |
| `shot_angle` | FLOAT64 | Vinkel mot mål (grader) |
| `xg` | FLOAT64 | Expected Goals-sannolikhet (0.00–1.00) |
| `xg_model_version` | STRING | Modellversion för xG |
| `penalty_minutes` | INT64 | Utvisningsminuter |
| `penalty_type` | STRING | Tripping, Hooking, etc. |

> **Partitionering:** `event_timestamp` (DAY)  
> **Klustring:** `match_id`, `team_id`, `player_id`

#### `fact_player_game_stats`
*Aggregerad per-spelare-per-match. Optimerad för dashboards.*

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `player_game_id` | STRING | PK — `{player_id}_{match_id}` |
| `player_id` | STRING | FK → dim_players |
| `match_id` | STRING | FK → dim_matches |
| `team_id` | STRING | FK → dim_teams |
| `goals` | INT64 | Mål |
| `assists` | INT64 | Assist |
| `points` | INT64 | Poäng |
| `plus_minus` | INT64 | +/- |
| `pim` | INT64 | Utvisningsminuter |
| `shots_on_goal` | INT64 | Skott på mål |
| `toi_seconds` | INT64 | Istid (sekunder) |
| `hits` | INT64 | Tacklingar |
| `blocked_shots` | INT64 | Blockerade skott |
| `faceoff_wins` | INT64 | Vunna tekningar |
| `faceoff_losses` | INT64 | Förlorade tekningar |
| `faceoff_pct` | FLOAT64 | Tekningsprocent |
| `corsi_for` | INT64 | Skottförsök för (5v5, on-ice) |
| `corsi_against` | INT64 | Skottförsök mot (5v5, on-ice) |
| `corsi_pct` | FLOAT64 | CF% = CF / (CF + CA) |
| `fenwick_for` | INT64 | Oblockerade skottförsök för (5v5) |
| `fenwick_against` | INT64 | Oblockerade skottförsök mot (5v5) |
| `xgf` | FLOAT64 | Expected Goals för (on-ice) |
| `xga` | FLOAT64 | Expected Goals mot (on-ice) |
| `game_score` | FLOAT64 | Composit impact-metric per match |

#### `fact_on_ice`
*Bryggtabell: vilka spelare var på isen vid varje händelse. Kritisk för on-ice Corsi/xG-attribution.*

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `event_id` | STRING | FK → fact_match_events |
| `player_id` | STRING | FK → dim_players |
| `team_id` | STRING | FK → dim_teams |
| `on_ice_for` | BOOL | Sant om händelsen var "för" spelarens lag |

> Möjliggör queries som: "Vad är Wallmarks on-ice CF% när han spelar med Ottosson?"

---

### 4.2 Dimensions-tabeller

#### `dim_matches`

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `match_id` | STRING | PK (Sportradar sport_event ID) |
| `season_id` | STRING | FK → dim_seasons |
| `match_date` | DATE | Matchdatum |
| `match_timestamp` | TIMESTAMP | Exakt starttid |
| `home_team_id` | STRING | FK → dim_teams |
| `away_team_id` | STRING | FK → dim_teams |
| `home_score` | INT64 | Slutresultat hemma |
| `away_score` | INT64 | Slutresultat borta |
| `winner_id` | STRING | FK → dim_teams |
| `result_type` | STRING | `REG`, `OT`, `SO` |
| `stage` | STRING | `regular_season`, `quarterfinal`, `semifinal`, `final` |
| `round_number` | INT64 | Omgång i grundserien |
| `venue` | STRING | Arena |
| `league` | STRING | `SHL`, `HA`, `J20` |

#### `dim_players`
*Sammanfogar Sportradar + EliteProspects data.*

| Kolumn | Källa | Typ |
|--------|-------|-----|
| `player_id` | Genererad | STRING PK (surrogat) |
| `sportradar_id` | Sportradar | STRING |
| `eliteprospects_id` | EP | INT64 |
| `full_name` | Båda | STRING |
| `first_name` | Båda | STRING |
| `last_name` | Båda | STRING |
| `position` | Båda | STRING (`C`, `LW`, `RW`, `D`, `G`) |
| `position_group` | Deriverad | STRING (`F`, `D`, `G`) |
| `shoots` | EP | STRING (`L`, `R`) |
| `nationality` | Båda | STRING |
| `date_of_birth` | EP | DATE |
| `birth_city` | EP | STRING |
| `height_cm` | EP | INT64 |
| `weight_kg` | EP | INT64 |
| `draft_year` | EP | INT64 |
| `draft_round` | EP | INT64 |
| `draft_pick` | EP | INT64 |
| `draft_team` | EP | STRING |
| `photo_url` | EP | STRING |
| `ep_profile_url` | EP | STRING |

#### `dim_player_careers`
*Karriärhistorik per spelare per säsong (från EliteProspects).*

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `career_entry_id` | STRING | PK (surrogat) |
| `player_id` | STRING | FK → dim_players |
| `season` | STRING | "2025-2026" |
| `team_name` | STRING | Lagnamn |
| `league` | STRING | `SHL`, `HA`, `KHL`, `J20`, etc. |
| `games_played` | INT64 | |
| `goals` | INT64 | |
| `assists` | INT64 | |
| `points` | INT64 | |
| `pim` | INT64 | |
| `plus_minus` | INT64 | |

#### `dim_teams`

| Kolumn | Typ |
|--------|-----|
| `team_id` | STRING PK |
| `team_name` | STRING |
| `short_name` | STRING |
| `abbreviation` | STRING |
| `league` | STRING |
| `primary_color` | STRING |
| `secondary_color` | STRING |

#### `dim_seasons`

| Kolumn | Typ |
|--------|-----|
| `season_id` | STRING PK |
| `season_name` | STRING ("SHL 26/27") |
| `league` | STRING |
| `start_date` | DATE |
| `end_date` | DATE |

#### `dim_contracts` (SCD Type 2 via dbt Snapshots)

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `contract_id` | STRING | PK (surrogat) |
| `player_id` | STRING | FK → dim_players |
| `team_id` | STRING | FK → dim_teams |
| `contract_start` | DATE | Kontraktsstart |
| `contract_end` | DATE | Kontraktsslut |
| `status` | STRING | `SIGNERAD`, `FÖRLÄNGD`, `NYFÖRVÄRV`, `UTGÅENDE` |
| `salary_sek` | INT64 | Årslön (om tillgänglig) |
| `valid_from` | TIMESTAMP | SCD2: när denna rad blev aktiv |
| `valid_to` | TIMESTAMP | SCD2: när raden ersattes |
| `is_current` | BOOL | Sant om gällande kontrakt |

#### `player_id_crosswalk`
*Mappar Sportradar-ID ↔ EliteProspects-ID.*

| Kolumn | Typ |
|--------|-----|
| `sportradar_id` | STRING |
| `eliteprospects_id` | INT64 |
| `player_name` | STRING |
| `match_confidence` | FLOAT64 |
| `matched_at` | TIMESTAMP |

---

### 4.3 AI-tabeller

#### `ai_article_sentiment`
*Gemini-analyserade nyhetsartiklar. Använder `ML.GENERATE_TEXT` direkt i BigQuery.*

| Kolumn | Typ |
|--------|-----|
| `article_id` | STRING PK |
| `title` | STRING |
| `source` | STRING |
| `published_at` | TIMESTAMP |
| `tag` | STRING |
| `sentiment_label` | STRING (`positive`, `negative`, `neutral`) |
| `sentiment_score` | FLOAT64 (0–100) |
| `fan_impact_summary` | STRING (Gemini-genererad) |
| `related_player_ids` | ARRAY\<STRING\> |
| `model_version` | STRING |

#### `ai_player_impact`
*AI-beräknade spelarvärderingar.*

| Kolumn | Typ | Beskrivning |
|--------|-----|-------------|
| `player_id` | STRING | FK → dim_players |
| `season_id` | STRING | FK → dim_seasons |
| `war` | FLOAT64 | Wins Above Replacement |
| `offensive_rating` | FLOAT64 | Offensiv rating |
| `defensive_rating` | FLOAT64 | Defensiv rating |
| `replacement_difficulty` | STRING | `easy`, `moderate`, `hard`, `elite` |
| `ai_scouting_report` | STRING | Gemini-genererad naturlig text |
| `computed_at` | TIMESTAMP | |

#### `ai_xg_model` (BigQuery ML)
*xG-modell tränad direkt i BigQuery.*

```sql
CREATE OR REPLACE MODEL `loven_ai.xg_model`
OPTIONS(
  model_type = 'LOGISTIC_REG',
  input_label_cols = ['is_goal']
) AS
SELECT
  is_goal,
  shot_distance_m,
  shot_angle,
  strength_state,
  is_rebound,
  is_rush,
  period,
  (home_score - away_score) AS score_differential
FROM `loven_marts.fact_match_events`
WHERE is_shot_on_goal = TRUE OR is_goal = TRUE;
```

Uppgradering till `BOOSTED_TREE_CLASSIFIER` (XGBoost) görs genom att ändra `model_type`.

---

## 5. dbt-projektstruktur

```
loven-stats-backend/dbt/
├── dbt_project.yml
├── packages.yml                    (dbt_utils)
├── profiles.yml                    (gitignored, lokal BQ-anslutning)
│
├── models/
│   ├── staging/
│   │   ├── sportradar/
│   │   │   ├── _sr_sources.yml
│   │   │   ├── stg_sr_matches.sql
│   │   │   ├── stg_sr_events.sql
│   │   │   ├── stg_sr_standings.sql
│   │   │   └── stg_sr_players.sql
│   │   ├── eliteprospects/
│   │   │   ├── _ep_sources.yml
│   │   │   ├── stg_ep_players.sql
│   │   │   ├── stg_ep_careers.sql
│   │   │   └── stg_ep_contracts.sql
│   │   └── content/
│   │       ├── _content_sources.yml
│   │       └── stg_articles.sql
│   │
│   ├── intermediate/
│   │   ├── int_goals_enriched.sql
│   │   ├── int_shot_metrics.sql
│   │   ├── int_player_game.sql
│   │   └── int_player_id_crosswalk.sql
│   │
│   └── marts/
│       ├── core/
│       │   ├── fact_match_events.sql    (incremental)
│       │   ├── fact_player_game_stats.sql
│       │   ├── fact_on_ice.sql
│       │   ├── dim_matches.sql
│       │   ├── dim_players.sql          (merged SR + EP)
│       │   ├── dim_player_careers.sql
│       │   ├── dim_teams.sql
│       │   └── dim_seasons.sql
│       └── ai/
│           ├── ai_article_sentiment.sql
│           ├── ai_player_impact.sql
│           └── ai_xg_predictions.sql
│
├── snapshots/
│   └── snap_contracts.sql              (SCD Type 2)
│
├── tests/
│   ├── assert_no_orphan_events.sql
│   └── assert_valid_xg_range.sql
│
└── macros/
    └── generate_surrogate_key.sql
```

---

## 6. Datavolymer

| Omfattning | Händelser/match | Matcher/säsong | Rader i fact_match_events | Rader i fact_on_ice |
|-----------|----------------|---------------|--------------------------|---------------------|
| Björklöven | ~200 | ~72 | ~14 400 | ~173 000 |
| Alla SHL-lag | ~200 | ~380 | ~76 000 | ~912 000 |
| 10 års backfill | ~200 | ~3 800 | ~760 000 | ~9.1M |

BigQuery hanterar miljarder rader. Lagringskostnad: i princip $0.

---

## 7. Avancerade Metriker (beräknas från fact_match_events)

| Metrik | Beräkning | Tabellkälla |
|--------|-----------|-------------|
| **Corsi (CF/CA/CF%)** | `SUM(is_shot_attempt)` per lag/spelare vid 5v5 | fact_match_events |
| **Fenwick (FF/FA/FF%)** | `SUM(is_unblocked)` per lag/spelare vid 5v5 | fact_match_events |
| **xG (Expected Goals)** | `SUM(xg)` beräknat av BigQuery ML-modell | fact_match_events |
| **PDO** | `SV% + SH%` (regressions-indikator) | fact_player_game_stats |
| **Game Score** | Composit av G, A, SOG, blocked shots, penalties, faceoffs | fact_player_game_stats |
| **WAR** | AI-beräknad Wins Above Replacement | ai_player_impact |
| **GSAx** | `SUM(xGA) - actual_goals_against` per målvakt | fact_match_events + dim_players |

---

## 8. Produktmart för startsidan: `mart_lovenlaget_snapshot`

Detta är första produktstyrda marten för nya frontendens startsida ("Lövenläget").
Syftet är att ge en snabb, stabil och källspårbar signalbild utan att frontenden behöver
tolka rådata i runtime.

### 8.1 Grain

En rad per refresh-tillfälle och säsong:
- `snapshot_at` (TIMESTAMP)
- `season_id` (STRING)

Primary key (logisk): `snapshot_id = {season_id}_{snapshot_at}`

### 8.2 Kolumner (v1)

Identifiering:
- `snapshot_id` STRING
- `snapshot_at` TIMESTAMP
- `season_id` STRING
- `league` STRING

Readiness:
- `readiness_score` INT64
- `readiness_summary` STRING

Paniknivå:
- `critical_1` STRING
- `critical_2` STRING
- `critical_3` STRING

Senaste impact:
- `latest_impact_title` STRING
- `latest_impact_level` STRING (`low|medium|high`)
- `latest_impact_meaning` STRING

Truppstatus:
- `goalies_status` STRING
- `defense_status` STRING
- `centers_status` STRING
- `forwards_status` STRING

Ekonomi:
- `economy_risk_level` STRING
- `economy_budget_pressure` STRING
- `economy_next_question` STRING

Freshness/meta:
- `source_updated_at` TIMESTAMP
- `freshness_status` STRING (`fresh|stale|critical|unknown`)
- `new_signals` INT64
- `scraped_articles` INT64
- `schema_version` STRING

### 8.3 Källdependencies (v1)

Primärt:
- `raw_content` / silly-scraper-output (nyheter + metadata)
- `dim_contracts` / roster-status (när tillgängligt)

Temporär fallback:
- baseline-data om upstream-källa saknas

### 8.4 Materialisering

Rekommenderat:
- dbt `incremental` i `loven_marts`
- partitionering på `DATE(snapshot_at)`
- klustring på `season_id`, `freshness_status`

### 8.5 SQL-skiss (dbt)

```sql
{{ config(
    materialized='incremental',
    unique_key='snapshot_id',
    partition_by={"field": "snapshot_at", "data_type": "timestamp"},
    cluster_by=["season_id", "freshness_status"]
) }}

with signals as (
  select
    current_timestamp() as snapshot_at,
    'sr:season:2026_2027_shl' as season_id,
    'SHL' as league,
    68 as readiness_score,
    'Nära, men två luckor kan sänka bygget.' as readiness_summary,
    'Toppback saknas' as critical_1,
    'Centerdjup osäkert' as critical_2,
    'Ekonomiskt tryck måste bevakas' as critical_3
),
meta as (
  select
    max(source_updated_at) as source_updated_at,
    sum(new_articles) as new_signals,
    sum(scraped_articles) as scraped_articles
  from {{ ref('stg_articles') }}
)
select
  concat(signals.season_id, '_', format_timestamp('%Y%m%d%H%M%S', signals.snapshot_at)) as snapshot_id,
  signals.*,
  'Hög påverkan på lagbalansen.' as latest_impact_meaning,
  'medium' as latest_impact_level,
  null as latest_impact_title,
  'stabilt' as goalies_status,
  'kritisk lucka' as defense_status,
  'bevaka' as centers_status,
  'stabilt' as forwards_status,
  'medel' as economy_risk_level,
  'högt' as economy_budget_pressure,
  'Har klubben råd med två spetsnamn?' as economy_next_question,
  meta.source_updated_at,
  case
    when timestamp_diff(current_timestamp(), meta.source_updated_at, hour) <= 6 then 'fresh'
    when timestamp_diff(current_timestamp(), meta.source_updated_at, hour) <= 24 then 'stale'
    when meta.source_updated_at is null then 'unknown'
    else 'critical'
  end as freshness_status,
  meta.new_signals,
  meta.scraped_articles,
  'v1' as schema_version
from signals
cross join meta
```

### 8.6 API-koppling

Endpoint:
- `GET /api/v1/lovenlaget`

Princip:
- endpoint ska läsa senaste raden från `mart_lovenlaget_snapshot`
- fallback till heuristik endast om mart saknas
- fallback ska loggas och exponera `freshness_status=unknown`

### 8.7 Datakvalitetstester (dbt)

Minimikrav:
- `snapshot_id` unique + not null
- `snapshot_at` not null
- `freshness_status` in (`fresh`, `stale`, `critical`, `unknown`)
- `readiness_score` between 0 and 100
- `schema_version` not null

### 8.8 Definition of Done för v1

En första version är klar när:
1. mart byggs schemalagt
2. endpoint läser från mart i produktion
3. frontenden visar martens freshness/status
4. fallback-väg är dokumenterad och mätbar
