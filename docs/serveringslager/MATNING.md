# Mätningen per anrop (S2.1)

Varje anrop till API:t lämnar en rad i Cloud Logging:

```json
{"severity": "INFO", "message": "anrop", "vag": "/api/v1/match/{game_id}",
 "ms": 412, "bq": 3, "cache": "miss", "status": 200, "intern": false}
```

| Fält     | Betyder |
|----------|---------|
| `vag`    | Routens mall, inte adressen. Alla matcher hamnar under `/api/v1/match/{game_id}`. |
| `ms`     | Tid i API:t, från att anropet kom in tills svaret var klart. Kallstart och nätet till besökaren ingår inte. |
| `bq`     | Antal BigQuery-frågor anropet startade, även de som körs parallellt. |
| `cache`  | `traff`, `miss`, `forbi` (`refresh=1`) eller `ingen` (endpointen har ingen cache). Den yttersta cacheuppslagningen avgör. |
| `status` | HTTP-status. 429 från taktbegränsningen räknas också. |
| `intern` | Värmningens egna anrop över 127.0.0.1. Tas med, inte bort: de kostar frågor precis som besökarnas. |

Schemaläggarens eget anrop till `/api/v1/warmup` kommer utifrån och står därför
som `intern=false`, med `bq` 0 och lång tid. Det är vägarna det anropar som
bär frågorna, och de står som `intern=true`.

Koden står i `api/matning.py`, kopplingen i `api/main.py` (`mat_anropet`,
`cached_ok`, `fraga_parallellt`). Testerna i `tests/test_matning.py`.

## Logs Explorer

Console → Logging → Logs Explorer, klistra in:

```
resource.type="cloud_run_revision"
resource.labels.service_name="loven-stats-api"
jsonPayload.message="anrop"
```

Smalna av efter behov genom att lägga till rader:

| Vill se | Lägg till |
|---------|-----------|
| bara besökare | `jsonPayload.intern=false` |
| bara missar | `jsonPayload.cache="miss"` |
| långsamma anrop | `jsonPayload.ms>2000` |
| en endpoint | `jsonPayload.vag="/api/v1/match/{game_id}"` |
| bara kandidaten | `resource.labels.revision_name="loven-stats-api-00NNN-xxx"` |

Kandidatens revisionsnamn skrivs ut av `deploy.sh kandidat`, eller:

```
gcloud run revisions list --service loven-stats-api --region europe-west1 --limit 3
```

## Sammanfattning per väg

Logs Explorer räknar inte percentiler. `tests/sammanfatta_matning.py` gör det.
Från Cloud Shell, i repot:

```
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="loven-stats-api" AND jsonPayload.message="anrop"' --freshness=6h --limit=20000 --format=json | python3 tests/sammanfatta_matning.py
```

Ger en tabell per väg: antal, p50, p95, max (ms), BigQuery-frågor totalt,
missar, träffar och 5xx. Besökarna först, värmningen för sig under.

`--freshness` styr fönstret. För en matchkväll (S2.2): `--freshness=4h` direkt
efter matchen, eller byt mot ett exakt fönster:

```
... AND timestamp>="2026-10-09T17:00:00Z" AND timestamp<="2026-10-09T21:00:00Z"
```

## Kostnad

En rad är runt 250 byte. Värmningen anropar runt 49 vägar var tionde minut,
alltså runt 7 000 rader om dygnet; besökarna lägger till några tusen. Det blir
under 100 MB i månaden. Cloud Logging tar betalt först över 50 GiB.

Värmningen dominerar alltså antalet rader. Det är skälet till att
sammanfattningen visar den för sig.
