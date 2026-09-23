"""Provar matchmodellen mot tidigare SHL-säsonger, speldag för speldag.

Före varje speldag skattas modellen på allt som spelats innan, och dagens
matcher spås. Inget från framtiden läcker in. Jämförs mot:

  - basfrekvens: seriens andel hemmavinster, förlängningar och bortavinster
    hittills, samma för varje match
  - Elo: simuleringens nuvarande modell (K 20, hemma 40, alla börjar på 1500
    varje säsong, förlängning med seriens andel)

Mått: log-loss och Brier på tre utfall (hemmavinst i ordinarie tid,
förlängning, bortavinst i ordinarie tid), och hur ofta matchens vinnare
tippas rätt.

Parametrarna ställs in på 2016/17–2021/22 och bedöms på 2022/23–2025/26,
som inställningen aldrig sett.

Körning:
    python3 scripts/backtest_matchmodell.py <katalog med säsongs-json>

Katalogen har en fil per säsong, {"id": ..., "games": [...]}, med
spelschemats rader som scrapern ger dem (_fetch_schedule).
"""

from __future__ import annotations

import glob
import json
import math
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import matchmodell as mm  # noqa: E402


def las(katalog: str) -> list[tuple[str, list[mm.Match]]]:
    ut = []
    for f in sorted(glob.glob(os.path.join(katalog, "*.json"))):
        namn = os.path.basename(f)[:7]
        matcher = []
        for g in json.load(open(f, encoding="utf-8"))["games"]:
            o = mm.ordinarie(g.get("result"), g.get("period_results"))
            if o is None:
                continue
            h, a, vidare = o
            slut = [int(x) for x in str(g.get("result") or "").replace(" ", "").split("-")[:2]]
            matcher.append(mm.Match(
                vann_hemma_ot=(slut[0] > slut[1]) if vidare else None,
                dag=date.fromisoformat(str(g["match_date"])[:10]),
                hemma=mm.lagnyckel(g["home_team"]), borta=mm.lagnyckel(g["away_team"]),
                h=h, a=a, vidare=vidare,
            ))
        if matcher:
            ut.append((namn, sorted(matcher, key=lambda m: m.dag)))
    return ut


def facit(m: mm.Match) -> int:
    """0 hemmavinst i ordinarie tid, 1 förlängning, 2 bortavinst."""
    if m.vidare or m.h == m.a:
        return 1
    return 0 if m.h > m.a else 2


def elo_prognos(sasong: list[mm.Match], dag: date, ot_default=0.2):
    K, HFA = 20, 40
    elo: dict[str, float] = {}
    ot = spelade = 0
    for m in sasong:
        if m.dag >= dag:
            break
        eh, ea = elo.setdefault(m.hemma, 1500.0), elo.setdefault(m.borta, 1500.0)
        f = facit(m)
        vann_hemma = (m.h > m.a) if f != 1 else None
        if f == 1:
            ot += 1
            # Förlängningens vinnare syns inte i ordinarie mål; halva.
            s = 0.5
        else:
            s = 1.0 if vann_hemma else 0.0
        e = 1 / (1 + 10 ** ((ea - (eh + HFA)) / 400))
        elo[m.hemma] = eh + K * (s - e)
        elo[m.borta] = ea + K * ((1 - s) - (1 - e))
        spelade += 1
    rate = ot / spelade if spelade >= 20 else ot_default

    def p(h, a):
        e = 1 / (1 + 10 ** ((elo.get(a, 1500) - (elo.get(h, 1500) + HFA)) / 400))
        return [(1 - rate) * e, rate, (1 - rate) * (1 - e)]
    return p


def kor(sasonger, par: mm.Parametrar, fran: int, till: int, bara_nykomlingar=False):
    alla = [m for _, s in sasonger for m in s]
    res = {"modell": [], "bas": [], "elo": []}
    for si in range(fran, till):
        namn, sasong = sasonger[si]
        nyk = mm.nykomlingar_for([s for _, s in sasonger], si)
        dagar = sorted({m.dag for m in sasong})
        for d in dagar:
            idag = [m for m in sasong if m.dag == d]
            # Tre säsonger bakåt räcker: äldre väger under en procent.
            hist = [m for m in alla if m.dag < d and (d - m.dag).days < 3 * 365]
            s = mm.skatta(hist, d, par, nyk)
            fore = [m for m in hist]
            n0 = sum(1 for m in fore if facit(m) == 0)
            n1 = sum(1 for m in fore if facit(m) == 1)
            n2 = sum(1 for m in fore if facit(m) == 2)
            tot = max(1, n0 + n1 + n2)
            bas = [n0 / tot, n1 / tot, n2 / tot]
            elo = elo_prognos(sasong, d)
            for m in idag:
                if bara_nykomlingar and not (m.hemma in nyk or m.borta in nyk):
                    continue
                u = mm.utfall(s, m.hemma, m.borta, par)
                pm = [u["hemma_ordinarie"], u["forlangning"], u["borta_ordinarie"]]
                f = facit(m)
                res["modell"].append((pm, f, m))
                res["bas"].append((bas, f, m))
                res["elo"].append((elo(m.hemma, m.borta), f, m))
    return res


def matt(rader, ot_hemma=0.52):
    if not rader:
        return {}
    ll = br = ratt = 0.0
    for p, f, m in rader:
        p = [max(1e-6, x) for x in p]
        ll -= math.log(p[f])
        br += sum((p[i] - (1.0 if i == f else 0.0)) ** 2 for i in range(3))
        # Vinnaren, förlängning inräknad.
        hem = p[0] + p[1] * ot_hemma
        slut_hem = m.h > m.a if not m.vidare else None
        if slut_hem is not None:
            ratt += 1.0 if (hem >= 0.5) == slut_hem else 0.0
    n = len(rader)
    avgjorda = sum(1 for _, f, m in rader if not m.vidare)
    return {"n": n, "logloss": round(ll / n, 4), "brier": round(br / n, 4),
            "ratt_vinnare_ordinarie": round(ratt / max(1, avgjorda), 3)}


if __name__ == "__main__":
    katalog = sys.argv[1]
    sasonger = las(katalog)
    print("säsonger:", [n for n, _ in sasonger])
    par = mm.Parametrar()
    if len(sys.argv) > 2:
        par = mm.Parametrar(**json.loads(sys.argv[2]))
    i_test = next(i for i, (n, _) in enumerate(sasonger) if n == "2022-23")
    i_slut = next(i for i, (n, _) in enumerate(sasonger) if n == "2026-27") if any(n == "2026-27" for n, _ in sasonger) else len(sasonger)
    for etikett, a, b in (("inställning 2016/17–2021/22", 2, i_test), ("prov 2022/23–2025/26", i_test, i_slut)):
        r = kor(sasonger, par, a, b)
        print(etikett)
        for k in ("modell", "elo", "bas"):
            print(f"  {k:<7}", matt(r[k], par.ot_hemma))
