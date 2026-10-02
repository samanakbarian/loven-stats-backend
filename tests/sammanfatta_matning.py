"""Sammanfattar loggraderna från S2.1: tid, BigQuery-frågor och cache per väg.

Läser `gcloud logging read --format=json` på stdin. Kommandot står i
docs/serveringslager/MATNING.md. Kör från Cloud Shell:

    gcloud logging read '...' --format=json --limit=20000 \\
      | python3 tests/sammanfatta_matning.py

Värmningens egna anrop (`intern`) räknas för sig, så att besökarnas tider
inte späds ut av anrop som ingen väntade på.
"""
import json
import sys
from collections import defaultdict


def percentil(varden: list[int], p: float) -> int:
    """Närmaste-rang-percentil. Tom lista ger 0."""
    if not varden:
        return 0
    s = sorted(varden)
    i = max(0, min(len(s) - 1, -(-len(s) * p // 100) - 1))
    return s[int(i)]


def rader(poster: list[dict]) -> list[dict]:
    """Plockar ut S2.1-raderna, oavsett om de kommer råa eller som loggposter."""
    ut = []
    for p in poster:
        r = p.get("jsonPayload", p)
        if r.get("message") == "anrop":
            ut.append(r)
    return ut


def sammanfatta(poster: list[dict]) -> list[dict]:
    grupper = defaultdict(list)
    for r in rader(poster):
        grupper[(r.get("vag", "?"), bool(r.get("intern")))].append(r)
    ut = []
    for (vag, intern), rr in grupper.items():
        ms = [int(r.get("ms", 0)) for r in rr]
        cache = defaultdict(int)
        for r in rr:
            cache[r.get("cache", "ingen")] += 1
        ut.append({
            "vag": vag,
            "intern": intern,
            "n": len(rr),
            "p50": percentil(ms, 50),
            "p95": percentil(ms, 95),
            "max": max(ms),
            "bq": sum(int(r.get("bq", 0)) for r in rr),
            "miss": cache["miss"] + cache["forbi"],
            "traff": cache["traff"],
            "fel": sum(1 for r in rr if int(r.get("status", 0)) >= 500),
        })
    ut.sort(key=lambda x: (x["intern"], -x["bq"], -x["n"]))
    return ut


def skriv_tabell(rad: list[dict]) -> str:
    huvud = f"{'väg':<34} {'n':>5} {'p50':>6} {'p95':>6} {'max':>6} {'bq':>5} {'miss':>5} {'träff':>5} {'5xx':>4}"
    linjer = [huvud, "-" * len(huvud)]
    for intern in (False, True):
        del_ = [r for r in rad if r["intern"] == intern]
        if not del_:
            continue
        if intern:
            linjer.append("\nintern (värmningen)")
        for r in del_:
            linjer.append(
                f"{r['vag'][:34]:<34} {r['n']:>5} {r['p50']:>6} {r['p95']:>6} "
                f"{r['max']:>6} {r['bq']:>5} {r['miss']:>5} {r['traff']:>5} {r['fel']:>4}"
            )
    return "\n".join(linjer)


if __name__ == "__main__":
    data = json.load(sys.stdin)
    print(skriv_tabell(sammanfatta(data)))
