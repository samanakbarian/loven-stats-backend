"""Jämför två API-adresser svar för svar, över hela säsonger.

Serveringslagrets grind (S1.2 i docs/serveringslager/PLAN.md). Används för att
visa att en kandidat svarar exakt som produktion innan något promotas:

    python3 tests/jamfor_svar.py --b https://kandidat---loven-stats-api-….run.app

Utan --b jämförs produktion mot sig själv. Det ska ge noll skillnader och är
hur verktyget självt valideras — visar det skillnader mot sig självt är det
verktyget som är fel, inte koden det ska granska.

Täcker säsongens endpoints och VARJE spelad match, inte ett urval. Två
skäl: matchrapporten är först ut att flyttas, och buggarna den här säsongen
satt i specialfall — en match där målskytten saknades bland spelarna på isen,
en där målvaktsraderna inte kommit in. Ett stickprov hade missat båda.

Vad som jämförs, och hur:

  Tal         med tolerans, eftersom en summa i annan ordning kan skilja i
              sista biten.
  Listor      strikt i ordning. Byter en sortering plats är det en
              regression även om samma värden finns kvar — tabellens
              skiljetal var precis den buggen.
  Ignoreras   tidsstämplar som sätts vid varje anrop. Listan står i IGNORERA,
              och varje rad där har ett skäl.

Inget refresh=1. Det är taktbegränsat till tolv i timmen och hade tagit slut
efter en säsong. Kör i stället strax efter omhämtningen (kvart i, efter
skördarna 00:30, 07:30, 18:30, 22:30) så att produktionens cache är färsk.
Skiljer något, kör om en gång innan du drar slutsatser: en skörd mitt i
körningen ger en skillnad i datat, inte i koden.

Taktbegränsningen är 120 anrop i minuten per adress. Verktyget håller sig på
ungefär hundra, räknat över båda adresserna.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROD = "https://loven-stats-api-324947473206.europe-west1.run.app"

# Nycklar som ändras av sig själva mellan två anrop med samma data.
IGNORERA = {
    "generated_at",      # sätts när svaret byggs
    "last_updated",      # dito, i äldre endpoints
    "snapshot_scraped_at",  # när ögonblicksbilden hämtades; kan skilja en skörd
    "ai_coach",          # LLM-text, olik vid varje anrop
    "stats_updated_at",  # körloggens tid, flyttar sig med skördarna
}
TOLERANS = 1e-6

SASONGSVAGAR = (
    "standings", "statistics", "players", "goalies", "lines", "onice",
    "shots", "analytics", "next-match", "table-history", "opponents",
)


def hamta(bas: str, vag: str, tidsgrans: int = 120) -> tuple[int, object]:
    url = f"{bas}{vag}"
    req = urllib.request.Request(url, headers={"User-Agent": "loven-jamfor-svar/1"})
    try:
        with urllib.request.urlopen(req, timeout=tidsgrans) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None


def stada(o):
    if isinstance(o, dict):
        return {k: stada(v) for k, v in o.items() if k not in IGNORERA}
    if isinstance(o, list):
        return [stada(x) for x in o]
    return o


def jamfor(a, b, vag: str = "") -> list[str]:
    """Skillnaderna mellan a och b, som läsbara rader med sökväg."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return [] if abs(float(a) - float(b)) <= TOLERANS else [f"{vag}: {a!r} → {b!r}"]
    if type(a) is not type(b):
        return [f"{vag}: typ {type(a).__name__} → {type(b).__name__}"]
    if isinstance(a, dict):
        ut = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                ut.append(f"{vag}.{k}: saknas i A")
            elif k not in b:
                ut.append(f"{vag}.{k}: saknas i B")
            else:
                ut.extend(jamfor(a[k], b[k], f"{vag}.{k}"))
        return ut
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{vag}: {len(a)} element → {len(b)}"]
        ut = []
        for i, (x, y) in enumerate(zip(a, b)):
            ut.extend(jamfor(x, y, f"{vag}[{i}]"))
        return ut
    return [] if a == b else [f"{vag}: {a!r} → {b!r}"]


class Takt:
    """Håller sig under taktbegränsningen räknat över båda adresserna."""

    def __init__(self, per_minut: int):
        self.mellan = 60.0 / per_minut
        self.senast = 0.0

    def vanta(self):
        nu = time.monotonic()
        kvar = self.senast + self.mellan - nu
        if kvar > 0:
            time.sleep(kvar)
        self.senast = time.monotonic()


def matcher_for(bas: str, sasong: str, takt: Takt) -> list[int]:
    takt.vanta()
    status, d = hamta(bas, f"/api/v1/statistics?season={sasong}")
    if status != 200 or not isinstance(d, dict):
        return []
    ids = []
    for g in d.get("games") or []:
        if g.get("game_id") and str(g.get("result") or "").strip():
            ids.append(int(g["game_id"]))
    return sorted(set(ids))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--a", default=PROD, help="referensen, normalt produktion")
    p.add_argument("--b", default=PROD, help="det som granskas, normalt kandidaten")
    p.add_argument("--sasonger", default="shl_2627,ha_2526")
    p.add_argument("--vagar", default=",".join(SASONGSVAGAR),
                   help="säsongsendpoints, kommaseparerade; tom för inga")
    p.add_argument("--matcher", default="alla",
                   help="'alla', 'inga', eller ett tal för de N senaste per säsong")
    p.add_argument("--per-minut", type=int, default=100)
    p.add_argument("--visa", type=int, default=8, help="skillnader att skriva ut per svar")
    args = p.parse_args()

    takt = Takt(args.per_minut)
    sasonger = [s for s in args.sasonger.split(",") if s]
    vagar = [v for v in args.vagar.split(",") if v]

    jobb: list[str] = ["/api/v1/seasons"]
    for s in sasonger:
        jobb += [f"/api/v1/{v}?season={s}" for v in vagar]
        if args.matcher != "inga":
            ids = matcher_for(args.a, s, takt)
            if args.matcher != "alla":
                ids = ids[-int(args.matcher):]
            print(f"{s}: {len(ids)} spelade matcher")
            jobb += [f"/api/v1/match/{gid}" for gid in ids]

    print(f"A {args.a}")
    print(f"B {args.b}")
    print(f"{len(jobb)} svar att jämföra, ungefär {len(jobb) * 2 * 60 // args.per_minut // 60 + 1} min\n")

    lika = olika = fel = 0
    for vag in jobb:
        takt.vanta()
        sa, da = hamta(args.a, vag)
        takt.vanta()
        sb, db = hamta(args.b, vag)
        if sa != 200 or sb != 200:
            fel += 1
            print(f"  FEL    {vag}  HTTP {sa} / {sb}")
            continue
        diff = jamfor(stada(da), stada(db))
        if diff:
            olika += 1
            print(f"  OLIKA  {vag}  {len(diff)} skillnader")
            for rad in diff[: args.visa]:
                print(f"           {rad}")
        else:
            lika += 1

    print(f"\n{lika} lika, {olika} olika, {fel} fel av {len(jobb)}")
    return 0 if olika == 0 and fel == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
