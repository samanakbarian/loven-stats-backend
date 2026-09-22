"""Swehockeys lagstatistik: /Teams/Statistics/{sida}/{sasongsgrupp}.

Sidorna ar Swehockeys egna summeringar av serien per lag — powerplay och
boxplay, skott och raddningar, tekningar, utvisningar, mal per spelform,
ledning och underlage, forsta malet och vunna perioder. De ar facit: talen vi
raknar fram ur matcherna ska ga ihop med dem, och dar vi inte kan rakna exakt
(powerplaytillfallen) ar de enda kallan.

En rad per lag, avsnitt och matt ("lang form"). Sidorna har olika kolumner,
och en bred tabell per sida hade varit nio scheman att halla i takt med
Swehockey. Har ar det ett.

Tva egenheter i kallan:

- Placeringen star bara pa den forsta av flera lika. Ovriga har en tom cell
  och arver placeringen ovanfor — samma monster som tappade tretton spelare
  ur poangligan i seriepremiaren.
- Decimaltecknet ar punkt pa vissa sidor och komma pa andra, och "N/A" star
  dar en kvot saknar namnare.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

PAGES = (
    "ScoringAndGoalkeeping",
    "PowerplayAndPenaltyKilling",
    "GoalForAgainst",
    "Faceoffs",
    "FairPlay",
    "LeadingTrailing",
    "ScoreTrailFirst",
    "ShorthandedGoals",
    "WonPeriods",
)

_MMSS = re.compile(r"^(\d+):(\d{2})$")
_SUMMARY = {"totals", "total:", "average", "average:", "total", "totals:"}


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _value(text: str) -> float | None:
    """Talet i en cell. Tid som "261:53" blir minuter, 261,88."""
    t = _clean(text)
    if not t or t.upper() == "N/A":
        return None
    m = _MMSS.match(t)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60, 4)
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def parse_team_codes(html: str) -> dict[str, str]:
    """Kod till lagnamn ur sidans forklaring: "FBK - Farjestad BK"."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for div in soup.select("div.divTeam"):
        code = _clean((div.select_one(".divTeamShortName") or div).get_text(" ", strip=True))
        name_el = div.select_one(".divTeamName")
        name = _clean(name_el.get_text(" ", strip=True) if name_el else "").lstrip("-").strip()
        if code and name:
            out[code] = name
    return out


def parse_team_stats(html: str, page: str) -> list[dict[str, Any]]:
    """En rad per lag, avsnitt, grupp och matt.

    grupp ar tom utom pa tekningssidan, som delar upp i Total, Home och Away
    under samma kolumnnamn.
    """
    soup = BeautifulSoup(html, "lxml")
    codes = parse_team_codes(html)
    out: list[dict[str, Any]] = []

    for table in soup.select("table.tblContent"):
        rows = [[_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")] for tr in table.select("tr")]
        hi = next((i for i, r in enumerate(rows) if r and r[0] == "Rk"), None)
        if hi is None or hi == 0:
            continue
        section = rows[0][0]
        header = rows[hi]
        if len(header) < 4 or header[1] != "Team" or header[2] != "GP":
            continue

        # Gruppraden star mellan avsnittsrubriken och kolumnnamnen. Den har
        # en cell per grupp — ibland med en dubblett ("Home", "Home") — sa
        # grupperna tas i ordning utan upprepningar och delar resten jamnt.
        metrics = header[3:]
        groups = [""] * len(metrics)
        if hi >= 2:
            names: list[str] = []
            for c in rows[hi - 1]:
                if c and c not in names and not c.lower().startswith("last update"):
                    names.append(c)
            if names and len(metrics) % len(names) == 0:
                size = len(metrics) // len(names)
                groups = [names[i // size] for i in range(len(metrics))]

        rank = None
        for r in rows[hi + 1:]:
            if not r or len(r) < len(header):
                continue
            if r[0].lower() in _SUMMARY or r[1].lower() in _SUMMARY:
                continue
            if r[0].isdigit():
                rank = int(r[0])
            elif r[0] != "" or rank is None:
                continue
            code = r[1]
            gp = _value(r[2])
            if not code or gp is None:
                continue
            for i, metric in enumerate(metrics):
                raw = r[3 + i]
                out.append({
                    "page": page,
                    "section": section,
                    "grp": groups[i],
                    "team_code": code,
                    "team_name": codes.get(code),
                    "rank": rank,
                    "games_played": int(gp),
                    "metric": metric,
                    "value": _value(raw),
                    "value_text": raw,
                })
    return out
