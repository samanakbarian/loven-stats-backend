# Roadmap 2026 - Backend Leveransplan (Synkad Med Produkt)

Senast uppdaterad: 2026-05-04
Kalla: Synkad med `slutspel/docs/ROADMAP_PRODUCT_2026.md`

## Syfte

Detta dokument beskriver hur `loven-stats-backend` levererar den gemensamma produktvisionen.
Backend ar source of truth for ingest, datakvalitet, lagring, API och drift.

## North Star 2026/27

Leverera tillforlitliga, kallsparbara och kostnadskontrollerade data/API:er som gor att frontenden varje dag kan forklara:
1. vad som har hant
2. varfor det spelar roll
3. hur det paverkar Bjorklovens SHL-etablering

## 2026-mål innan SHL-start

1. Stabil silly-ingest med kallmetadata och freshness-signalering.
2. Roster/match/financials som versionerade API-kontrakt.
3. Overvakning och driftprocess for produktionsmiljo.
4. Tydlig QA-process for finansiella data over flera perioder.

## Leveransfaser

## Fas 0 - Synk och kontrakt (nu)
Mål: Etablera kontraktet mellan backend och frontend.

Leverabler:
- API-kontrakt v1 for:
  - `GET /api/silly-season`
  - `GET /api/v1/matches`
  - `GET /api/v1/roster`
  - `GET /api/v1/financials`
- Gemensam `meta`-struktur:
  - `generated_at`
  - `source_updated_at`
  - `freshness_status`
  - `schema_version`
- Definition of Done per endpoint.

Exit-kriterier:
- Kontrakt publicerat i docs och accepterat av frontend.
- Enhetlig felmodell for 4xx/5xx.

## Fas 1 - Datagrund och stabilisering (maj-juni 2026)
Mål: Gora datapipelines robusta och observerbara.

Leverabler:
- Silly:
  - verifiera scheduler-kedja
  - lagg till status/freshness i payload
  - stale-markering efter policy
- Roster:
  - produktionsendpoint med cache
  - fallback-kalla om primarkalla faller
- Match:
  - grundmodeller i BigQuery/dbt
  - endpoint for matchlista + matchdetalj
- Financials:
  - verified JSON-ingest i backend
  - minst 3 perioder med QA-status

Exit-kriterier:
- Endpoints svarar med konsekvent schema och `meta`.
- Inga tysta fail (fel ska synas i status/monitoring).

## Fas 2 - Fan-upplevelse v1 (juni-augusti 2026)
Mål: Stodja frontendens viktigaste supporterfloden.

Leverabler:
- Datalager for "Dagens Lovenlage" (kort sammanfattningsyta).
- Silly impact-underlag for tapp/nyforvarv/forlangningar.
- Matchcenterunderlag for periodpuls och nyckelhändelser.
- Ekonomiunderlag for trend/riskradar/SHL-gap.

Exit-kriterier:
- Samtliga moduler har backend-data i stallet for UI-hardkodning.
- Freshness och kallspårbarhet exponeras i API.

## Fas 3 - SHL Go-Live (september 2026)
Mål: Produktionshardning for sasongstrafik.

Leverabler:
- SHL-ingest med driftfrekvens under match.
- Monitoring:
  - endpoint health
  - ingest-fel
  - stale-data-larm
- Incidentplaybook och rollback-rutiner.

Exit-kriterier:
- Kritiska larm ar aktiva och testade.
- Driftansvar och runbooks ar dokumenterade.

## Fas 4 - Konkurrenskraft och analys (oktober 2026+)
Mål: Skala analyskapacitet utan att tappa tillit och kostnadskontroll.

Leverabler:
- Competitive Index-underlag.
- Value-for-money och flerarsjämförelser.
- Talent pipeline-data.
- Backend-AI i batch/offline dar det ar rimligt.

## Repoansvar

Backend ansvarar for:
- ingestion
- transform
- QA
- API
- scheduler
- monitoring
- release/incident-hantering

Frontend ansvarar for:
- presentation
- UX
- states
- fallback-upplevelse

## Beslutsregler

- Backend-kontrakt styr over PoC-format.
- Ej verifierad data markeras `preliminary` eller exponeras inte.
- Runtime-AI utan kostnadskontroll godkanns inte for bred produktion.
- Ingen endpoint far sakna freshness-signal.

## 90-dagars teknisk prioritering

1. Kontrakt och schema:
- Publicera endpoint-spec + exempelpayloads.

2. Stabilitet:
- Sakerstall scheduler och ingest-forlopp for silly/roster/match.

3. Financials:
- Bygg verifierad ingest + read endpoint for flerarsdata.

4. Drift:
- Lagg till larmning och incidentrutin.

## Styrmatal

- Freshness SLA per endpoint.
- Andel svar med komplett `meta`.
- Ingest success rate per datakalla.
- API error rate (5xx).
- Tid till upptackt av stale data.

## Nasta konkreta backlog

1. Dokumentera API-kontrakt v1 i backend.
2. Implementera `freshness_status` pa samtliga primara endpoints.
3. Publicera forsta `financials` endpoint med verifierade perioder.
4. Lagg till health + stale-larm for silly-pipelinen.
5. Bygg `mart_lovenlaget_snapshot` i `loven_marts` och koppla `GET /api/v1/lovenlaget` till marten.

## Genomfort (2026-05-04)

- `GET /api/v1/lovenlaget` ar implementerad med mart-first och fallback till heuristik.
- dbt bootstrap ar skapad i backend med:
  - `mart_lovenlaget_snapshot`
  - `stg_silly_articles`
  - tester for nyckelfalt i produktmarten.
- Avvikelsen fran ideal "warehouse forst" ar dokumenterad i `SYSTEM_DOCUMENTATION.md` under arkitekturavvikelser.
