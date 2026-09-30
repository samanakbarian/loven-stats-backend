"""GameScore per spelare och match, för Matchens bästa i matchrapporten.

Dom Luszczyszyns GameScore väger ihop det en spelare gjorde i en match till
ett tal. Vikterna nedan är hans. Swehockey saknar blockerade skott, dragna
utvisningar och skottförsök på isen (Corsi); de delarna finns inte med, så
talet blir något lägre än hos den som räknar på SHL:s data. Provat mot
@AJanssonn 29 september 2026: Ekeståhl-Jonsson 1,75 här mot 1,8 där, och
samma fem spelare i omgångens topp.

Rena funktioner utan BigQuery.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

MAL = 0.75
ASSIST1 = 0.7
ASSIST2 = 0.55
SKOTT = 0.075
UTVISNING = -0.15
TEKNING = 0.01
PA_ISEN = 0.15
MV_INSLAPPT = -0.75
MV_RADDNING = 0.1

# Utvisningar som ger motståndaren powerplay. Tio minuter och matchstraff
# lämnar laget fullt.
_TAGNA = (2, 4, 5)


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def matchens_basta(
    events: list[dict[str, Any]],
    skaters: list[dict[str, Any]],
    goalies: list[dict[str, Any]],
    antal: int = 3,
) -> list[dict[str, Any]]:
    """De `antal` bästa i matchen, båda lagen, med det som gav poängen.

    events: matchens händelser (mål med assist1/assist2, utvisningar).
    skaters: rader ur fact_player_game för matchen, båda lagen.
    goalies: rader ur fact_goalie_game för matchen.

    Mål och assist tas ur händelserna, för att skilja första och andra
    assist. Straffläggningens avgörande mål räknas inte: det är inget
    matchmål. Skott och tekningar finns bara där matchrapporten hämtats;
    `full` säger om den fanns.
    """
    g: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        typ = e.get("event_type")
        if typ == "goal":
            if re.search(r"\(GWS\)", str(e.get("score_state") or "")):
                continue
            if e.get("player_name"):
                g[e["player_name"]]["g"] += 1
            if e.get("assist1_name"):
                g[e["assist1_name"]]["a1"] += 1
            if e.get("assist2_name"):
                g[e["assist2_name"]]["a2"] += 1
        elif typ == "penalty" and e.get("player_name") and _int(e.get("penalty_minutes")) in _TAGNA:
            g[e["player_name"]]["pt"] += 1

    ut: list[dict[str, Any]] = []
    for s in skaters:
        namn = s.get("player_key")
        if not namn:
            continue
        h = g.get(namn, {})
        d = {
            "g": h.get("g", 0), "a1": h.get("a1", 0), "a2": h.get("a2", 0), "pt": h.get("pt", 0),
            "sog": _int(s.get("shots")), "fow": _int(s.get("faceoffs_won")), "fol": _int(s.get("faceoffs_lost")),
            "gf": _int(s.get("gf_on_ev")), "ga": _int(s.get("ga_on_ev")),
        }
        poang = (
            MAL * d["g"] + ASSIST1 * d["a1"] + ASSIST2 * d["a2"] + SKOTT * d["sog"]
            + UTVISNING * d["pt"] + TEKNING * (d["fow"] - d["fol"]) + PA_ISEN * (d["gf"] - d["ga"])
        )
        ut.append({
            "name": namn, "team": s.get("team_key"), "goalie": False,
            "score": round(poang, 2), "parts": d,
            "full": s.get("shots") is not None,
        })
    for k in goalies:
        namn = k.get("player_key")
        if not namn or k.get("saves") is None:
            continue
        sv, ga = _int(k.get("saves")), _int(k.get("goals_against"))
        ut.append({
            "name": namn, "team": k.get("team_key"), "goalie": True,
            "score": round(MV_INSLAPPT * ga + MV_RADDNING * sv, 2),
            "parts": {"sv": sv, "ga": ga}, "full": True,
        })
    ut.sort(key=lambda x: (-x["score"], x["name"]))
    return ut[:antal]
