"""Gyllene mastare for /api/v1/analytics.

Endpointen ar 1599 rader som raknar sjutton moduler, utan ett enda test. Den
behover skrivas om — tolv separata loopar over samma trettontusen handelserader
— men en omskrivning gar inte att gora tryggt utan nagot som sager om en siffra
tyst andrades.

    python3 tests/gyllene_master.py spara     # fore omskrivningen
    python3 tests/gyllene_master.py jamfor    # efter, sa ofta man vill

Undantag, och skalen till dem:

  ai_coach      LLM-genererad text, olik vid varje anrop. Kan inte jamforas.
  last_updated  tidsstampel, satts vid varje korning.

Talen jamfors med tolerans, eftersom flyttal kan skilja i sista biten nar en
summa raknas i annan ordning. Ordningen i listor DAREMOT jamfors strikt: byter
en sortering plats ar det en regression, aven om samma varden finns kvar.
"""
import json
import os
import sys
import urllib.request

API = os.environ.get("API_URL", "https://loven-stats-api-324947473206.europe-west1.run.app")
SASONGER = ("ha_2526", "ha_2324", "shl_2526")
HAR = os.path.dirname(os.path.abspath(__file__))
MAPP = os.path.join(HAR, "gyllene")
IGNORERA = {"ai_coach", "last_updated"}
TOLERANS = 1e-6


def hamta(sasong):
    url = f"{API}/api/v1/analytics?season={sasong}"
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def stada(o):
    """Plockar bort det som andras av sig sjalvt."""
    if isinstance(o, dict):
        return {k: stada(v) for k, v in o.items() if k not in IGNORERA}
    if isinstance(o, list):
        return [stada(x) for x in o]
    return o


def jamfor(a, b, vag=""):
    if isinstance(a, float) or isinstance(b, float):
        try:
            if abs(float(a) - float(b)) <= TOLERANS:
                return []
        except (TypeError, ValueError):
            pass
    if type(a) is not type(b):
        return [f"{vag}: typ {type(a).__name__} -> {type(b).__name__}"]
    if isinstance(a, dict):
        ut = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                ut.append(f"{vag}/{k}: TILLKOM")
            elif k not in b:
                ut.append(f"{vag}/{k}: FORSVANN")
            else:
                ut += jamfor(a[k], b[k], f"{vag}/{k}")
        return ut
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{vag}: {len(a)} rader -> {len(b)} rader"]
        ut = []
        for i, (x, y) in enumerate(zip(a, b)):
            ut += jamfor(x, y, f"{vag}[{i}]")
        return ut
    if a != b:
        return [f"{vag}: {str(a)[:45]!r} -> {str(b)[:45]!r}"]
    return []


def main():
    lage = sys.argv[1] if len(sys.argv) > 1 else "jamfor"
    os.makedirs(MAPP, exist_ok=True)
    avvikelser = 0

    for sasong in SASONGER:
        fil = os.path.join(MAPP, f"analytics_{sasong}.json")
        try:
            svar = stada(hamta(sasong))
        except Exception as e:
            print(f"  FEL  {sasong}: kunde inte hamta — {e}")
            avvikelser += 1
            continue

        if lage == "spara":
            with open(fil, "w", encoding="utf-8") as f:
                json.dump(svar, f, ensure_ascii=False, indent=1, sort_keys=True)
            n = len(svar.get("modules") or {})
            print(f"  sparad  {sasong:<10} {n} moduler  {os.path.getsize(fil)//1024} kB")
            continue

        if not os.path.exists(fil):
            print(f"  SAKNAS  {sasong}: kor 'spara' forst")
            avvikelser += 1
            continue
        with open(fil, encoding="utf-8") as f:
            fore = json.load(f)
        d = jamfor(fore, svar)
        if d:
            avvikelser += len(d)
            print(f"  AVVIKER {sasong}: {len(d)} skillnader")
            for rad in d[:15]:
                print(f"            {rad}")
            if len(d) > 15:
                print(f"            ... och {len(d)-15} till")
        else:
            print(f"  ok      {sasong:<10} identiskt")

    if lage == "spara":
        print("\nGyllene mastare sparad. Skriv om koden, kor sedan 'jamfor'.")
        return 0
    print("\nInga regressioner." if not avvikelser else f"\n{avvikelser} avvikelser — se ovan.")
    return 1 if avvikelser else 0


if __name__ == "__main__":
    sys.exit(main())
