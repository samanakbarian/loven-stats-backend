#!/usr/bin/env bash
# Deployar API och scraper till Google Cloud.
#
# Tänkt att köras från Google Cloud Shell (shell.cloud.google.com), som
# fungerar i mobilwebbläsare och redan är inloggad. Kör:
#
#   bash deploy.sh            # allt
#   bash deploy.sh api        # bara API:t
#   bash deploy.sh scraper    # bara scrapern
#   bash deploy.sh backfill   # hämta om avslutade säsonger, utan deploy
#
# Backfill kör scrapern mot säsonger som inte är markerade aktiva, så de får
# fält som lagts till i efterhand — game_id, periodresultat, publik, trupp.
# Säsongerna anges med BACKFILL_SEASONS, som standard HA 25/26 med slutspel.
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-granskaren-d51a1}"
REGION="${REGION:-europe-west1}"
BUCKET="${GCS_BUCKET:-loven-stats-raw-data-prod}"
TARGET="${1:-all}"
BACKFILL_SEASONS="${BACKFILL_SEASONS:-18266,19979}"

say() { printf '\n\033[1;32m▸ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1"; exit 1; }

command -v gcloud >/dev/null || fail "gcloud saknas. Kör från Cloud Shell."
gcloud config set project "$PROJECT_ID" --quiet >/dev/null

# Cloud Shell tappar ibland vilket konto som är aktivt när sessionen startas
# om. Inloggningen finns oftast kvar — bara markeringen av vilket konto som
# gäller är borta. Att sätta tillbaka den här sparar en runda, i stället för
# att felet dyker upp först flera minuter in i en deploy.
ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)
if [[ -z "$ACCOUNT" ]]; then
  KNOWN=$(gcloud auth list --format='value(account)' 2>/dev/null | head -1)
  if [[ -n "$KNOWN" ]]; then
    say "Ingen aktiv inloggning — återställer $KNOWN"
    gcloud config set account "$KNOWN" --quiet >/dev/null
    ACCOUNT="$KNOWN"
  fi
fi
if [[ -z "$ACCOUNT" ]]; then
  fail "Cloud Shell har ingen inloggning kvar. Kör:

  gcloud auth login --no-launch-browser

och sedan samma kommando igen."
fi

say "Projekt: $PROJECT_ID · Region: $REGION · Mål: $TARGET · Konto: $ACCOUNT"

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
  FN_URL="https://${REGION}-${PROJECT_ID}.cloudfunctions.net/swehockey-stats-scraper"
  # Anropa funktionen direkt i stället för via schemaläggaren: då syns
  # resultatet här, inklusive hur många rader varje datatyp laddade.
  OUT=$(curl -sS --max-time 320 "$FN_URL" 2>/dev/null || echo '{}')
  printf '%s' "$OUT" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('  kunde inte tolka svaret'); raise SystemExit
print('  status:', d.get('status','?'))
for k,v in (d.get('types') or {}).items():
    mark='ok ' if v.get('ok') else 'FEL'
    print(f\"    {mark} {k:<14} {v.get('rows',0):>5} rader  {v.get('bq_loaded',0):>5} laddade\")
" || true
fi

if [[ "$TARGET" == "backfill" ]]; then
  say "Backfill av säsonger: $BACKFILL_SEASONS"
  FN_URL="https://${REGION}-${PROJECT_ID}.cloudfunctions.net/swehockey-stats-scraper"
  OUT=$(curl -sS --max-time 480 "${FN_URL}?seasons=${BACKFILL_SEASONS}" 2>/dev/null || echo '{}')
  printf '%s' "$OUT" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('  kunde inte tolka svaret'); raise SystemExit
print('  status:', d.get('status','?'))
for k,v in (d.get('types') or {}).items():
    mark='ok ' if v.get('ok') else 'FEL'
    line=f\"    {mark} {k:<14} {v.get('rows',0):>5} rader  {v.get('bq_loaded',0):>5} laddade\"
    if v.get('error'): line += '  ' + str(v['error'])[:70]
    print(line)
" || true
  say "Klart"
  exit 0
fi

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  URL=$(gcloud run services describe loven-stats-api --region "$REGION" --format='value(status.url)')
  say "Kontrollerar $URL"
  # API:t svarar 200 aven nar det misslyckats internt, med {"status":"error"}
  # i kroppen. Kontrollera darfor innehallet, inte bara HTTP-koden.
  PROBLEM=0
  for ep in /api/v1/health /api/v1/standings /api/v1/roster; do
    BODY=$(curl -sS --max-time 90 "${URL}${ep}" 2>/dev/null || echo '{}')
    CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 90 "${URL}${ep}" 2>/dev/null || echo 000)
    if [[ "$BODY" == *'"status": "error"'* || "$BODY" == *'"status":"error"'* ]]; then
      REASON=$(printf '%s' "$BODY" | sed -n 's/.*"error": *"\([^"]\{0,110\}\).*/\1/p')
      printf '  %-22s HTTP %s  \033[1;31mFEL\033[0m %s\n' "$ep" "$CODE" "$REASON"
      PROBLEM=1
    elif [[ "$CODE" != "200" ]]; then
      printf '  %-22s HTTP %s  \033[1;31mmisslyckades\033[0m\n' "$ep" "$CODE"
      PROBLEM=1
    else
      printf '  %-22s HTTP %s  ok\n' "$ep" "$CODE"
    fi
  done
  if [[ "$PROBLEM" == "1" ]]; then fail "Minst en endpoint svarar inte som den ska."; fi
fi

say "Klart"
