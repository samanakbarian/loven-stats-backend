# 🗺️ Roadmap — Löven Stats Hub

*Senast uppdaterad: 2026-05-03*

---

## Kontext

Björklöven vann HockeyAllsvenskan 25/26 och spelar i **SHL säsongen 26/27** med start **mitten av september 2026**. Det ger oss ~4,5 månader att bygga färdigt arkitekturen.

---

## Fas 1: Grundläggning (Maj–Juni 2026) ← **VI ÄR HÄR**

### 1A. Silly Season Hub ✅ LIVE
- [x] Scraper i Cloud Functions (var 30:e min)
- [x] Gemini AI-klassificering av artiklar
- [x] Keyword-baserad fallback-reklassificering i API
- [x] FastAPI-endpoint `/api/silly-season` på Cloud Run
- [x] Frontend (old) visar live-data på Netlify
- [x] Frontend 2.0 Silly Season-sida ansluten till live-API
- [ ] Impact Cards (visar förlorad istid/poäng)
- [ ] Auto-refresh var 5:e minut i frontend
- [ ] AI-sentimentmätare per rykte (0–100%)

### 1B. Data Warehouse Setup ✅ GRUNDLAGT
- [x] Skapa BigQuery-datasets (`raw_sportradar`, `raw_eliteprospects`, `raw_content`, `loven_staging`, `loven_marts`, `loven_ai`)
- [x] Installera dbt-bigquery (Python 3.12 venv i `slutspel/dbt-venv/`)
- [x] Initiera dbt-projekt i `slutspel/dbt/`
- [x] Skapa staging-modeller (`stg_sr_matches`, `stg_sr_events`, `stg_sr_standings`)
- [x] Skapa mart-modeller (`dim_matches` 199 rader, `dim_teams` 14 lag, `dim_seasons` 1 säsong)
- [x] Köra `dbt build` → 6/6 modeller PASS, 6/6 tester PASS
- [x] Ladda rå-JSON (200 summaries, 1 timeline, standings) från GCS till BigQuery
- [ ] `dim_players` — behöver spelardata från timelines/EP
- [ ] `fact_match_events` — behöver fler timelines
- [ ] `fact_player_game_stats` — beror på fact_match_events
- [ ] `fact_on_ice` — behöver lineup-data (SHL only)

### 1C. Roster → Riktig Data
- [ ] Nytt API-endpoint: `GET /api/v1/roster`
- [ ] Hämta trupp via Sportradar Competitor Profile
- [ ] Cacha i GCS (uppdatera dagligen)
- [ ] Ansluta `useRosterStore.ts` till riktigt endpoint
- [ ] Lägg till nationalitetsflaggor, ålder, kontraktsstatus

---

## Fas 2: Frontend 2.0 & Sommarbygge (Juni–Augusti 2026)

### 2A. Dashboard (Hemsidan)
- [ ] "Nästa match"-widget (datum, motståndare, arena)
- [ ] Säsongens KPI:er (tabellplats, V/F, MF/MM)
- [ ] Senaste nyheter (topp 3 från Silly Season)
- [ ] Truppstatus (kontrakterade vs lediga platser)

### 2B. SHL Tabell & Spelschema
- [ ] `StandingsPage.tsx` med sorterbar tabell
- [ ] `SchedulePage.tsx` med kalendervy
- [ ] API-endpoints: `/api/v1/standings`, `/api/v1/schedule`

### 2C. Spelarprofiler
- [ ] Klickbar roster → expanderat spelarkort
- [ ] Säsongsstatistik (G, A, PTS, +/-, PIM, TOI)
- [ ] Foto (EliteProspects / placeholder)
- [ ] Kontraktsstatus
- [ ] Matchlogg (när data finns)

### 2D. Mobilanpassning
- [ ] Sidebar → hamburger-meny på mobil
- [ ] Grid-layouts stackar vertikalt
- [ ] Touch-vänliga interaktioner

### 2E. Deploy Frontend 2.0
- [ ] Sätt upp Firebase Hosting i GCP-projektet
- [ ] CI/CD: GitHub Actions → build → deploy till Firebase
- [ ] Behåll gamla Netlify-siten parallellt tills v2 är redo

### 2F. EliteProspects Integration
- [ ] Ansök om EP API-nyckel (api@eliteprospects.com)
- [ ] Bygg ingestion: Cloud Function → GCS → BigQuery
- [ ] Player ID Crosswalk (matcha SR ↔ EP spelare)
- [ ] Berika `dim_players` med EP-data

---

## Fas 3: SHL Go-Live (September 2026)

### 3A. SHL Match Ingestion Pipeline
- [ ] Uppgradera Sportradar API-nyckel till betald plan
- [ ] Hitta SHL 26/27 `season_id` (publiceras i augusti)
- [ ] Cloud Scheduler: trigga var 2:a min under matcher
- [ ] Spara timelines/events i GCS + BigQuery
- [ ] dbt incremental models för `fact_match_events`

### 3B. Live Matchcenter
- [ ] `useMatchStore.ts` → polling mot API var 30:e sek
- [ ] LiveScoreboard med riktig period/klocka/ställning
- [ ] PlayByPlay med riktiga händelser i realtid
- [ ] MatchStats med SOG, teknings-%, PP%

### 3C. xG-modell
- [ ] Samla skottdata med koordinater (SHL har detta)
- [ ] Träna `LOGISTIC_REG`-modell i BigQuery ML
- [ ] Utvärdera med `ML.EVALUATE`
- [ ] Eventuell uppgradering till `BOOSTED_TREE_CLASSIFIER`
- [ ] Populera `xg`-kolumnen i `fact_match_events`

---

## Fas 4: Historik & AI (Oktober 2026+)

### 4A. Historisk HA-backfill
- [ ] Hämta alla Björklöven-timelines från HA 25/26
- [ ] Ladda in i BigQuery bredvid SHL-data
- [ ] Bygga "Historik & Tidsmaskinen"-sidan

### 4B. AI Sentiment & Spelaranalys
- [ ] Sätt upp Gemini remote model i BigQuery
- [ ] dbt-modell: `ai_article_sentiment.sql` med `ML.GENERATE_TEXT`
- [ ] dbt-modell: `ai_player_impact.sql` (WAR, scouting reports)
- [ ] Visa AI-insikter i Dashboard och Spelarprofiler

### 4C. Cube.dev (Semantiskt lager)
- [ ] Installera Cube.dev ovanpå BigQuery
- [ ] Definiera measures + dimensions
- [ ] Aktivera aggressiv caching
- [ ] Flytta FastAPI till att läsa via Cube istället för direkt BQ

---

## Datakällsstatus

| Källa | Status | API-nyckel |
|-------|--------|------------|
| Sportradar (HA) | ✅ Fungerar | Trial-nyckel (1000 req/30 dagar) |
| Sportradar (SHL) | ⏳ Behöver betald plan | Kontakta Sportradar |
| EliteProspects | ⏳ Behöver API-nyckel | Kontakta api@eliteprospects.com |
| Web Scrapers | ✅ Live | — |
| Manuell Baseline | ✅ I GCS | — |
