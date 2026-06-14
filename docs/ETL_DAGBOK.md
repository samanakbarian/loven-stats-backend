# ETL-dagbok

Dagboken dokumenterar konkreta ändringar, tekniska beslut, verifiering och
kvarstående risker i arbetet med produktions-ETL.

## 2026-06-14 — Kontrollplan V1

### Mål

Påbörja den robusta kedjan från Swehockey till datalager. Första leveransen ska
göra varje ny körning spårbar och stoppa uppenbart felaktiga snapshots innan de
publiceras.

### Genomfört

- Skapade gemensam ETL-runtime i `functions/etl_runtime.py`.
- Definierade `raw_ops.ingestion_runs` som append-only körlogg.
- Definierade `raw_ops.data_quality_runs` för kontroller per datatyp och säsong.
- Lade `run_id` och `source_url` på nya rader i Swehockeys råtabeller.
- Ändrade GCS-strukturen till immutable sökvägar per datum och `run_id`.
- Gjorde Swehockey-ingestionen tvåfasig:
  1. hämta och validera alla batchar
  2. publicera endast om hela kvalitetsgrinden passerar
- Lade stagingmodellen `stg_successful_ingestion_runs`.
- Filtrerade spelare, målvakter, tabell och schema till godkända nya runs.
- Behöll stöd för äldre historiska rader som saknar `run_id`.
- Konsoliderade den dubbla dbt-deklarationen av källan `raw_sports`.
- Verifierade Swehockeys egna säsongsväljare och korrigerade säsongskartan:
  `18263/18266` är 2025/26 och `20961/20962` är 2026/27.
- Lade reproducerbar migrering i `scripts/etl/sync_swehockey_seasons.py`.
- Gjorde API-defaulten deterministisk mot SHL när flera ligor är aktiva.
- Körningen av säsongsmigreringen mot BigQuery slutfördes och verifierades.

### Tekniska beslut

- Körloggen är append-only. BigQuery streaming rows ska inte följas av direkt
  `UPDATE`, eftersom rader i streaming buffer inte är säkra att mutera.
- En underkänd kritisk kontroll stoppar hela publiceringen.
- Tomma spelar- och målvaktslistor före första spelade match loggas som varning.
  Efter säsongsstart är samma utfall ett blockerande fel.
- Partiella laddningar kan finnas i raw om ett laddningsfel sker mitt i
  publiceringen, men deras `run_id` godkänns inte och filtreras bort i dbt.
- Nuvarande API läser fortfarande raw direkt. Full isolering uppnås först när
  API migreras till `serving_*`.

### Verifiering

- Python syntaxkontroll passerar för ETL-runtime, scraper och tester.
- Enhetstester för radkontroller och append-only körlogg passerar.
- Produktionsanslutna tester i `tests/test_data_validation.py` passerar.
- Read-only smoketest mot SHL och HA 2026/27 verifierar att tom preseasonstatistik
  blir varning medan tabell och schema passerar kvalitetsgrinden.
- API-lookup väljer `shl_2627` som default och mappar `ha_2526` till `18266`.
- Ett skarpt lokalt end-to-end-anrop slutfördes med
  `run_id=e0f29281-1b88-41a7-bea4-82f63f501385`.
- Körningen skrev två run-events, 24 kvalitetsresultat, 28 tabellrader och
  728 schemarader. Fyra förväntat tomma preseasonkontroller loggades som
  varningar.
- Lineage-kolumnerna `run_id` och `source_url` verifierades på samtliga fyra
  Swehockey-råtabeller.
- Samtliga dbt-YAML-filer kan parsas.
- `git diff --check` passerar.
- Full `dbt parse/run/test` kunde inte köras eftersom dbt CLI inte är installerat
  i den lokala miljön.

### Kvarstående risker

- Cloud Function-förändringen är inte driftsatt eller provkörd i staging.
- API kan fortfarande läsa en partiell raw-snapshot innan serving-migrationen.
- Historisk backfill använder ännu inte det gemensamma körkontraktet.
- Volymavvikelse, freshness-larm och domänspecifika hockeykontroller saknas.
- Två ligor är aktiva för 2026/27; andra konsumenter än SHL-defaulten behöver
  ange liga eller säsong explicit.

### Nästa arbetsblock

1. Lägg samma `run_id`-kontrakt i `backfill_season.py`.
2. Skapa volym- och freshness-kontroller.
3. Lägg deploy-/smoketest för Cloud Function.
4. Kör dbt i CI eller dbt Cloud efter godkänd ingestion.
5. Migrera statistics till en serving-vy baserad på godkända runs.
