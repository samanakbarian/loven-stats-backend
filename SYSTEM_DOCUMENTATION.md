# 📚 Löven Stats Hub — Systemdokumentation & Master Plan

*Detta dokument utgör den officiella tekniska dokumentationen för systemet samt "Master Planen" för hela Löven Stats Hub.*

---

## 1. Vision & Affärsmodell

- **Kärnsyfte:** Att bygga Sveriges smartaste och snyggaste community-plattform för IF Björklövens supportrar.
- **Användarlöfte:** Alltid 100 % gratis för fansen.
- **Monetisering (på sikt):** 
  - "Native" sponsring från lokala Umeå-företag (snyggt integrerat i Dark Mode-UI:t).
  - B2B-licensiering av datadrivna widgets till lokalmedia (ex. VK).
  - Relevanta affiliate-länkar (t.ex. boka bord på O'Learys inför match).

---

## 2. Övergripande Arkitektur (Den Moderna Datastacken i GCP)

Vi flyttar från ett monolitiskt upplägg till en serverlös, skalbar (och kostnadseffektiv) Google Cloud-arkitektur.

- **Orkestrering:** Cloud Workflows + Cloud Scheduler triggar hela pipelinen.
- **Extract & Load (EL):** Python-skript i Cloud Functions hämtar API-data och skrapar webben. Datan landar i Cloud Storage (GCS) som rå-JSON.
- **Data Warehouse:** **BigQuery** agerar hjärta och datalager.
- **Transformation (dbt):** Vi använder dbt för att modellera stjärnschemat (Staging -> Intermediate -> Marts). Historik löses (SCD Type 2) med dbt:s inbyggda Snapshots (t.ex. kontraktsläget).
- **AI Integration:** Vi använder Googles Gemini AI (via `ML.GENERATE_TEXT` direkt i BigQuery SQL) för att analysera sentimentet i de skrapade forumkommentarerna utan att flytta datan.
- **Semantiskt Lager:** **Cube.dev** sitter ovanpå BigQuery. Det fungerar som vår analysmodell (Headless BI) och har extremt aggressiv caching för att appen ska svara på millisekunder.
- **Backend & API:** Ett supersnabbt Python FastAPI (hostat på Cloud Run) pratar med Cube.dev.
- **Frontend:** React/Vite-applikation hostad via **Firebase Hosting** för att ligga på samma nätverk som resten av GCP-stacken.

---

## 3. Datastrategi (Källorna)

- **Elite Prospects API:** Huvudkällan för Silly Season. Hanterar laguppställningar, kontrakt, spelarhistorik och övergångar.
- **Web Scraping (Egna källor):** Vi skrapar nyheter från officiella sidor och media (Björklöven, HockeySverige) samt forum (SvenskaFans/Gröngult) för att mata AI:n med supporteråsikter.
- **Swehockey (TSS):** Identifierad som den primära källan för att hämta djup historisk data (play-by-play) för HockeyAllsvenskan (historik).
- **Sportradar:** Aktuellt när vi behöver sekundsnabb live-data under pågående SHL-matcher.

---

## 4. Implementationslogg (MVP Fas)

### 4.1. Silly Season Scraper & Data Lake (Implementerad Maj 2026)
- **Scraper-funktion (`functions/silly_scraper.py`):** Skrapar webben, filtrerar med `BJORKLOVEN_KEYWORDS`. Output landar i `loven-stats-raw-data-prod` (GCS).
- **Scheduler:** Triggar scraping var 30:e minut.
- **FastAPI Endpoint (`/api/silly-season`):** Mergar rådatan med vår statiska baseline och serverar JSON till Frontend.

> [!NOTE]  
> Kommande implementationer kommer omfatta BigQuery-laddning, dbt-modeller, AI-sentimentanalys och Cube.dev.
