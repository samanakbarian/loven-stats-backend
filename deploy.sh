#!/usr/bin/env bash
# Deployar API och scraper till Google Cloud.
#
# Tänkt att köras från Google Cloud Shell (shell.cloud.google.com), som
# fungerar i mobilwebbläsare och redan är inloggad. Kör:
#
#   bash deploy.sh            # allt
#   bash deploy.sh api        # bara API:t
#   bash deploy.sh scraper    # bara scrapern
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-granskaren-d51a1}"
REGION="${REGION:-europe-west1}"
BUCKET="${GCS_BUCKET:-loven-stats-raw-data-prod}"
TARGET="${1:-all}"

say() { printf '\n\033[1;32m▸ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1"; exit 1; }

command -v gcloud >/dev/null || fail "gcloud saknas. Kör från Cloud Shell."
gcloud config set project "$PROJECT_ID" --quiet >/dev/null

say "Projekt: $PROJECT_ID · Region: $REGION · Mål: $TARGET"

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  say "Deployar API:t till Cloud Run"
  gcloud run deploy loven-stats-api \
    --source api \
    --region "$REGION" \
    --allow-unauthenticated \
    --set-env-vars "BQ_PROJECT_ID=${PROJECT_ID},GCS_BUCKET_NAME=${BUCKET}" \
    --quiet
fi

if [[ "$TARGET" == "all" || "$TARGET" == "scraper" ]]; then
  say "Deployar Swehockey-scrapern till Cloud Functions"
  gcloud functions deploy swehockey-stats-scraper \
    --gen2 \
    --region "$REGION" \
    --runtime python311 \
    --source functions \
    --entry-point run_swehockey_stats_scraper \
    --trigger-http \
    --allow-unauthenticated \
    --memory 1024Mi \
    --timeout 300s \
    --set-env-vars "GCP_PROJECT=${PROJECT_ID},GCS_BUCKET=${BUCKET},SWEHOCKEY_TEAM_ID=1139,SWEHOCKEY_SEASON_GROUP_ID=20961" \
    --quiet

  # Utan en körning är de nya tabellerna tomma och endpointsen svarar
  # utan innehåll, vilket lätt förväxlas med ett fel.
  say "Kör scrapern så game_id och trupplistan fylls (tar ~1 min)"
  gcloud scheduler jobs run swehockey-stats-scraper-job --location="$REGION" --quiet \
    || echo "  (schemalagt jobb saknas — hoppar över, scrapern körs vid nästa schemalagda tillfälle)"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  URL=$(gcloud run services describe loven-stats-api --region "$REGION" --format='value(status.url)')
  say "Kontrollerar $URL"
  for ep in /api/v1/health /api/v1/standings /api/v1/roster; do
    CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 "${URL}${ep}" || echo 000)
    printf '  %-22s HTTP %s\n' "$ep" "$CODE"
  done
fi

say "Klart"
