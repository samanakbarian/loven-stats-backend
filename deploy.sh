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
#   bash deploy.sh schedule   # sätt schemaläggningen, utan deploy
#   bash deploy.sh views      # skapa/uppdatera core- och marts-vyerna
#   bash deploy.sh restore-env # återställ miljövariabler från äldre revision
#
# Backfill kör scrapern mot säsonger som inte är markerade aktiva, så de får
# fält som lagts till i efterhand — game_id, periodresultat, publik, trupp.
# Säsongerna anges med BACKFILL_SEASONS, som standard HA 25/26 med slutspel.
# Backfill hämtar även matchhändelser för säsongens alla matcher, inklusive
# spelarna på isen vid varje mål.
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

# Cloud Shell tappar ibland vilket konto som är markerat som aktivt när
# sessionen startas om, och gcloud faller da pa "no active account selected".
# Finns det ett kant konto satts det tillbaka har.
#
# En tom `gcloud auth list` betyder daremot inte att inloggningen ar borta:
# Cloud Shell autentiserar via sin egen kanal och svarar sjalv "you are
# already authenticated" om man forsoker logga in. Darfor varnar vi bara och
# later kommandot sjalvt avgora — annars stoppas en deploy som hade gatt bra.
ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)
if [[ -z "$ACCOUNT" ]]; then
  KNOWN=$(gcloud auth list --format='value(account)' 2>/dev/null | head -1)
  if [[ -n "$KNOWN" ]]; then
    say "Ingen aktiv inloggning — återställer $KNOWN"
    gcloud config set account "$KNOWN" --quiet >/dev/null
    ACCOUNT="$KNOWN"
  fi
fi

say "Projekt: $PROJECT_ID · Region: $REGION · Mål: $TARGET · Konto: ${ACCOUNT:-okänt}"

# Nar gcloud inte kommer at nagon inloggning ar felet detsamma oavsett
# kommando, och det dyker upp forst efter att bygget har startat. Den har
# raden kostar en sekund och sager direkt vad som galler.
if ! gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
  fail "gcloud kommer inte åt projektet. Vanligast är att Cloud Shell tappat
sin inloggning när sessionen startades om. Starta om Cloud Shell (menyn uppe
till höger → Restart) och kör samma kommando igen. Hjälper inte det:

  gcloud auth login"
fi

if [[ "$TARGET" == "views" ]]; then
  # raw_sports ar append-only och bar historiken. Varje lasning maste valja
  # senaste generationen, och det har gatt fel tva ganger. core-vyerna gor
  # avdupliceringen pa ett stalle sa att API:t inte kan glomma den.
  command -v bq >/dev/null || fail "bq saknas. Kör från Cloud Shell."
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  # Ordningen spelar roll: marts laser core, och dim_team laser dim_game.
  for f in core_views marts; do
    sed "s/@PROJECT@/${PROJECT_ID}/g" "sql/${f}.sql" > "$TMP/${f}.sql"
    say "Skapar vyerna i ${f/core_views/core}"
    bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --quiet \
      --format=none < "$TMP/${f}.sql"
  done

  # Rakna rader i bastabellen mot vyn. Skillnaden ar de generationer som
  # avdupliceringen sorterar bort — ar den noll skrivs ingenting i onodan,
  # och ar vyn tom nar tabellen inte ar det stammer inte nyckeln.
  say "Jämför rader: raw_sports mot core"
  {
    first=1
    while read -r raw view; do
      [[ -z "$raw" ]] && continue
      [[ $first == 1 ]] || printf ' UNION ALL '
      first=0
      printf "SELECT '%s' AS tabell, (SELECT COUNT(*) FROM \`%s.raw_sports.%s\`) AS raw_rader, (SELECT COUNT(*) FROM \`%s.core.%s\`) AS core_rader" \
        "$view" "$PROJECT_ID" "$raw" "$PROJECT_ID" "$view"
    done <<'TABLES'
swehockey_game_events game_events
swehockey_game_summary game_team_summary
swehockey_game_goalies game_goalies
swehockey_game_lineups game_lineups
swehockey_schedule schedule
swehockey_standings standings
swehockey_player_stats player_season_stats
swehockey_goalie_stats goalie_season_stats
swehockey_roster roster
TABLES
    printf ' ORDER BY tabell'
  } > "$TMP/counts.sql"
  bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=json --quiet \
    < "$TMP/counts.sql" | python3 -c "
import json, sys
try: rows = json.load(sys.stdin)
except Exception: print('  kunde inte tolka svaret'); raise SystemExit
print(f\"  {'vy':<22}{'raw':>10}{'core':>10}{'bortsorterat':>14}\")
for r in rows:
    a, b = int(r['raw_rader']), int(r['core_rader'])
    flag = '  <-- vyn är tom' if a and not b else ''
    print(f\"  {r['tabell']:<22}{a:>10}{b:>10}{a-b:>14}{flag}\")
" || true
  say "Klart"
  exit 0
fi

if [[ "$TARGET" == "schedule" ]]; then
  # Swehockey publicerar matchrapporten en kvart till tre kvart efter
  # slutsignal. Matt over arton matcher lag rapporten uppe 137-195 minuter
  # efter nedslapp. Med avslag 15:15, 16:00, 19:00 och 20:30 ger det:
  #
  #   18:30  eftermiddagsmatcherna ar inne
  #   22:30  kvallsmatcherna (19:00-avslag) ar inne
  #   00:30  sena avslag och matcher som drog ut
  #   07:30  rattelser som kom under natten, och tabellen infor dagen
  #
  # Veckokorningen den ersatter lamnade en tisdagsmatch osedd i sex dygn.
  # Fyra korningar kostar nastan ingenting nu nar matchsidorna hamtas
  # inkrementellt: en korning utan nya matcher ror bara schema, tabell och
  # spelarstatistik.
  CRON="${SCRAPER_CRON:-30 0,7,18,22 * * *}"
  FN_URL="https://${REGION}-${PROJECT_ID}.cloudfunctions.net/swehockey-stats-scraper"
  say "Sätter schemat: $CRON (Europe/Stockholm)"
  if gcloud scheduler jobs describe swehockey-stats-scraper-job \
       --location "$REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http swehockey-stats-scraper-job \
      --location "$REGION" \
      --schedule "$CRON" \
      --time-zone "Europe/Stockholm" \
      --uri "$FN_URL" \
      --http-method GET \
      --attempt-deadline 320s \
      --quiet
  else
    gcloud scheduler jobs create http swehockey-stats-scraper-job \
      --location "$REGION" \
      --schedule "$CRON" \
      --time-zone "Europe/Stockholm" \
      --uri "$FN_URL" \
      --http-method GET \
      --attempt-deadline 320s \
      --quiet
  fi
  gcloud scheduler jobs describe swehockey-stats-scraper-job --location "$REGION" \
    --format='value[separator="  "](schedule, timeZone, state, scheduleTime)'
  say "Klart"
  exit 0
fi

if [[ "$TARGET" == "restore-env" ]]; then
  # `--set-env-vars` satte hela uppsattningen och raderade allt som inte stod
  # i kommandot — bland annat X_BEARER_TOKEN, sa X-flodet slutade ge tweets.
  # Deployen anvander numera `--update-env-vars`, men variablerna som redan
  # forsvunnit maste hamtas tillbaka. Cloud Run sparar tidigare revisioner,
  # och dar ligger de kvar.
  say "Letar efter en revision med X_BEARER_TOKEN"
  WANT="${RESTORE_KEY:-X_BEARER_TOKEN}"
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  FOUND=""

  for REV in $(gcloud run revisions list --service loven-stats-api --region "$REGION" \
                 --format='value(metadata.name)' --sort-by='~metadata.creationTimestamp' \
                 --limit 40 2>/dev/null); do
    # Vardena skrivs till fil och aldrig till terminalen — en bearer-token
    # ska inte hamna i skarmhistoriken.
    if gcloud run revisions describe "$REV" --region "$REGION" --format=json 2>/dev/null \
       | WANT="$WANT" OUT="$TMP/env.yaml" python3 -c "
import json, os, sys
d = json.load(sys.stdin)
env = (d.get('spec', {}).get('containers') or [{}])[0].get('env') or []
vals = {e['name']: e['value'] for e in env if e.get('value') is not None}
if os.environ['WANT'] not in vals:
    raise SystemExit(1)
# JSON ar giltig YAML, sa filen duger till --env-vars-file utan PyYAML.
with open(os.environ['OUT'], 'w') as f:
    json.dump(vals, f, ensure_ascii=False)
print(' '.join(sorted(vals)))
" > "$TMP/names.txt"; then
      FOUND="$REV"
      break
    fi
  done

  if [[ -z "$FOUND" ]]; then
    fail "Ingen av de 40 senaste revisionerna har $WANT. Variabeln måste sättas
för hand:

  gcloud run services update loven-stats-api --region $REGION \\
    --update-env-vars X_BEARER_TOKEN=DITT_TOKEN"
  fi

  say "Hittade i revision $FOUND"
  printf '  variabler: %s\n' "$(cat "$TMP/names.txt")"

  # --env-vars-file satter hela uppsattningen, precis som --set-env-vars. Den
  # gamla revisionen kanner inte nodvandigtvis dagens variabler, sa de laggs
  # tillbaka i ett andra steg.
  gcloud run services update loven-stats-api --region "$REGION" \
    --env-vars-file "$TMP/env.yaml" --quiet
  gcloud run services update loven-stats-api --region "$REGION" \
    --update-env-vars "BQ_PROJECT_ID=${PROJECT_ID},GCS_BUCKET_NAME=${BUCKET}" --quiet

  # Variabler som pekar pa Secret Manager har inget varde i revisionen och
  # kan inte aterstallas har. Sag till i stallet for att tiga om dem.
  SECRETS=$(gcloud run revisions describe "$FOUND" --region "$REGION" --format=json 2>/dev/null \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
env = (d.get('spec', {}).get('containers') or [{}])[0].get('env') or []
print(' '.join(e['name'] for e in env if e.get('value') is None))
" 2>/dev/null || true)
  if [[ -n "${SECRETS// /}" ]]; then
    printf '  \033[1;33mobs\033[0m: hämtas från Secret Manager och måste sättas för hand: %s\n' "$SECRETS"
  fi
  say "Återställt. Kontrollerar X-flödet"
  URL=$(gcloud run services describe loven-stats-api --region "$REGION" --format='value(status.url)')
  # force_refresh kringgar den cachade blobben. Utan den serveras svaret som
  # skrevs medan tokenet saknades, och en lyckad aterstallning ser ut att ha
  # misslyckats i en timme till.
  curl -sS --max-time 120 "${URL}/api/v1/x-feed?force_refresh=true" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('  kunde inte tolka svaret'); raise SystemExit
err = (d.get('meta') or {}).get('error')
print(f\"  tweets: {d.get('count', 0)}\" + (f'  fel: {err}' if err else ''))
" || true
  exit 0
fi

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  say "Deployar API:t till Cloud Run"
  gcloud run deploy loven-stats-api \
    --source api \
    --region "$REGION" \
    --allow-unauthenticated \
    --update-env-vars "BQ_PROJECT_ID=${PROJECT_ID},GCS_BUCKET_NAME=${BUCKET}" \
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
    --update-env-vars "GCP_PROJECT=${PROJECT_ID},GCS_BUCKET=${BUCKET},SWEHOCKEY_TEAM_ID=1139,SWEHOCKEY_SEASON_GROUP_ID=20961" \
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
    note='  oförändrad, inget skrivet' if v.get('unchanged') else ''
    print(f\"    {mark} {k:<14} {v.get('rows',0):>5} rader  {v.get('bq_loaded',0):>5} laddade{note}\")
for c in (d.get('reconciliation') or []):
    ok = c.get('ok')
    mark = 'ok ' if ok else ('  ?' if ok is None else 'AVVIKER')
    extra = '' if ok else f\"  {c.get('observed')} mot {c.get('expected')}  {c.get('note','')}\"
    print(f\"    {mark} {c['name']}{extra}\")
" || true
fi

if [[ "$TARGET" == "backfill" ]]; then
  say "Backfill av säsonger: $BACKFILL_SEASONS"
  FN_URL="https://${REGION}-${PROJECT_ID}.cloudfunctions.net/swehockey-stats-scraper"
  # events_limit=all hamtar handelser for sasongens alla matcher. En vanlig
  # korning nojer sig med de senaste, eftersom handelsesidan maste hamtas en
  # match i taget och tar ungefar en sekund styck.
  OUT=$(curl -sS --max-time 540 "${FN_URL}?seasons=${BACKFILL_SEASONS}&events_limit=all" 2>/dev/null || echo '{}')
  printf '%s' "$OUT" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('  kunde inte tolka svaret'); raise SystemExit
print('  status:', d.get('status','?'))
for k,v in (d.get('types') or {}).items():
    mark='ok ' if v.get('ok') else 'FEL'
    line=f\"    {mark} {k:<14} {v.get('rows',0):>5} rader  {v.get('bq_loaded',0):>5} laddade\"
    if v.get('unchanged'): line += '  oförändrad, inget skrivet'
    if v.get('error'): line += '  ' + str(v['error'])[:70]
    print(line)
for c in (d.get('reconciliation') or []):
    ok = c.get('ok')
    mark = 'ok ' if ok else ('  ?' if ok is None else 'AVVIKER')
    extra = '' if ok else f\"  {c.get('observed')} mot {c.get('expected')}  {c.get('note','')}\"
    print(f\"    {mark} {c['name']}{extra}\")
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
