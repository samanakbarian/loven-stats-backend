"""Matchmodell: sannolikheten för varje utfall i en kommande match.

Poissonmodell för mål i ordinarie tid. Varje lag har en anfalls- och en
försvarsstyrka, hemmalaget ett tillägg:

    log(mål hemma) = nivå + hemma + anfall[hemma] - försvar[borta]
    log(mål borta) = nivå          + anfall[borta] - försvar[hemma]

Styrkorna skattas om före varje speldag ur alla tidigare matcher, där en
match väger mindre ju äldre den är. Så lär sig modellen av varje ny omgång
utan att glömma förra säsongen på en gång, och ett nytt lag får sin egen
bild efter hand.

Ett lag utan förra säsongen i serien — nyuppflyttat — dras mot hur
nyuppflyttade lag brukar spela sin första säsong, inte mot seriens mitt.
Den dragningen mäts ur historiken (nykomlingsstart) i stället för att
antas.

Oavgjort efter full tid betyder förlängning. Vem som vinner den sätts till
ett fast hemmaövertag ur historiken; förlängning och straffar är nära
slump.

Rena funktioner och numpy, så modellen går att prova mot sparade säsonger.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np

MAX_MAL = 12

# Två stavningar av samma klubb i Swehockeys historik.
_ALIAS = {"färjestads bk": "färjestad bk", "modo hockey": "modo hockey"}


def lagnyckel(namn: str) -> str:
    k = " ".join(str(namn or "").lower().split())
    # Swehockey har skrivit notiser i lagfältet: "Färjestad BK Matchstart ca
    # 20.30" i 2018/19. Utan tvätten blev det ett fjortonde lag.
    k = re.sub(r"\s+matchstart\b.*$", "", k)
    return _ALIAS.get(k, k)


def ordinarie(result: str | None, periods: str | None) -> tuple[int, int, bool] | None:
    """Mål efter tre perioder, och om matchen gick vidare.

    Slutresultatet räknar med avgörandet i förlängning eller straffar. För
    en målmodell är det fel mål: det sista kom i en annan matchform.
    """
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", str(result or ""))
    if not m:
        return None
    par = re.findall(r"(\d+)\s*-\s*(\d+)", str(periods or ""))
    if len(par) >= 3:
        h = sum(int(a) for a, _ in par[:3])
        a = sum(int(b) for _, b in par[:3])
        return h, a, len(par) > 3
    h, a = int(m.group(1)), int(m.group(2))
    return h, a, False


@dataclass
class Match:
    dag: date
    hemma: str
    borta: str
    h: int
    a: int
    vidare: bool
    vikt_extra: float = 1.0
    # Vem som vann efter full tid, när matchen gick vidare. Behövs för
    # poängen, inte för målmodellen.
    vann_hemma_ot: bool | None = None


@dataclass
class Parametrar:
    """Inställda på SHL 2016/17–2021/22, provade på 2022/23–2025/26.

    Se scripts/backtest_matchmodell.py. På provåren: log-loss 1,046 mot Elos
    1,057 och basfrekvensens 1,060, och välkalibrerad — sade modellen 55 %
    vann laget 55 % av gångerna.
    """
    # Halveringstid i dagar för en matchs vikt.
    halvering: float = 240.0
    # Hur hårt en styrka dras mot sitt förväntade värde. Större = försiktigare.
    krympning: float = 40.0
    # Förväntad styrka för ett nyuppflyttat lag, (anfall, försvar).
    #
    # Noll, alltså ett genomsnittligt lag. Nykomlingarna 2015–2022 var klart
    # svagare (−0,10 och −0,16 i snitt), men de fyra senaste — HV71, MoDo,
    # Brynäs, Djurgården — låg nära mitten, och en start ur historiken gjorde
    # prognosen sämre för deras matcher: log-loss 1,071 mot 1,059 utan.
    # Värt att pröva igen när det finns fler nykomlingar att lära av.
    nykomling: tuple[float, float] = (0.0, 0.0)
    # Krympningen för nyuppflyttade lag. Lägre än för övriga: spridningen
    # mellan nykomlingar är stor, så deras egna matcher ska ta över fortare.
    krympning_nykomling: float | None = None
    # Andel förlängningar hemmalaget vinner: 53,1 % av 531 i SHL 2015–2022.
    ot_hemma: float = 0.531
    # Extra sannolikhet för lika efter full tid, utöver oberoende Poisson.
    # Med den spår modellen förlängning i 20,6 % av matcherna; det blev 21,6.
    lika_extra: float = 0.04


@dataclass
class Skattning:
    niva: float
    hemma: float
    anfall: dict[str, float] = field(default_factory=dict)
    forsvar: dict[str, float] = field(default_factory=dict)
    matcher: dict[str, float] = field(default_factory=dict)


def skatta(matcher: list[Match], pa: date, par: Parametrar, nykomlingar: set[str] = frozenset()) -> Skattning:
    """Styrkorna som de såg ut dagen `pa`, ur matcher spelade före den.

    Viktad Poissonregression med ridge mot förväntat värde, löst med Newton.
    """
    tidigare = [m for m in matcher if m.dag < pa]
    lag = sorted({m.hemma for m in tidigare} | {m.borta for m in tidigare} | set(nykomlingar))
    if not lag:
        return Skattning(niva=math.log(1.4), hemma=0.1)
    ix = {t: i for i, t in enumerate(lag)}
    n = len(lag)
    # Parametrar: nivå, hemma, anfall[n], försvar[n].
    P = 2 + 2 * n
    rader, y, w = [], [], []
    for m in tidigare:
        vikt = 0.5 ** ((pa - m.dag).days / par.halvering) * m.vikt_extra
        hi, ai = ix[m.hemma], ix[m.borta]
        rader.append((1, 1, hi, ai)); y.append(m.h); w.append(vikt)
        rader.append((1, 0, ai, hi)); y.append(m.a); w.append(vikt)
    N = len(rader)
    X = np.zeros((N, P))
    for r, (niva, hemma, at, df) in enumerate(rader):
        X[r, 0] = niva
        X[r, 1] = hemma
        X[r, 2 + at] = 1.0
        X[r, 2 + n + df] = -1.0
    y_ = np.array(y, float)
    w_ = np.array(w, float)

    prior = np.zeros(P)
    for t in nykomlingar:
        if t in ix:
            prior[2 + ix[t]] = par.nykomling[0]
            prior[2 + n + ix[t]] = par.nykomling[1]
    pen = np.full(P, par.krympning)
    pen[0] = pen[1] = 1e-6
    if par.krympning_nykomling is not None:
        for t in nykomlingar:
            if t in ix:
                pen[2 + ix[t]] = pen[2 + n + ix[t]] = par.krympning_nykomling

    beta = prior.copy()
    beta[0] = math.log(max(0.5, float(np.average(y_, weights=w_)) if N else 1.4))
    for _ in range(30):
        eta = X @ beta
        mu = np.exp(eta)
        grad = X.T @ (w_ * (y_ - mu)) - pen * (beta - prior)
        H = (X * (w_ * mu)[:, None]).T @ X + np.diag(pen)
        steg = np.linalg.solve(H, grad)
        beta += steg
        if np.max(np.abs(steg)) < 1e-7:
            break

    spelat: dict[str, float] = {t: 0.0 for t in lag}
    for m in tidigare:
        vikt = 0.5 ** ((pa - m.dag).days / par.halvering)
        spelat[m.hemma] += vikt
        spelat[m.borta] += vikt
    return Skattning(
        niva=float(beta[0]),
        hemma=float(beta[1]),
        anfall={t: float(beta[2 + i]) for t, i in ix.items()},
        forsvar={t: float(beta[2 + n + i]) for t, i in ix.items()},
        matcher=spelat,
    )


def _poisson(lam: float) -> np.ndarray:
    k = np.arange(MAX_MAL + 1)
    return np.exp(-lam + k * math.log(max(lam, 1e-9)) - np.array([math.lgamma(i + 1) for i in k]))


def utfall(s: Skattning, hemma: str, borta: str, par: Parametrar,
           nykomling_default: bool = False) -> dict[str, float]:
    """Sannolikheterna för en match, och väntade mål i ordinarie tid."""
    def styrka(d: dict[str, float], t: str, idx: int) -> float:
        if t in d:
            return d[t]
        return par.nykomling[idx] if nykomling_default else 0.0
    lh = math.exp(s.niva + s.hemma + styrka(s.anfall, hemma, 0) - styrka(s.forsvar, borta, 1))
    la = math.exp(s.niva + styrka(s.anfall, borta, 0) - styrka(s.forsvar, hemma, 1))
    ph, pa_ = _poisson(lh), _poisson(la)
    M = np.outer(ph, pa_)
    p_h = float(np.tril(M, -1).sum())
    p_d = float(np.trace(M))
    p_a = float(np.triu(M, 1).sum())
    # Hockey slutar lika oftare än två oberoende Poisson säger; skillnaden
    # läggs på lika och tas proportionellt från de andra.
    if par.lika_extra:
        extra = min(par.lika_extra, 0.5)
        p_d2 = p_d + extra * (1 - p_d)
        skala = (1 - p_d2) / max(1e-9, p_h + p_a)
        p_h, p_a, p_d = p_h * skala, p_a * skala, p_d2
    tot = p_h + p_d + p_a
    p_h, p_d, p_a = p_h / tot, p_d / tot, p_a / tot
    # Troligaste resultat i ordinarie tid.
    i, j = np.unravel_index(int(np.argmax(M)), M.shape)
    return {
        "hemma_ordinarie": p_h,
        "forlangning": p_d,
        "borta_ordinarie": p_a,
        "hemma_vinst": p_h + p_d * par.ot_hemma,
        "borta_vinst": p_a + p_d * (1 - par.ot_hemma),
        "mal_hemma": lh,
        "mal_borta": la,
        "troligast": (int(i), int(j)),
    }


def nykomlingar_for(sasonger: list[list[Match]], index: int) -> set[str]:
    """Lag i säsongen `index` som inte spelade i serien säsongen före."""
    if index == 0:
        return set()
    fore = {t for m in sasonger[index - 1] for t in (m.hemma, m.borta)}
    nu = {t for m in sasonger[index] for t in (m.hemma, m.borta)}
    return nu - fore


def simulera(
    s: Skattning,
    kvar: list[tuple[str, str]],
    start: dict[str, int],
    par: Parametrar,
    antal: int = 5000,
    sigma: float = 0.10,
    seed: int = 20260919,
) -> dict[str, dict]:
    """Resten av säsongen, spelad `antal` gånger med modellens sannolikheter.

    Styrkorna är skattade, inte kända. Varje simulering drar därför en
    avvikelse per lag, N(0, sigma) på målskalan, och låter den gälla hela
    säsongen — utan den blir intervallen för smala. Sannolikheterna för varje
    match räknas i förväg på ett rutnät av avvikelser, så simuleringen inte
    behöver räkna Poisson femtusen gånger per match.

    Poäng: tre för vinst i ordinarie tid, två för vinst efter förlängning,
    en för förlust efter förlängning.
    """
    import random

    lag = sorted(set(start) | {t for m in kvar for t in m})
    ix = {t: i for i, t in enumerate(lag)}
    steg = np.linspace(-4 * sigma, 4 * sigma, 33) if sigma > 0 else np.array([0.0])
    tabeller = []
    for h, a in kvar:
        rad = []
        for d in steg:
            u = utfall(Skattning(s.niva, s.hemma,
                                 {h: s.anfall.get(h, 0.0) + d / 2, a: s.anfall.get(a, 0.0) - d / 2},
                                 {h: s.forsvar.get(h, 0.0) + d / 2, a: s.forsvar.get(a, 0.0) - d / 2}),
                       h, a, par)
            rad.append((u["hemma_ordinarie"], u["hemma_ordinarie"] + u["forlangning"]))
        tabeller.append((ix[h], ix[a], rad))

    rnd = random.Random(seed)
    n = len(lag)
    placering = [[0] * n for _ in range(n)]
    totaler: list[list[int]] = [[] for _ in range(n)]
    bas = [start.get(t, 0) for t in lag]
    for _ in range(antal):
        avv = [rnd.gauss(0.0, sigma) if sigma > 0 else 0.0 for _ in range(n)]
        p = bas[:]
        for hi, ai, rad in tabeller:
            d = avv[hi] - avv[ai]
            k = 0 if len(steg) == 1 else int(round((d - steg[0]) / (steg[1] - steg[0])))
            k = max(0, min(len(rad) - 1, k))
            p_h, p_hd = rad[k]
            r = rnd.random()
            if r < p_h:
                p[hi] += 3
            elif r < p_hd:
                if rnd.random() < par.ot_hemma:
                    p[hi] += 2; p[ai] += 1
                else:
                    p[ai] += 2; p[hi] += 1
            else:
                p[ai] += 3
        # Lika poäng delas slumpvis: målskillnaden simuleras inte, och att
        # skilja på namn vore precis det fel tabellen hade i premiären.
        ordning = sorted(range(n), key=lambda i: (-p[i], rnd.random()))
        for plats, i in enumerate(ordning):
            placering[i][plats] += 1
            totaler[i].append(p[i])
    return {t: {"placering": placering[ix[t]], "poang": totaler[ix[t]]} for t in lag}
