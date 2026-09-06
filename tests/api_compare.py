"""Jamfor API:ts svar mot ett facit taget fore en andring.

Anvands for refaktoreringar som inte ska andra nagot — som flytten fran
raw_sports till core. Ta facit fore, deploya, jamfor efter: varje skillnad
som inte star i IGNORE ar en regression.

    python3 tests/api_compare.py --save baseline/     # fore andringen
    python3 tests/api_compare.py --check baseline/    # efter deploy

refresh=1 kringgar API:ts sextimmarscache. Utan den jamfor man gamla svar
med gamla svar och far gront oavsett vad koden gor.
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
    "/api/v1/match/1005612",
]

# Falt som skiljer sig mellan tva identiska anrop och darfor inte sager nagot.
# Uppmatt genom att anropa varje endpoint tva ganger i rad.
IGNORE = ("ai_coach", "last_updated", "generated_at", "scraped_at", "cached_at")

# Slutplaceringen ar en Monte Carlo-simulering och ar inte jamforbar rad for rad.
SKIP = ("/api/v1/projection",)


def slug(ep):
    return "".join(c if c.isalnum() else "_" for c in ep).strip("_")[:80]


def fetch(ep):
    with urllib.request.urlopen(API + ep, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


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
        mark = "ok " if not d else "SKILJER"
        print(f"  {mark:<8}{ep.split('?')[0]:<42}{len(d):>4} skillnader")
        for line in d[:8]:
            print(f"        {line[:150]}")
        problems += bool(d)

    if args.check:
        print("\nIngen skillnad — svaren är identiska." if not problems
              else f"\n{problems} endpoints skiljer sig. Varje rad ovan är en regression.")
    return 1 if (args.check and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
