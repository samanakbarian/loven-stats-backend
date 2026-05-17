# 📚 Löven Stats Hub — Systemdokumentation & Master Plan

*Senast uppdaterad: 2026-05-17*  
*Detta dokument utgör den officiella tekniska dokumentationen för systemet samt "Master Planen" för hela Löven Stats Hub.*

---

## 1. Vision & Affärsmodell

- **Kärnsyfte:** Att bygga Sveriges smartaste och snyggaste community-plattform för IF Björklövens supportrar.
- **Användarlöfte:** Alltid 100 % gratis för fansen.
- **Kontext:** Björklöven vann HockeyAllsvenskan 25/26 och spelar i SHL från säsongen 26/27.
- **Monetisering (på sikt):** 
  - "Native" sponsring från lokala Umeå-företag (snyggt integrerat i Dark Mode-UI:t).
  - B2B-licensiering av datadrivna widgets till lokalmedia (ex. VK).
  - Relevanta affiliate-länkar (t.ex. boka bord på O'Learys inför match).

## 1.1 Roadmap-synk mellan repos

Roadmapen i `loven-stats-backend/docs/ROADMAP.md` och
`slutspel/docs/ROADMAP_PRODUCT_2026.md` ska vara innehallsmassigt synkade.
Om roadmaparna avviker galler senaste synkade version daterad 2026-05-04 tills ny synk ar publicerad i bada repor.

## 1.2 UX-styrning for frontendupplevelsen

Styrande UX-spec finns i `slutspel/docs/UX_REBUILD_2026.md`.
Backend-roadmap och API-kontrakt ska prioritera dataleveranser som stodjer den upplevelsen
(Lovenlaget, panikniva forst, "vad betyder det?", djupanalys pa klick, tydlig freshness).

## 1.3 Dokumenterade arkitekturavvikelser (obligatoriskt)

Nedan listas aktiva avvikelser fran tidigare planerad malarkitektur.

### ADV-2026-05-04-01: `GET /api/v1/lovenlaget` inford fore full warehouse-kedja

- Beslut: endpointet levererar produktsignal nu, med mart-first och fallback till heuristik.
- Avvikelse: tidigare ideal var "warehouse forst, endpoint efter". Nu kor vi "endpoint tidigt" for att driva produktbygget.
- Motiv: frontend v2 behovde en stabil signalyta direkt for att bygga Laget-floden.
- Teknik:
  - Primarkalla: BigQuery `loven_marts.mart_lovenlaget_snapshot`
  - Fallback: existerande silly-baseline + enkel regeltolkning
- Risk: tillfallig skillnad mellan fallbacklogik och martlogik.
- Plan for normalisering: fallback ska tas bort nar marten ar schemalagd och verifierad i produktion.

### ADV-2026-05-04-02: dbt bootstrap skapad i backend innan full model tree

- Beslut: minimal dbt-struktur skapad i `loven-stats-backend/dbt/` med produktmart + tester.
- Avvikelse: tidigare dokumentation beskrev full model tree som malbild men repositoryn saknade korbar bas.
- Motiv: ga fran "design-only" till korbar warehouseleverans omedelbart.
- Teknik:
  - `dbt_project.yml`, `packages.yml`, produktmart, staging for silly
  - toggle `use_real_silly_source` for stegvis overgang
- Risk: bootstrapvarder finns kvar tills stagingkallor ar komplett etablerade.
- Plan for normalisering: ersatt bootstrap med full staging->marts-kedja och schema-tester pa kallsidan.

---

## 2. Repositories

| Repo | Innehåll | URL |
|------|---------|-----|
| `slutspel` | Frontend (old + v2), systemdokumentation | [github.com/samanakbarian/slutspel](https://github.com/samanakbarian/slutspel) |
| `loven-stats-backend` | API, scrapers, Cloud Functions, dbt, docs | [github.com/samanakbarian/loven-stats-backend](https://github.com/samanakbarian/loven-stats-backend) |

---

## 3. Övergripande Arkitektur

```
┌──────────────────────────────────────────────────────────────────────┐
│                         DATAKÄLLOR                                   │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│ Sportradar   │ EliteProsp.  │ Web Scrapers │ Manuell Baseline       │
│ (live events)│ (kontrakt)   │ (nyheter)    │ (silly season)         │
└──────┬───────┴──────┬───────┴──────┬───────┴──────┬─────────────────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    GCS DATA LAKE (raw JSON)                          │
│  raw/sportradar/  raw/eliteprospects/  raw/silly_season/  raw/...  │
│  Bucket: loven-stats-raw-data-prod                                  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        BIGQUERY                                      │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐         │
│  │ raw_sportradar │  │ raw_content    │  │ raw_elite...   │ DATASETS│
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘         │
│          │                   │                   │                   │
│          ▼                   ▼                   ▼                   │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │              loven_staging (dbt views)                    │       │
│  │  stg_sr_matches, stg_sr_events, stg_articles, ...       │       │
│  └──────────────────────────┬───────────────────────────────┘       │
│                             │                                       │
│                             ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │         loven_marts (dbt tables, star schema)            │       │
│  │  fact_match_events, fact_player_game_stats,              │       │
│  │  dim_matches, dim_players, dim_teams, dim_contracts, ... │       │
│  └──────────────────────────┬───────────────────────────────┘       │
│                             │                                       │
│                             ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │              loven_ai (BigQuery ML + Gemini)             │       │
│  │  xg_model, ai_article_sentiment, ai_player_impact       │       │
│  └──────────────────────────────────────────────────────────┘       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      CLOUD RUN (FastAPI)                             │
│  /api/silly-season  /api/v1/matches  /api/v1/roster  /api/v1/...   │
│  URL: https://loven-stats-api-324947473206.europe-west1.run.app    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
         ┌──────────────────┐  ┌──────────────────┐
         │  Frontend (old)  │  │  Frontend 2.0    │
         │  Netlify         │  │  React/Vite/TS   │
         │  Vanilla JS      │  │  Firebase Hosting│
         └──────────────────┘  └──────────────────┘
```

### Teknologier

| Lager | Teknologi | Plats |
|-------|-----------|-------|
| **Orkestrering** | Cloud Scheduler + Cloud Workflows | GCP |
| **Extract** | Python Cloud Functions | `functions/` |
| **Data Lake** | Google Cloud Storage (GCS) | `loven-stats-raw-data-prod` |
| **Data Warehouse** | BigQuery | `granskaren-d51a1` |
| **Transformation** | dbt (dbt-bigquery) | `dbt/` |
| **AI/ML** | BigQuery ML + Gemini (Vertex AI) | BigQuery |
| **API** | FastAPI (Python) på Cloud Run | `api/` |
| **Frontend** | React/Vite/TypeScript + Zustand | `slutspel/frontend_v2/` |
| **Styling** | Vanilla CSS med Design Tokens | Dark Mode, glassmorphism |

---

## 4. Datakällor

| Källa | Typ | Status | Data |
|-------|-----|--------|------|
| **Sportradar** | REST API | ✅ Trial-nyckel | Live-matcher, resultat, tabeller, trupper |
| **EliteProspects** | REST API | ⏳ Behöver nyckel | Spelarprofiler, kontrakt, löner, karriärhistorik |
| **Web Scrapers** | Cloud Functions | ✅ Live (var 30 min) | Nyheter från Björklöven.com, Expressen, HockeySverige, EP |
| **Swehockey Stats Scraper** | Cloud Functions Gen2 | ✅ Implementerad (2h-intervall) | Player/goalie stats, standings, schedule från stats.swehockey.se |
| **Manuell Baseline** | JSON i GCS | ✅ | Silly Season-data, kända kontrakt |

### Sportradar-detaljer

| ID | Värde |
|----|-------|
| Competition (HA) | `sr:competition:416` |
| Season (HA 25/26) | `sr:season:131137` |
| Björklöven Team ID | `sr:competitor:3747` |
| API-nyckel | Env var `SPORTRADAR_API_KEY` |
| Trial-begränsning | 1 req/sek, 1000 req/30 dagar |

---

## 5. GCP-infrastruktur

| Resurs | Typ | Detaljer |
|--------|-----|---------|
| **Projekt** | GCP | `granskaren-d51a1` |
| **Region** | | `europe-west1` |
| **GCS Bucket** | Storage | `loven-stats-raw-data-prod` |
| **loven-stats-api** | Cloud Run | FastAPI, Python 3.11, allow-unauthenticated |
| **silly-season-scraper** | Cloud Functions Gen2 | 1024Mi, 300s timeout, Python 3.11 |
| **sportradar-ingest** | Cloud Functions Gen2 | Python 3.11 (schemalagd) |
| **swehockey-stats-scraper** | Cloud Functions Gen2 | 1024Mi, 300s timeout, Python 3.11 (var 2:e timme) |
| **Cloud Scheduler** | Cron | `*/30 * * * *` (silly scraper), `0 */2 * * *` (swehockey stats) |

### API-endpoints (Live)

| Endpoint | Beskrivning |
|----------|-------------|
| `GET /api/silly-season` | Silly Season-feed (mergad scraper + baseline) |
| `GET /api/v1/x-feed` | X/Twitter-feed för Björklöven + sentiment + valfri AI-sammanfattning |
| `GET /api/v1/roster` | Trupp (planerad) |
| `GET /api/v1/matches` | Matcher (planerad) |
| `GET /api/v1/standings` | Tabell (planerad) |

Se även:
- `docs/SWEHOCKEY_STATS_SCRAPER.md` för deploy, scheduler och dbt-koppling.

### Driftnoter 2026-05-17

- X-feed (`GET /api/v1/x-feed`) har nu fallback-query om dagens traffar saknas i primar-queryn.
- Rosterdata i `GET /api/silly-season` hardas med automatisk sync:
  - alla `confirmed_signings` speglas till `roster` om de saknas.
- Topi Niemela ar inlagd i baseline-signings och roster.
- Aktiv Cloud Run URL for prod:
  - `https://loven-stats-api-324947473206.europe-west1.run.app`

### X/Twitter integration (kostnadskontrollerad)

- Källa: X API v2 `recent search` (app-only bearer token).
- Query default: Björklöven i text/hashtag, utan retweets/replies.
- Cache: endpointen lagrar senaste payload i GCS (`derived/x_feed/latest.json`).
- Refreshfrekvens: styrs av `X_CACHE_MINUTES` (default `60`).
- API-kostnad: en ny X-hämtning sker först när cache löpt ut eller `force_refresh=true`.
- Sentiment:
  - Bas: lokal heuristik per tweet (ingen modellkostnad).
  - Sammanfattning: valfri Gemini-körning en gång per refresh (inte per tweet, inte per sidvisning).

Miljövariabler:
- `X_BEARER_TOKEN`
- `X_QUERY_DEFAULT`
- `X_MAX_RESULTS_DEFAULT` (10–100)
- `X_CACHE_BLOB` (default `derived/x_feed/latest.json`)
- `X_CACHE_MINUTES` (default `60`)
- `X_AI_ENABLED` (`true/false`)
- `X_AI_MODEL` (default `gemini-2.5-flash`)
- `GEMINI_API_KEY` (krävs endast om `X_AI_ENABLED=true`)

---

## 6. Implementationslogg

### 6.1. Silly Season Scraper & Feed (Maj 2026) ✅
- Scraper i Cloud Functions hämtar nyheter från 4 källor var 30:e minut
- Gemini AI klassificerar artiklar (BEKRÄFTAT_NYFÖRVÄRV, BEKRÄFTAD_FÖRLUST, etc.)
- Konservativ keyword-baserad fallback i API (titelmatchning, ej body)
- GCS-filer namnges med `%Y%m%d_%H%M%S`, sorteras på `blob.updated` timestamp
- Frontend (old + v2) visar live-data

### 6.2. Data Warehouse & dbt (Maj 2026) ✅
- 6 BigQuery-datasets skapade (`raw_sportradar`, `raw_eliteprospects`, `raw_content`, `loven_staging`, `loven_marts`, `loven_ai`)
- Stjärnschema designat i `docs/DATA_WAREHOUSE_DESIGN.md`
- Stödjer: Basic stats → Corsi/Fenwick → xG → AI (Gemini)
- Multi-source: Sportradar + EliteProspects + Scrapers
- Multi-league: SHL + HA + J20
- Player ID Crosswalk löser matchning Sportradar ↔ EP
- dbt-projekt med Python 3.12 venv (`slutspel/dbt/`)
  - 3 staging-modeller: `stg_sr_matches`, `stg_sr_events`, `stg_sr_standings`
  - 3 mart-modeller: `dim_matches` (199 matcher), `dim_teams` (14 lag), `dim_seasons`
  - 6/6 modeller PASS, 6/6 tester PASS
- Rådata laddad: 200 summaries + 1 timeline + standings → BigQuery

### 6.3. Frontend 2.0 (Pågående)
- React/Vite/TypeScript med Zustand state management
- Sidor klara: Silly Season (live), Matchcenter (mock), Roster (mock)
- Sidor ej klara: Dashboard, Standings, History
- Se `slutspel/FRONTEND_2.0_SPECS.md` för fullständig kravspec

---

## 7. Referensdokumentation

| Dokument | Plats | Beskrivning |
|----------|-------|-------------|
| Systemdokumentation | `SYSTEM_DOCUMENTATION.md` (båda repos) | Detta dokument |
| Data Warehouse Design | `loven-stats-backend/docs/DATA_WAREHOUSE_DESIGN.md` | Fullständigt stjärnschema |
| Roadmap | `loven-stats-backend/docs/ROADMAP.md` | Fasad plan med milstolpar |
| Affärsmodell | `loven-stats-backend/docs/BUSINESS_MODEL.md` | Monetisering & intäktsströmmar |
| Frontend 2.0 Spec | `slutspel/FRONTEND_2.0_SPECS.md` | UX/UI-krav och teknisk stack |
