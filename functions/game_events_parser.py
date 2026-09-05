"""Parsning av Swehockeys handelsesida, /Game/Events/{game_id}.

Sidans "Actions"-tabell har fem kolumner:

    [0] tid pa matchklockan, "45:27"
    [1] handelsens art: "0-1 (EQ)" for mal, "2 min" for utvisning,
        "GK In"/"GK Out" for malvaktsbyte, "PS", "TO"
    [2] lagkod, "IFB"
    [3] spelare — for mal bade malskytt och assisterande
    [4] detalj — for mal spelarna pa isen, for utvisning typ och intervall

Perioden star som egen rubrikrad ("1st period", "Overtime") fore sina
handelser, sa den behover inte gissas ur klockan. Raderna kommer i omvand
kronologisk ordning.

Cell [4] pa en malrad bar `Pos. Part.` och `Neg. Part.` — alla spelare pa isen
for respektive lag. Det ar den enda kallan vi har till on-ice-data, och den
gor riktigt plus/minus och kombinationsanalys mojlig.

Modulen ar avsiktligt fri fran BigQuery och natverk: den tar HTML och ger
rader, sa den gar att testa mot sparade sidor.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

# Rubrikrader som byter period. Nyckeln matchas gemen och utan skiljetecken.
_PERIOD_HEADERS = {
    "1st period": 1,
    "2nd period": 2,
    "3rd period": 3,
    "overtime": 4,
    "game winning shots": 5,
    "game winning shot": 5,
}

# Rader som inte ar handelser i matchen.
_SKIP_SECTIONS = {"goalkeeper summary", "actions"}

_TIME = re.compile(r"^\d{1,3}:\d{2}$")
_GOAL = re.compile(r"^(\d+)\s*-\s*(\d+)\s*(?:\(([^)]*)\))?$")
_PENALTY_MIN = re.compile(r"^(\d+)\s*min", re.I)
# "64. Malmström, Anton (1)" — nummer, namn, och for malskytten mallopnummer.
_PLAYER = re.compile(r"^(\d{1,2})\.\s*(.+?)(?:\s*\((\d+)\))?$")


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _split_players(text: str) -> list[dict[str, Any]]:
    """'64. Malmström, Anton (1) 10. Nilsson, Marcus' -> tva poster.

    Delas fore varje "NN. ", vilket ar det enda som skiljer namnen at —
    Swehockey separerar dem inte pa nagot annat satt.
    """
    text = _clean(text)
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for chunk in re.split(r"\s+(?=\d{1,2}\.\s)", text):
        m = _PLAYER.match(_clean(chunk))
        if not m:
            continue
        out.append(
            {
                "number": int(m.group(1)),
                "name": _clean(m.group(2)).rstrip(",").strip(),
                "tally": int(m.group(3)) if m.group(3) else None,
            }
        )
    return out


def _split_on_ice(text: str) -> tuple[list[int], list[int]]:
    """'Pos. Part.: 10 , 26 Neg. Part.: 16 , 29' -> ([10, 26], [16, 29]).

    Pos ar spelarna pa isen for det lag som gjorde malet, Neg for det andra.
    """
    text = _clean(text)
    if "Part." not in text:
        return [], []
    parts = re.split(r"Neg\.\s*Part\.\s*:", text, maxsplit=1)
    pos_raw = re.sub(r"^.*?Pos\.\s*Part\.\s*:", "", parts[0], flags=re.S)
    neg_raw = parts[1] if len(parts) > 1 else ""
    nums = lambda s: [int(n) for n in re.findall(r"\d{1,2}", s)]
    return nums(pos_raw), nums(neg_raw)


def _penalty_type(text: str) -> str:
    """'Interference (53:16 - 55:16)' -> 'Interference'.

    Slutparentesen kan sakna sluttid — sammanfallande utvisningar skrivs
    "Roughing (00:00 - )" — sa den delen far vara valfri.
    """
    return _clean(
        re.sub(r"\(\s*\d{1,3}:\d{2}\s*-\s*(?:\d{1,3}:\d{2}\s*)?\)\s*$", "", _clean(text))
    )


def parse_header(html: str) -> dict[str, Any]:
    """Lagnamn, datum och publik ur sidhuvudet."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Any] = {"home_team": None, "away_team": None, "spectators": None}

    for table in soup.select("table.tblContent"):
        for tr in table.select("tr"):
            cells = [_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")]
            if len(cells) == 1 and " - " in cells[0] and not _TIME.match(cells[0]):
                home, _, away = cells[0].partition(" - ")
                if home.strip() and away.strip() and not home.strip().isdigit():
                    out["home_team"] = _clean(home)
                    out["away_team"] = _clean(away)
            for c in cells:
                m = re.search(r"Spectators:\s*(\d[\d\s]*)", c)
                if m:
                    out["spectators"] = int(re.sub(r"\D", "", m.group(1)))
        if out["home_team"]:
            break
    return out


def parse_events(html: str, game_id: int) -> list[dict[str, Any]]:
    """Alla handelser i matchen, i kronologisk ordning."""
    soup = BeautifulSoup(html, "lxml")
    header = parse_header(html)

    rows: list[list[str]] = []
    for table in soup.select("table.tblContent"):
        collected = []
        for tr in table.select("tr"):
            # get_text med separator: annars klistras cellernas inre taggar
            # ihop och "Possler, Gustav" blir "Possler, GustavPos".
            collected.append([_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")])
        # Handelsetabellen ar den som innehaller tidsstamplar.
        if any(cells and _TIME.match(cells[0]) for cells in collected):
            rows = collected
            break
    if not rows:
        return []

    period = 0
    out: list[dict[str, Any]] = []

    for cells in rows:
        label = _clean(cells[0]).lower().strip(".")
        if len(cells) == 1 or (len(cells) > 1 and not any(cells[1:])):
            if label in _PERIOD_HEADERS:
                period = _PERIOD_HEADERS[label]
                continue
            if label in _SKIP_SECTIONS:
                continue
        if label in _PERIOD_HEADERS:
            period = _PERIOD_HEADERS[label]
            continue
        if len(cells) < 4 or not _TIME.match(cells[0]):
            continue

        time_str, kind, team_code, who = cells[0], cells[1], cells[2], cells[3]
        detail = cells[4] if len(cells) > 4 else ""

        base: dict[str, Any] = {
            "game_id": game_id,
            "time": time_str,
            "period": period or None,
            "team_code": _clean(team_code) or None,
            "home_team": header.get("home_team"),
            "away_team": header.get("away_team"),
        }

        goal = _GOAL.match(_clean(kind))
        pen = _PENALTY_MIN.match(_clean(kind))

        if goal:
            players = _split_players(who)
            scorer = players[0] if players else {}
            assists = players[1:3]
            pos, neg = _split_on_ice(detail)
            state = _clean(goal.group(3) or "")
            out.append(
                {
                    **base,
                    "event_type": "goal",
                    "player_number": scorer.get("number"),
                    "player_name": scorer.get("name"),
                    "assist1_name": assists[0]["name"] if len(assists) > 0 else None,
                    "assist1_number": assists[0]["number"] if len(assists) > 0 else None,
                    "assist2_name": assists[1]["name"] if len(assists) > 1 else None,
                    "assist2_number": assists[1]["number"] if len(assists) > 1 else None,
                    "score_state": _clean(kind),
                    "home_goals": int(goal.group(1)),
                    "away_goals": int(goal.group(2)),
                    "is_power_play": state.upper().startswith("PP"),
                    "is_short_handed": state.upper().startswith("SH"),
                    "is_empty_net": "EN" in state.upper(),
                    "is_game_winning_shot": "GWS" in state.upper(),
                    # Spelarna pa isen, som trojnummer. Pos ar det gorande
                    # lagets skridskoakare, Neg det slappande lagets.
                    "on_ice_for": ",".join(str(n) for n in pos) or None,
                    "on_ice_against": ",".join(str(n) for n in neg) or None,
                    "detail": _clean(kind),
                    "penalty_minutes": 0,
                }
            )
        elif pen:
            players = _split_players(who)
            player = players[0] if players else {}
            out.append(
                {
                    **base,
                    "event_type": "penalty",
                    "player_number": player.get("number"),
                    # "Team penalty" har inget nummer och ska behalla sin text.
                    "player_name": player.get("name") or (_clean(who) or None),
                    "penalty_minutes": int(pen.group(1)),
                    "detail": _penalty_type(detail),
                    "score_state": None,
                    "is_power_play": False,
                    "is_short_handed": False,
                }
            )
        else:
            kind_clean = _clean(kind)
            if not kind_clean:
                continue
            players = _split_players(who)
            player = players[0] if players else {}
            out.append(
                {
                    **base,
                    "event_type": {"GK In": "goalie_in", "GK Out": "goalie_out", "TO": "timeout"}
                    .get(kind_clean, kind_clean.lower().replace(" ", "_")),
                    "player_number": player.get("number"),
                    "player_name": player.get("name") or (_clean(who) or None),
                    "detail": _penalty_type(detail) or None,
                    "penalty_minutes": 0,
                    "score_state": None,
                    "is_power_play": False,
                    "is_short_handed": False,
                }
            )

    # Sidan listar nyast forst; kronologisk ordning ar mer anvandbar nedstroms.
    out.reverse()
    for i, row in enumerate(out):
        row["event_index"] = i
    return out
