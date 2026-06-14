# ETL Operating Model

Senast uppdaterad: 2026-06-14

## Syfte

Det här dokumentet definierar produktionskontraktet för ingestion till Löven
Stats Hub. Målet är att varje körning ska vara spårbar, validerad,
återstartbar och säker att publicera vidare till dbt och API.

## Målflöde

```text
Källa
  -> immutable GCS raw
  -> BigQuery raw med run_id
  -> kvalitetsgrind
  -> dbt staging för godkända runs
  -> marts
  -> serving
  -> API
```

## Körkontrakt

Varje ingestion får ett UUID i `run_id`.

`raw_ops.ingestion_runs` är en append-only händelselogg:

- `RUNNING` skrivs när körningen startar.
- `SUCCESS`, `FAILED` eller `FAILED_QUALITY` skrivs när körningen avslutas.
- senaste händelsen per `run_id` är körningens aktuella status.
- avbrutna processer lämnar en synlig `RUNNING`-händelse som kan larmas.

Varje rådatapost från den nya pipelinen innehåller:

- `run_id`
- `source`
- `source_url`
- `scraped_at`

## Kvalitetsgrind

`raw_ops.data_quality_runs` lagrar resultat per:

- `run_id`
- datatyp
- `season_group_id`
- kontroll

Första kontrollpaketet verifierar:

1. Minst en rad hämtades.
2. Obligatoriska fält är ifyllda.
3. Affärsnycklar är unika inom snapshoten.

Swehockey-pipelinen hämtar och validerar alla batchar innan någon batch
publiceras. Om en kritisk kontroll faller laddas inga råtabeller för körningen.

## Publiceringsregel

dbt-modellen `stg_successful_ingestion_runs` väljer endast körningar vars senaste
händelse är `SUCCESS`.

Swehockey staging-modellerna:

- accepterar historiska rader där `run_id` saknas
- accepterar nya rader endast om deras `run_id` är godkänt
- deduplicerar därefter senaste snapshot per affärsnyckel

Detta gör införandet bakåtkompatibelt samtidigt som nya körningar får en
kontrollerad publiceringsväg.

## Felhantering

- Käll- eller parserfel ger `FAILED`.
- Underkänd datakvalitet ger `FAILED_QUALITY`.
- Partiell BigQuery-laddning ger `FAILED`; dbt publicerar inte run-id:t.
- GCS-objekt lagras under datum, `run_id`, säsong och datatyp och skrivs inte
  över av senare körningar.

## Nästa härdning

1. Larma på gamla `RUNNING`-körningar.
2. Lägg volymavvikelse mot föregående godkända snapshot.
3. Lägg domänkontroller per tabell, exempelvis tabellplacering och poängregler.
4. Flytta API från direkta raw-frågor till godkända serving-vyer.
5. Inför samma körkontrakt i backfill och övriga scrapers.
6. Orkestrera dbt först efter en godkänd ingestion.
