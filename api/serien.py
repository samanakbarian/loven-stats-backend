"""Seriens lag sida vid sida, ur alla matcher i serien (feature 26).

Rena funktioner utan BigQuery, sa de gar att prova mot sparade rader.

Metoden ar densamma som for vart eget lag pa ovriga sidor, sa talen inte
motsager varandra:

- Mal ur skotten, som /api/v1/shots: skott minus motstandarmalvaktens
  raddningar. Mal i tom bur raknas, straffavgorandet inte.
- Powerplay och boxplay som Swehockey raknar dem: mal i numerart overlage
  delat med motstandarens utvisningar, men utan de som doms samtidigt pa
  bada lagen och utan tio minuters personligt straff. Provat mot Swehockeys
  egen procent i varje match; se powerplaytillfallen.
- Skjut- och raddningsprocent ur totalerna, inte som ett snitt av matchernas.

Placeringen delas vid lika varde. Att skilja lika lag pa namn hade varit
precis det fel tabellen hade i seriepremiaren.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

# Hogre ar battre, utom dar det star False.
MATT: dict[str, bool] = {
    "gf_pg": True,
    "ga_pg": False,
    "sf_pg": True,
    "sa_pg": False,
    "shot_share": True,
    "sh_pct": True,
    "sv_pct": True,
    "pdo": True,
    "pp_pct": True,
    "pk_pct": True,
    "pim_pg": False,
}


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def lagkoder(events: list[dict[str, Any]]) -> dict[str, str]:
    """Lagkod till lagnamn, ur vilken sida som gjorde malen.

    Handelserna bar koden ("MIF") men inte namnet, och lagsummeringen namnet
    men inte koden. Ett mal som okar hemmalagets siffra ar hemmalagets. Koden
    ar densamma hela sasongen, sa varje mal ar en rost och majoriteten vinner.
    """
    per_game: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") == "goal":
            per_game[_int(e.get("game_id")) or 0].append(e)

    votes: dict[str, Counter] = defaultdict(Counter)
    for goals in per_game.values():
        goals.sort(key=lambda e: _int(e.get("event_index")) or 0)
        home = away = 0
        for e in goals:
            h, a = _int(e.get("home_goals")), _int(e.get("away_goals"))
            code = str(e.get("team_code") or "").strip()
            if h is None or a is None or not code:
                continue
            if h > home and a == away:
                votes[code][str(e.get("home_team") or "")] += 1
            elif a > away and h == home:
                votes[code][str(e.get("away_team") or "")] += 1
            home, away = h, a

    return {code: c.most_common(1)[0][0] for code, c in votes.items() if c and c.most_common(1)[0][0]}


def _matchkoder(game_events: list[dict[str, Any]], names: set[str], koder: dict[str, str]) -> dict[str, str] | None:
    """Koderna i en match till lagnamn, eller None nar det inte gar.

    Ett lag som inte gjort mal an saknar kod i sasongens karta — Djurgarden
    efter premiaren mot oss. Ar den andra kodens lag kant hor den okanda till
    det andra laget; det finns bara tva.
    """
    codes = {str(e.get("team_code") or "").strip() for e in game_events} - {""}
    if len(names) != 2 or not codes or len(codes) > 2:
        return None
    out = {c: koder[c] for c in codes if koder.get(c) in names}
    unknown = [c for c in codes if c not in out]
    if len(unknown) == 1 and len(out) == 1:
        out[unknown[0]] = next(n for n in names if n not in out.values())
    if len(out) != len(codes) or len(set(out.values())) != len(out):
        return None
    return out


# Utvisningar som ger numerart overlage. Tio minuters personligt straff och
# matchstraffets tjugo tas inte med: laget spelar fullt under dem.
_PP_MINUTER = (2, 4, 5)


def powerplaytillfallen(game_events: list[dict[str, Any]], match_koder: dict[str, str]) -> Counter:
    """Utvisningar som gav motstandaren powerplay, per lag.

    Swehockey raknar inte utvisningar som doms samtidigt pa bada lagen: tva
    mot tva vid samma tid tar ut varandra. Att rakna varje utvisning gav
    Farjestad 4 av 8 i premiaren dar Swehockey skriver 80 procent, 4 av 5.
    """
    per_tid: dict[str, Counter] = defaultdict(Counter)
    for e in game_events:
        if e.get("event_type") != "penalty":
            continue
        if (_int(e.get("penalty_minutes")) or 0) not in _PP_MINUTER:
            continue
        team = match_koder.get(str(e.get("team_code") or "").strip())
        if team:
            per_tid[str(e.get("time") or "")][team] += 1
    out: Counter = Counter()
    for c in per_tid.values():
        lag = list(c)
        if len(lag) == 2:
            lika = min(c[lag[0]], c[lag[1]])
            for t in lag:
                out[t] += c[t] - lika
        else:
            for t in lag:
                out[t] += c[t]
    return out


def lagtabell(
    summary: list[dict[str, Any]],
    events: list[dict[str, Any]],
    played_games: set[int],
    is_ours,
) -> dict[str, Any]:
    """Nyckeltal, snitt och placering per lag.

    summary: tva rader per match ur lagsummeringen, vara och seriens.
    events: handelserna for samma matcher.
    played_games: alla spelade matcher i serien enligt schemat, for tackningen.
    is_ours: funktion som sager om ett lagnamn ar vart.
    """
    per_game: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in summary:
        gid = _int(r.get("game_id"))
        if gid is None:
            continue
        per_game[gid]["home" if r.get("is_home") else "away"] = r

    koder = lagkoder(events)
    ev_by_game: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        gid = _int(e.get("game_id"))
        if gid is not None:
            ev_by_game[gid].append(e)

    tot: dict[str, Counter] = defaultdict(Counter)
    complete: set[int] = set()
    for gid, sides in per_game.items():
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        hs, as_ = _int(home.get("shots")), _int(away.get("shots"))
        hv, av = _int(home.get("saves")), _int(away.get("saves"))
        if None in (hs, as_, hv, av):
            continue
        complete.add(gid)
        for us, them, sf, sa, our_sv, their_sv in (
            (home, away, hs, as_, hv, av),
            (away, home, as_, hs, av, hv),
        ):
            t = tot[str(us.get("team_name") or "")]
            t["gp"] += 1
            t["sf"] += sf
            t["sa"] += sa
            t["gf"] += sf - their_sv
            t["ga"] += sa - our_sv
            t["pim"] += _int(us.get("pim")) or 0

        # Specialteam ur handelserna. En match utan handelser raknas inte
        # alls har, hellre an som noll powerplay.
        game_events = ev_by_game.get(gid) or []
        names = {str(home.get("team_name") or ""), str(away.get("team_name") or "")}
        match_koder = _matchkoder(game_events, names, koder) if game_events else None
        if not match_koder:
            continue
        tillfallen = powerplaytillfallen(game_events, match_koder)
        for name in names:
            other = next(n for n in names if n != name)
            t = tot[name]
            t["st_gp"] += 1
            t["pp_opps"] += tillfallen[other]
            t["pk_opps"] += tillfallen[name]
            for e in game_events:
                if e.get("event_type") != "goal" or not e.get("is_power_play"):
                    continue
                team = match_koder.get(str(e.get("team_code") or "").strip())
                if team == name:
                    t["pp_goals"] += 1
                elif team == other:
                    t["pk_ga"] += 1

    teams: list[dict[str, Any]] = []
    for name, t in tot.items():
        if not name or not t["gp"]:
            continue
        gp = t["gp"]
        sh = _pct(t["gf"], t["sf"])
        sv = _pct(t["sa"] - t["ga"], t["sa"])
        teams.append({
            "team": name,
            "is_ours": bool(is_ours(name)),
            "gp": gp,
            "gf": t["gf"], "ga": t["ga"], "sf": t["sf"], "sa": t["sa"],
            "gf_pg": round(t["gf"] / gp, 2),
            "ga_pg": round(t["ga"] / gp, 2),
            "sf_pg": round(t["sf"] / gp, 1),
            "sa_pg": round(t["sa"] / gp, 1),
            "shot_share": _pct(t["sf"], t["sf"] + t["sa"]),
            "sh_pct": sh,
            "sv_pct": sv,
            "pdo": round(sh + sv, 1) if sh is not None and sv is not None else None,
            "pp_goals": t["pp_goals"], "pp_opps": t["pp_opps"],
            "pp_pct": _pct(t["pp_goals"], t["pp_opps"]),
            "pk_ga": t["pk_ga"], "pk_opps": t["pk_opps"],
            "pk_pct": _pct(t["pk_opps"] - t["pk_ga"], t["pk_opps"]),
            "pim_pg": round(t["pim"] / gp, 1),
            "special_teams_games": t["st_gp"],
        })

    # Placering: ett plus antalet lag som ar strikt battre. Lika varde ger
    # samma placering, och "tied" sager hur manga som delar den.
    for key, higher in MATT.items():
        vals = [x[key] for x in teams if x[key] is not None]
        for x in teams:
            v = x[key]
            x.setdefault("ranks", {})
            x.setdefault("tied", {})
            if v is None:
                x["ranks"][key] = None
                x["tied"][key] = 0
                continue
            better = sum(1 for o in vals if (o > v if higher else o < v))
            x["ranks"][key] = better + 1
            x["tied"][key] = sum(1 for o in vals if o == v)

    # Seriens snitt, ur totalerna.
    s = Counter()
    for t in tot.values():
        s.update(t)
    lsh = _pct(s["gf"], s["sf"])
    lsv = _pct(s["sa"] - s["ga"], s["sa"])
    league = {
        "gf_pg": round(s["gf"] / s["gp"], 2) if s["gp"] else None,
        "sf_pg": round(s["sf"] / s["gp"], 1) if s["gp"] else None,
        "sh_pct": lsh,
        "sv_pct": lsv,
        "pdo": round(lsh + lsv, 1) if lsh is not None and lsv is not None else None,
        "pp_pct": _pct(s["pp_goals"], s["pp_opps"]),
        "pk_pct": _pct(s["pk_opps"] - s["pk_ga"], s["pk_opps"]),
        "pim_pg": round(s["pim"] / s["gp"], 1) if s["gp"] else None,
    }

    teams.sort(key=lambda x: (-(x["shot_share"] or 0), x["team"]))
    return {
        "teams": teams,
        "league": league,
        "coverage": {
            "games": len(complete & played_games) if played_games else len(complete),
            "played": len(played_games),
            "complete": bool(played_games) and played_games <= complete,
        },
    }


# ── Swehockeys lagstatistik ────────────────────────────────────────────────

# Matten sajten visar, ur core.team_stats. (avsnitt, grupp, kolumn).
_KALLA = {
    "gp": ("Scoring Efficiency", "", "GP"),
    "gf": ("Scoring Efficiency", "", "GF"),
    "sf": ("Scoring Efficiency", "", "SOG"),
    "sh_pct": ("Scoring Efficiency", "", "SG%"),
    "ga": ("Goalkeeping Efficiency", "", "GA"),
    "sa": ("Goalkeeping Efficiency", "", "SOG"),
    "saves": ("Goalkeeping Efficiency", "", "SVS"),
    "sv_pct": ("Goalkeeping Efficiency", "", "SVS%"),
    "pp_opps": ("Powerplay Efficiency", "", "ADV."),
    "pp_goals": ("Powerplay Efficiency", "", "PPGF"),
    "pp_pct": ("Powerplay Efficiency", "", "PP%"),
    "pk_opps": ("Penalty Killing", "", "DVG."),
    "pk_ga": ("Penalty Killing", "", "PPGA"),
    "pk_pct": ("Penalty Killing", "", "PK%"),
    "ev_gf": ("Goals For", "", "5-5"),
    "ev_ga": ("Goals Against", "", "5-5"),
    "fo_won": ("Faceoff Efficiency", "Total", "FO+"),
    "fo_lost": ("Faceoff Efficiency", "Total", "FO-"),
    "fo_pct": ("Faceoff Efficiency", "Total", "FO%"),
    "pim": ("Fair Play", "", "PIM"),
    "first_games": ("Score first", "", "Tot"),
    "first_wins": ("Score first", "", "W"),
    "trail_games": ("Trail first", "", "Tot"),
    "trail_wins": ("Trail first", "", "W"),
}

# Hogre ar battre, utom dar det star False.
LAGMATT: dict[str, bool] = {
    "gf_pg": True,
    "ga_pg": False,
    "sf_pg": True,
    "sa_pg": False,
    "shot_share": True,
    "sh_pct": True,
    "sv_pct": True,
    "pdo": True,
    "pp_pct": True,
    "pk_pct": True,
    "ev_share": True,
    "fo_pct": True,
    "pim_pg": False,
}


def _div(a: float | None, b: float | None, scale: float = 1, nd: int = 2) -> float | None:
    if a is None or not b:
        return None
    return round(a / b * scale, nd)


def _placera(teams: list[dict[str, Any]], keys: dict[str, bool]) -> None:
    """Placering per matt: ett plus antalet lag som ar strikt battre."""
    for key, higher in keys.items():
        vals = [t["values"][key] for t in teams if t["values"].get(key) is not None]
        for t in teams:
            v = t["values"].get(key)
            if v is None:
                t["ranks"][key] = None
                t["tied"][key] = 0
                continue
            t["ranks"][key] = 1 + sum(1 for o in vals if (o > v if higher else o < v))
            t["tied"][key] = sum(1 for o in vals if o == v)


def fran_lagstatistik(rows: list[dict[str, Any]], is_ours) -> dict[str, Any]:
    """Lagen sida vid sida ur Swehockeys lagstatistik.

    Kvoterna raknas om ur totalerna i stallet for att tas fran sidan, sa
    seriens snitt och lagens tal raknas pa samma satt. Over HA 25/26 gav det
    samma tal som Swehockeys egna kolumner, pa avrundningen nar.
    """
    raw: dict[str, dict[str, float | None]] = defaultdict(dict)
    names: dict[str, str] = {}
    for r in rows:
        code = str(r.get("team_code") or "")
        if not code:
            continue
        if r.get("team_name"):
            names[code] = str(r["team_name"])
        for key, (section, grp, metric) in _KALLA.items():
            if r.get("section") == section and (r.get("grp") or "") == grp and r.get("metric") == metric:
                raw[code][key] = r.get("value")
        if r.get("section") == "Scoring Efficiency":
            raw[code]["gp"] = r.get("games_played")

    teams: list[dict[str, Any]] = []
    for code, v in raw.items():
        gp = v.get("gp")
        if not gp:
            continue
        sf, sa, gf, ga = v.get("sf"), v.get("sa"), v.get("gf"), v.get("ga")
        sh = _div(gf, sf, 100, 2)
        sv = _div(sa - ga if sa is not None and ga is not None else None, sa, 100, 2)
        values = {
            "gf_pg": _div(gf, gp),
            "ga_pg": _div(ga, gp),
            "sf_pg": _div(sf, gp, 1, 1),
            "sa_pg": _div(sa, gp, 1, 1),
            "shot_share": _div(sf, (sf or 0) + (sa or 0), 100, 1) if sf is not None and sa is not None else None,
            "sh_pct": sh,
            "sv_pct": sv,
            "pdo": round(sh + sv, 2) if sh is not None and sv is not None else None,
            "pp_pct": _div(v.get("pp_goals"), v.get("pp_opps"), 100, 1),
            "pk_pct": _div((v.get("pk_opps") or 0) - (v.get("pk_ga") or 0), v.get("pk_opps"), 100, 1)
            if v.get("pk_opps") else None,
            "ev_share": _div(v.get("ev_gf"), (v.get("ev_gf") or 0) + (v.get("ev_ga") or 0), 100, 1)
            if v.get("ev_gf") is not None and v.get("ev_ga") is not None else None,
            "fo_pct": _div(v.get("fo_won"), (v.get("fo_won") or 0) + (v.get("fo_lost") or 0), 100, 1)
            if v.get("fo_won") is not None and v.get("fo_lost") is not None else None,
            "pim_pg": _div(v.get("pim"), gp, 1, 1),
        }
        counts = {k: v.get(k) for k in (
            "gf", "ga", "sf", "sa", "pp_goals", "pp_opps", "pk_ga", "pk_opps", "ev_gf", "ev_ga",
            "first_games", "first_wins", "trail_games", "trail_wins",
        )}
        name = names.get(code, code)
        teams.append({
            "team": name,
            "code": code,
            "is_ours": bool(is_ours(name)),
            "gp": int(gp),
            "values": values,
            "counts": {k: (int(x) if isinstance(x, float) and x.is_integer() else x) for k, x in counts.items()},
            "ranks": {},
            "tied": {},
        })

    _placera(teams, LAGMATT)

    # Seriens snitt ur summorna, som lagens egna tal.
    def tot(key: str) -> float:
        return sum((raw[t["code"]].get(key) or 0) for t in teams)

    gp = tot("gp")
    lsh = _div(tot("gf"), tot("sf"), 100, 2)
    lsv = _div(tot("sa") - tot("ga"), tot("sa"), 100, 2)
    league = {
        "gf_pg": _div(tot("gf"), gp),
        "ga_pg": _div(tot("ga"), gp),
        "sf_pg": _div(tot("sf"), gp, 1, 1),
        "sa_pg": _div(tot("sa"), gp, 1, 1),
        "shot_share": 50.0 if teams else None,
        "sh_pct": lsh,
        "sv_pct": lsv,
        "pdo": round(lsh + lsv, 2) if lsh is not None and lsv is not None else None,
        "pp_pct": _div(tot("pp_goals"), tot("pp_opps"), 100, 1),
        "pk_pct": _div(tot("pk_opps") - tot("pk_ga"), tot("pk_opps"), 100, 1),
        "ev_share": 50.0 if teams else None,
        "fo_pct": 50.0 if teams else None,
        "pim_pg": _div(tot("pim"), gp, 1, 1),
    }
    teams.sort(key=lambda t: t["team"])
    return {"teams": teams, "league": league}
