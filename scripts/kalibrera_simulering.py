"""Kalibrerar simuleringens osäkerhet (sigma) mot tidigare SHL-säsonger.

Vid tre tidpunkter per säsong simuleras resten, och slutpoängen jämförs
med intervallet p10–p90. Rätt sigma ger åtta träffar av tio.

    python3 scripts/kalibrera_simulering.py <katalog med säsongs-json>
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
sys.path.insert(0, os.path.dirname(__file__))
import matchmodell as mm  # noqa: E402
from backtest_matchmodell import las  # noqa: E402


def poang(m: mm.Match, vann_hemma_ot: bool | None) -> tuple[int, int]:
    if not m.vidare and m.h != m.a:
        return (3, 0) if m.h > m.a else (0, 3)
    return (2, 1) if vann_hemma_ot else (1, 2)


def kor(sasonger, fran, till, sigma, antal=1000):
    par = mm.Parametrar()
    alla = [m for _, s in sasonger for m in s]
    traff = tot = 0
    fel = []
    for si in range(fran, till):
        _, s = sasonger[si]
        slut = defaultdict(int)
        for m in s:
            h, a = poang(m, m.vann_hemma_ot)
            slut[m.hemma] += h
            slut[m.borta] += a
        for andel in (0.25, 0.5, 0.75):
            cut = s[int(len(s) * andel)].dag
            fore = [m for m in s if m.dag < cut]
            kvar = [(m.hemma, m.borta) for m in s if m.dag >= cut]
            start = defaultdict(int)
            for m in fore:
                h, a = poang(m, m.vann_hemma_ot)
                start[m.hemma] += h
                start[m.borta] += a
            for t in slut:
                start.setdefault(t, 0)
            sk = mm.skatta([m for m in alla if m.dag < cut and (cut - m.dag).days < 3 * 365], cut, par)
            res = mm.simulera(sk, kvar, dict(start), par, antal=antal, sigma=sigma, seed=si)
            for t, r in res.items():
                v = sorted(r["poang"])
                p10, p90 = v[int(0.1 * len(v))], v[int(0.9 * len(v)) - 1]
                traff += p10 <= slut[t] <= p90
                tot += 1
                fel.append(abs(sum(v) / len(v) - slut[t]))
    return traff / tot, sum(fel) / len(fel)
