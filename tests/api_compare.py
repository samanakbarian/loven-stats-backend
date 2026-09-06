"""Jamfor API:ts svar mot ett facit taget fore en andring.

Anvands for refaktoreringar som inte ska andra nagot — som flytten fran
raw_sports till core. Ta facit fore, deploya, jamfor efter: varje skillnad
som inte star i IGNORE ar en regression.

    python3 tests/api_compare.py --save baseline/     # fore andringen
    python3 tests/api_compare.py --check baseline/    # efter deploy

refresh=1 kringgar API:ts sextimmarscache. Utan den jamfor man gamla svar
med gamla svar och far gront oavsett vad koden gor.

OBS: --save skriver over tests/api_baseline/, som ligger i repot. Kor den
bara nar facit ska flyttas fram, och committa resultatet — annars vagrar
nasta git pull med "local changes would be overwritten". Ligger andringarna
kvar och du inte vill ha dem: git checkout -- tests/api_baseline
"""
import argparse, json, os, sys, urllib.parse, urllib.request

API = os.environ.get("LOVEN_API", "https://loven-stats-api-324947473206.europe-west1.run.app")

ENDPOINTS = [
    "/api/v1/seasons",
    "/api/v1/standings?season=ha_2526&refresh=1",
    "/api/v1/roster?season=ha_2526&refresh=1",
    "/api/v1/players?season=ha_2526&refresh=1",
    "/api/v1/goalies?season=ha_2526&refresh=1",
    "/api/v1/shots?season=ha_2526&refresh=1",
    "/api/v1/onice?season=ha_2526&refresh=1",
    "/api/v1/analytics?season=ha_2526&refresh=1",
    "/api/v1/statistics?season=ha_2526&refresh=1",
    "/api/v1/lovenlaget",
    "/api/v1/player/Dower%20Nilsson,%20Liam?season=ha_2526&refresh=1",
    # Mora-Bjorkloven 1-2, avgjord pa straffar. Vald med flit: 1005612 var
    # MoDo-Ostersund, en match vi inte har handelser for, sa facit provade
    # bara tomvagen. Den har har bade mal, utvisningar, on-ice och det
    # straffavgorande malet som parsern tidigare tappade.
    "/api/v1/match/1005615",
]

# Falt som skiljer sig mellan tva identiska anrop, eller som andras av sig
# sjalva med tiden, och darfor inte sager nagot om en kodandring. Uppmatt
# genom att anropa varje endpoint tva ganger i rad — inte antaget.
IGNORE = (
    "ai_coach",          # sprakmodellsgenererad text
    "last_updated", "generated_at", "scraped_at", "cached_at", "source_updated_at",
    "new_signals",       # silly season-flodet far nya signaler over tid
)

# Slutplaceringen ar en Monte Carlo-simulering och ar inte jamforbar rad for rad.
SKIP = ("/api/v1/projection",)


def slug(ep):
    return "".join(c if c.isalnum() else "_" for c in ep).strip("_")[:80]


def fetch(ep):
    with urllib.request.urlopen(API + ep, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def canon(x):
    """Jamforbar form, med de flyktiga falten borttagna."""
    if isinstance(x, dict):
        return {k: canon(v) for k, v in sorted(x.items()) if not any(i in k for i in IGNORE)}
    if isinstance(x, list):
        return [canon(v) for v in x]
    return x


def same_multiset(a, b):
    """Samma rader, kanske i annan ordning.

    BigQuery garanterar ingen radordning utan ORDER BY, och en lista sorterad
    pa ett tal med lika varden kan darfor komma i olika ordning mellan tva
    korningar. Det ar inte samma sak som att innehallet andrats, och det ska
    inte rapporteras som en regression — men det ska synas, for en lista som
    kapas vid topp fem far olika innehall beroende pa ordningen.
    """
    try:
        return sorted(json.dumps(canon(v), sort_keys=True, default=str) for v in a) == \
               sorted(json.dumps(canon(v), sort_keys=True, default=str) for v in b)
    except Exception:
        return False


def diff(a, b, path=""):
    if any(k in path for k in IGNORE):
        return []
    if type(a) is not type(b):
        return [f"{path}: {type(a).__name__} -> {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: tillkommen")
            elif k not in b:
                out.append(f"{path}.{k}: borta")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} rader -> {len(b)}"]
        if canon(a) != canon(b) and same_multiset(a, b):
            return [f"~{path}: samma {len(a)} rader, annan ordning"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="DIR")
    ap.add_argument("--check", metavar="DIR")
    args = ap.parse_args()
    out = args.save or args.check
    if not out:
        ap.error("ange --save eller --check")
    os.makedirs(out, exist_ok=True)

    problems = 0
    for ep in ENDPOINTS:
        if any(ep.startswith(s) for s in SKIP):
            continue
        path = os.path.join(out, slug(ep) + ".json")
        try:
            got = fetch(ep)
        except Exception as e:
            print(f"  FEL  {ep.split('?')[0]:<44} {str(e)[:60]}")
            problems += 1
            continue
        if args.save:
            with open(path, "w") as f:
                json.dump(got, f, ensure_ascii=False, sort_keys=True)
            print(f"  sparat  {ep.split('?')[0]:<42} {len(json.dumps(got)):>8} tecken")
            continue
        if not os.path.exists(path):
            print(f"  saknas i facit: {ep}")
            continue
        want = json.load(open(path))
        d = diff(want, got)
        real = [x for x in d if not x.startswith("~")]
        order = [x for x in d if x.startswith("~")]
        mark = "SKILJER" if real else ("ordning" if order else "ok ")
        note = f"{len(real):>4} skillnader" + (f", {len(order)} omsorterade" if order else "")
        print(f"  {mark:<8}{ep.split('?')[0]:<42}{note}")
        for line in (real + order)[:8]:
            print(f"        {line[:150]}")
        problems += bool(real)

    if args.check:
        if problems:
            print(f"\n{problems} endpoints har ändrat innehåll. Varje rad utan ~ är en regression.")
        else:
            print("\nInget innehåll har ändrats. Rader med ~ är samma data i annan ordning.")
    return 1 if (args.check and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
