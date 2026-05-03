# 📚 Löven Stats Hub — Systemdokumentation & Master Plan

*Senast uppdaterad: 2026-05-03*  
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
| **Cloud Scheduler** | Cron | `*/30 * * * *` (scraper) |

### API-endpoints (Live)

| Endpoint | Beskrivning |
|----------|-------------|
| `GET /api/silly-season` | Silly Season-feed (mergad scraper + baseline) |
| `GET /api/v1/roster` | Trupp (planerad) |
| `GET /api/v1/matches` | Matcher (planerad) |
| `GET /api/v1/standings` | Tabell (planerad) |

---

## 6. Implementationslogg

### 6.1. Silly Season Scraper & Feed (Maj 2026) ✅
- Scraper i Cloud Functions hämtar nyheter från 4 källor var 30:e minut
- Gemini AI klassificerar artiklar (BEKRÄFTAT_NYFÖRVÄRV, BEKRÄFTAD_FÖRLUST, etc.)
- Konservativ keyword-baserad fallback i API (titelmatchning, ej body)
- GCS-filer namnges med `%Y%m%d_%H%M%S`, sorteras på `blob.updated` timestamp
- Frontend (old + v2) visar live-data

### 6.2. Data Warehouse Design (Maj 2026) 📐
- Stjärnschema designat i `docs/DATA_WAREHOUSE_DESIGN.md`
- Stödjer: Basic stats → Corsi/Fenwick → xG → AI (Gemini)
- Multi-source: Sportradar + EliteProspects + Scrapers
- Multi-league: SHL + HA + J20
- Player ID Crosswalk löser matchning Sportradar ↔ EP

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
| Frontend 2.0 Spec | `slutspel/FRONTEND_2.0_SPECS.md` | UX/UI-krav och teknisk stack |
