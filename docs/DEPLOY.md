# Deploy

**Backend deployas för hand från Cloud Shell med `bash deploy.sh`.**
Frontend deployas av Netlify vid push till `main` i `slutspel`.

## Så görs det i dag

Alltid från `master`, i ett block som börjar med `cd`:

```
cd ~/loven-stats-backend && git checkout master && git pull && bash deploy.sh api
```

| Mål | Gör |
|---|---|
| `api` | Cloud Run `loven-stats-api`, en instans (`MAX_INSTANCES`, förval 1), värmer cachen efteråt |
| `scraper` | Cloud Functions `swehockey-stats-scraper` |
| `views` | BigQuery-vyerna i `core` och `marts` |
| `film` | Cloud Run `loven-matchfilm`, bucketen `granskaren-d51a1-matchfilm` (publik, CORS), schemat :55 |
| `news` | Cloud Functions `silly-season-scraper` |
| `schedule` | skördens schema, varmhållningen och cacheomhämtningen |
| `backfill` | hämtar om avslutade säsonger, utan deploy |
| `budget` | budgetlarm på projektet |
| `restore-env` | återställer miljövariabler från en äldre revision |
| `all` | api och scraper |

Ordningen när flera delar ändrats: `views` före `scraper` före `api`.
Filmtjänsten läser bara API:t och kan deployas när som helst.

Filmerna görs direkt, utan att vänta på schemat:

```
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$(gcloud run services describe loven-matchfilm --region europe-west1 --format='value(status.url)')/kor"
```

Med `?alla=1` gås hela säsongen igenom, med `?game_id=` en match, med
`?sasong=ha_2526` en äldre säsongsfilm.

## Den automatiska deployen som togs bort

Det fanns en GitHub Actions-workflow som skulle göra det automatiskt vid push
till `master`. Den blev aldrig färdiguppsatt — hemligheterna `GCP_WIF_PROVIDER`
och `GCP_DEPLOY_SA` sattes aldrig — så den föll på autentiseringssteget efter
sex sekunder, 69 körningar i rad, och mejlade ett misslyckande vid varje push
till `api/` eller `functions/`. Den togs bort 11 september 2026.

Resten av det här dokumentet beskriver uppsättningen för den som vill ta
tillbaka den. Filen finns kvar i git-historiken:
`git show 14c3ca6:.github/workflows/deploy.yml`. Var den uppsättningen skulle
ha deployat:

| Komponent | Tjänst | Källa |
|---|---|---|
| `loven-stats-api` | Cloud Run | `api/` |
| `swehockey-stats-scraper` | Cloud Functions Gen2 | `functions/` |

Autentiseringen sker med **Workload Identity Federation**. GitHub byter sin
OIDC-token mot en kortlivad GCP-token vid varje körning. Ingen
servicekontonyckel skapas, lagras eller roteras.

## Engångsuppsättning

Kör lokalt, med ett konto som har `roles/owner` eller motsvarande i projektet.

```bash
export PROJECT_ID=granskaren-d51a1
export REPO=samanakbarian/loven-stats-backend
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

gcloud services enable \
  iamcredentials.googleapis.com \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$PROJECT_ID"
```

### 1. Servicekonto som deployen agerar som

```bash
gcloud iam service-accounts create github-deployer \
  --project="$PROJECT_ID" \
  --display-name="GitHub Actions deployer"

export SA="github-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

for ROLE in \
  roles/run.admin \
  roles/cloudfunctions.admin \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.writer \
  roles/storage.admin \
  roles/iam.serviceAccountUser
do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA}" --role="$ROLE" --condition=None
done
```

### 2. Workload Identity Pool och provider

`--attribute-condition` är säkerhetsspärren: utan den kan vilket
GitHub-repo som helst begära en token för det här servicekontot.

```bash
gcloud iam workload-identity-pools create github \
  --project="$PROJECT_ID" --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"
```

### 3. Låt repot agera som servicekontot

```bash
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --project="$PROJECT_ID" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}"
```

### 4. Lägg in värdena som GitHub-secrets

Skriv ut dem:

```bash
echo "GCP_WIF_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
echo "GCP_DEPLOY_SA    = ${SA}"
```

Lägg in båda under **Settings → Secrets and variables → Actions** i
`samanakbarian/loven-stats-backend`. Inget av dem är hemligt i sig — de är
identifierare, inte nycklar — men workflowen läser dem som secrets så att
projektnumret inte ligger i klartext i loggarna.

## Efter uppsättningen

Varje push till `master` som rör `api/` eller `functions/` deployar
automatiskt. Workflowen avslutas med att anropa `/api/v1/health` och
misslyckas om API:t inte svarar 200.

Deploy utan kodändring, t.ex. efter en rollback: **Actions → Deploy backend
→ Run workflow**, och välj `api`, `scraper` eller `both`.

## Scrapern körs på schema

Scrapern deployas av workflowen men triggas av Cloud Scheduler, som sätts
upp separat (se `docs/SWEHOCKEY_STATS_SCRAPER.md`). Efter en deploy kan den
köras direkt:

```bash
gcloud scheduler jobs run swehockey-stats-scraper-job --location=europe-west1
```

## Rollback

```bash
# Cloud Run: lista revisioner och peka trafiken på en tidigare
gcloud run revisions list --service=loven-stats-api --region=europe-west1
gcloud run services update-traffic loven-stats-api \
  --region=europe-west1 --to-revisions=REVISION=100
```

## Åtgärd krävs: läckt Sportradar-nyckel

`functions/main.py:15` hade en Sportradar-API-nyckel hårdkodad som
default-värde. Den är borttagen ur koden, men **ligger kvar i
git-historiken** och kan inte tas bort därifrån utan att skriva om
historiken.

Nyckeln måste därför roteras hos Sportradar. Den nya nyckeln ska aldrig
in i koden — lägg den i Secret Manager och montera den som miljövariabel:

```bash
echo -n "NY_NYCKEL" | gcloud secrets create sportradar-api-key \
  --project="$PROJECT_ID" --data-file=-

# ge körtidskontot läsrätt
gcloud secrets add-iam-policy-binding sportradar-api-key \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

Montera den sedan vid deploy med
`--set-secrets="SPORTRADAR_API_KEY=sportradar-api-key:latest"`.

Notera att Sportradar-flödet inte används av appen i nuläget: koden pekar
på `trial`-endpointen, är hårdkodad till HA 25/26 och skriver till GCS i
stället för BigQuery. Om integrationen inte ska återupptas är det enklaste
att avregistrera nyckeln helt.
