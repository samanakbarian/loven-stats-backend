"""Roktest: svarar sajten, och med riktigt innehall?

Byggd efter att frontendbygget gick sonder 8 september och Netlify stod still i
ett dygn utan att nagon markte det. Kor den nar som helst, och sarskilt efter en
deploy:

    python3 tests/roktest.py

Avslutar med 0 nar allt ar friskt och 1 annars, sa den gar att kora i CI eller
fran ett schemalagt jobb. Inga beroenden utover standardbiblioteket.

Bevisat att den faller: onabar sajt, sajt utan app-skal, och dott API ger alla
exit 1. Friskt lage ger 0.

Tva saker den INTE fangar, och bada ar medvetna:

  Ett GAMMALT bygge. Misslyckas bygget publicerar Netlify ingenting, och forra
  versionen star kvar och svarar 200 med riktigt innehall. Det felet fangas
  bara av att kora `npm run build` i CI vid varje push till slutspel/main.

  Fel ROUTE. netlify.toml har en catch-all som serverar app-skalet for varje
  adress, sa /finns-inte svarar 200 med samma HTML. Det ar ratt beteende for en
  SPA, men det betyder att den har kontrollen sager "sajten lever", inte
  "routingen stammer".
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Gar att peka nagon annanstans, bade for att testa testet och for att kora det
# mot en annan revision innan trafiken slapps pa.
API = os.environ.get("API_URL", "https://loven-stats-api-324947473206.europe-west1.run.app")
SAJT = os.environ.get("SITE_URL", "https://sida377.se")

# Tidsbudget per endpoint, i sekunder. Satt over uppmatt normalvarde med
# marginal — meningen ar att fanga en regression, inte att mata exakt.
# /analytics gick fran 14,3 till 4,0 sekunder 10 september.
BUDGET = {"analytics": 8.0, "match": 6.0, "_standard": 4.0}

fel: list[str] = []
varningar: list[str] = []


def hamta(url, timeout=30):
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")), time.time() - t0


def kolla(namn, url, krav, budget=None):
    """krav(data) -> None om allt ar bra, annars en strang som beskriver felet."""
    tak = budget or BUDGET["_standard"]
    try:
        data, sek = hamta(url)
    except Exception as e:
        fel.append(f"{namn}: {type(e).__name__} {str(e)[:60]}")
        print(f"  FEL   {namn:<22} {str(e)[:44]}")
        return None
    brist = krav(data)
    langsam = sek > tak
    if brist:
        fel.append(f"{namn}: {brist}")
    if langsam:
        varningar.append(f"{namn}: {sek:.1f}s over budget {tak:.0f}s")
    mark = "FEL " if brist else ("SEN " if langsam else "ok  ")
    print(f"  {mark}  {namn:<22} {sek:>5.2f}s  {brist or ''}")
    return data


def icke_tom(*nycklar):
    def krav(d):
        if d.get("status") not in (None, "ok"):
            return f"status={d.get('status')}"
        for n in nycklar:
            v = d
            for del_ in n.split("."):
                v = (v or {}).get(del_) if isinstance(v, dict) else None
            if not v:
                return f"{n} ar tom"
        return None
    return krav


print(f"Roktest mot {SAJT}\n")

# 1. Sajten sjalv. Ett trasigt bygge syns inte har, men en nere sajt gor det.
try:
    t0 = time.time()
    with urllib.request.urlopen(SAJT, timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
        sek = time.time() - t0
    saknas = [m for m in ('<div id="root"', "/assets/", "sida377") if m not in html]
    if r.status != 200 or saknas:
        fel.append(f"sajten: http {r.status}, saknar {saknas}")
        print(f"  FEL   {'sida377.se':<22} {sek:>5.2f}s  saknar {saknas}")
    else:
        print(f"  ok    {'sida377.se':<22} {sek:>5.2f}s  {len(html)} tecken")
except Exception as e:
    fel.append(f"sajten: {e}")
    print(f"  FEL   {'sida377.se':<22}        {str(e)[:44]}")

# 2. API:t.
kolla("health", f"{API}/api/v1/health", lambda d: None if d.get("status") == "healthy" else "ohalsosam")
kolla("statistics", f"{API}/api/v1/statistics", icke_tom("record"))
kolla("standings", f"{API}/api/v1/standings", icke_tom("standings"))
kolla("next-match", f"{API}/api/v1/next-match", lambda d: None)
kolla("feed", f"{API}/api/v1/feed?limit=20", icke_tom("items"))
kolla("analytics", f"{API}/api/v1/analytics", icke_tom("modules"), BUDGET["analytics"])

# 3. Sasongskonfigurationen — premiarkritisk. Fel aktiv sasong och hela
#    sajten visar fjolarets siffror utan att nagot ser trasigt ut.
sasonger = kolla("seasons", f"{API}/api/v1/seasons", icke_tom("seasons"))
if sasonger:
    aktiv = sasonger.get("active")
    if aktiv != "shl_2627":
        fel.append(f"aktiv sasong ar {aktiv}, vantade shl_2627")
        print(f"  FEL   {'aktiv sasong':<22}        {aktiv}")
    else:
        print(f"  ok    {'aktiv sasong':<22}        {aktiv}")

# 4. Farskhet. snapshot_scraped_at duger INTE: den star stilla med flit nar
#    innehallet ar oforandrat, och hade larmat falskt varje dag fore
#    seriestart. meta.freshness_status ar API:ts egen bedomning.
lag = kolla("lovenlaget", f"{API}/api/v1/lovenlaget", icke_tom("meta"))
if lag:
    status = (lag.get("meta") or {}).get("freshness_status")
    if status != "fresh":
        varningar.append(f"farskhet: {status}")
        print(f"  SEN   {'farskhet':<22}        {status}")
    else:
        print(f"  ok    {'farskhet':<22}        {status}")

print()
if varningar:
    print("Varningar:")
    for v in varningar:
        print(f"  · {v}")
if fel:
    print(f"\n{len(fel)} FEL:")
    for f in fel:
        print(f"  · {f}")
    sys.exit(1)
print("Allt friskt." + ("  (se varningar ovan)" if varningar else ""))
sys.exit(0)

# Not: for att fanga ett trasigt BYGGE behovs `npm run build` i CI pa varje
# push till slutspel/main. Det har testet kor mot det som ar publicerat, och
# ett misslyckat bygge publicerar ingenting — den gamla versionen star kvar och
# svarar friskt.
