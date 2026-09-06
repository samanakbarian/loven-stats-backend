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

# ── Matchsammanfattning: skott, räddningar och målvakter ──────────────────
#
# Sidhuvudet bär en tabell med lagens skott, räddningar, utvisningsminuter och
# powerplay, hemmalaget till vänster och bortalaget till höger. Längre ner
# står "Goalkeeper Summary" med vilken målvakt som stod och hens
# räddningsprocent. Inget av det finns i handelserna, och det ar enda kallan
# till skott per match — och darmed till PDO.

_PCT = re.compile(r"(\d+[.,]\d+|\d+)\s*%")
_GOALIE = re.compile(r"^(\d{1,2})\.\s*(.+)$")
_SAVES = re.compile(r"(\d+[.,]\d+|\d+)\s*%\s*\((\d+)\s*/\s*(\d+)\)")


def _num(text: Any) -> float | None:
    t = _clean(text).replace("%", "").replace(",", ".").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _periods(text: Any) -> list[int]:
    """'(3:10:4:2:0)' -> [3, 10, 4, 2, 0]."""
    m = re.search(r"\(([\d:\s]+)\)", _clean(text))
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def _labelled_pair(cells: list[str], label: str) -> list[tuple[str, str]]:
    """Vardet efter varje forekomst av etiketten: forst hemma, sedan borta.

    Raden kan bara skrap mellan lagens halvor ("Line Up Actions Reports"), sa
    positionen raknas fran etiketten i stallet for fran radens borjan.
    """
    out: list[tuple[str, str]] = []
    for i, c in enumerate(cells):
        if _clean(c) == label:
            out.append((cells[i + 1] if i + 1 < len(cells) else "",
                        cells[i + 2] if i + 2 < len(cells) else ""))
    return out


def parse_game_summary(html: str, game_id: int) -> dict[str, Any]:
    """Lagens skott och rakningar, plus vilka malvakter som stod."""
    soup = BeautifulSoup(html, "lxml")
    header = parse_header(html)

    # Hemma och borta i den ordning tabellen skriver dem.
    sides: list[dict[str, Any]] = [
        {"is_home": True, "team_name": header.get("home_team")},
        {"is_home": False, "team_name": header.get("away_team")},
    ]

    for table in soup.select("table.tblContent"):
        rows = [[_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")] for tr in table.select("tr")]
        if not any(r and r[0] == "Shots" for r in rows):
            continue
        for idx, cells in enumerate(rows):
            for label, key in (("Shots", "shots"), ("Saves", "saves"), ("PIM", "pim")):
                for side, (value, periods) in zip(sides, _labelled_pair(cells, label)):
                    if _num(value) is not None:
                        side[key] = int(_num(value))
                        side[f"{key}_by_period"] = ",".join(str(n) for n in _periods(periods)) or None
                # Procentraden foljer direkt efter och saknar etikett.
                if any(_clean(c) == label for c in cells) and idx + 1 < len(rows):
                    pcts = [_num(x) for x in rows[idx + 1] if _PCT.fullmatch(_clean(x))]
                    field = {"Shots": "shooting_pct", "Saves": "save_pct"}.get(label)
                    if field:
                        for side, pct in zip(sides, pcts):
                            if pct is not None:
                                side[field] = pct
            for side, (pct, time_text) in zip(sides, _labelled_pair(cells, "PP")):
                if _num(pct) is not None:
                    side["pp_pct"] = _num(pct)
                    side["pp_time"] = _clean(time_text).strip("()") or None
        break

    # Malvaktssammanfattningen: lagkod, nummer, namn och raddningsprocent.
    goalies: list[dict[str, Any]] = []
    for table in soup.select("table.tblContent"):
        rows = [[_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")] for tr in table.select("tr")]
        seen_header = False
        for cells in rows:
            joined = " ".join(cells)
            if "Goalkeeper Summary" in joined:
                seen_header = True
                continue
            if not seen_header:
                continue
            # Avsnittet slutar vid nasta rubrik.
            if len(cells) == 1 and cells[0] and not _SAVES.search(cells[0]):
                break
            m = _SAVES.search(joined)
            who = next((c for c in cells if _GOALIE.match(c)), "")
            g = _GOALIE.match(who)
            if not m or not g:
                continue
            saves, shots = int(m.group(2)), int(m.group(3))
            code = next((c for c in cells if c and c.isupper() and 2 <= len(c) <= 4), None)
            goalies.append(
                {
                    "game_id": game_id,
                    "team_code": code,
                    "goalie_number": int(g.group(1)),
                    "goalie_name": _clean(g.group(2)).rstrip(",").strip(),
                    "save_pct": _num(m.group(1)),
                    "saves": saves,
                    "shots_against": shots,
                    "goals_against": shots - saves,
                }
            )
        if goalies:
            break

    for side in sides:
        side["game_id"] = game_id
        side["home_team"] = header.get("home_team")
        side["away_team"] = header.get("away_team")
        side["spectators"] = header.get("spectators")
        # PDO: skjutprocent plus raddningsprocent. Runt 100 ar normalt; hogre
        # brukar betyda tur, lagre otur — over tid drar det mot 100.
        if side.get("shooting_pct") is not None and side.get("save_pct") is not None:
            side["pdo"] = round(side["shooting_pct"] + side["save_pct"], 2)

    return {"teams": sides, "goalies": goalies}

# ── Kedjor: /Game/LineUps/{game_id} ───────────────────────────────────────
#
# Sidan listar den uppstallning klubben registrerat: forsta till fjarde
# kedjan med forwards och backpar, malvakter och extraspelare. Det ar bättre
# an att gissa kedjor ur vilka som gor mal tillsammans — det har ar lagets
# egen indelning, match for match.
#
# Lagen skrivs i tva spalter med spegelvand ordning: hemmalaget listar
# backparet fore forwardskedjan, bortalaget tvartom, och bortalagets kedjor
# kommer i fallande ordning. Parsern samlar darfor hela blocket per kedja och
# later positionen avgoras i efterhand av spelarens kanda position.

_LINE_LABEL = re.compile(r"^(1st|2nd|3rd|4th)\s+Line$", re.I)
# Laghuvudet ar "IF Bjorkloven ()" eller "MoDo Hockey (Red)" — parentesen bar
# trojfargen och ar ofta tom. Kravde man tomma parenteser foll matcher dar
# fargen stod utsatt, och motstandarens spelare tillskrevs da fel lag.
_TEAM_HEADER = re.compile(r"^(?!\d+\.)(.+?)\s*\(([^)]*)\)$")
_LINE_NUMBER = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


def parse_lineups(html: str, game_id: int) -> list[dict[str, Any]]:
    """En rad per spelare och kedja i matchens uppstallning."""
    soup = BeautifulSoup(html, "lxml")
    header = parse_header(html)

    # Uppstallningen ligger i flera nastlade tabeller ovanpa varandra, och
    # select("tr") plockar upp raderna i dem alla. Den innersta som fortfarande
    # bar kedjeetiketterna ar den vi vill ha — de yttre upprepar samma rader
    # med extra sammanslagna celler.
    candidates = []
    for t in soup.select("table"):
        rows = [[_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")] for tr in t.select("tr")]
        labels = sum(1 for r in rows if r and _LINE_LABEL.match(r[0]))
        if labels >= 2:
            candidates.append((len(t.select("table")), len(rows), rows))
    if not candidates:
        return []
    table = min(candidates)[2]

    out: list[dict[str, Any]] = []
    team: str | None = None
    jersey: str | None = None
    block: str | None = None
    line_no: int | None = None

    def _players(cells: list[str]) -> list[dict[str, Any]]:
        found = []
        for c in cells:
            m = _PLAYER.match(_clean(c))
            if m:
                found.append({"number": int(m.group(1)), "name": _clean(m.group(2)).rstrip(",").strip()})
        return found

    for cells in table:
        if not cells or not any(cells):
            continue
        first = _clean(cells[0])

        # Lagrubriken star ensam och slutar pa tomma parenteser.
        if len(cells) == 1 and " - " not in first:
            m = _TEAM_HEADER.match(first)
            if m:
                team = _clean(m.group(1))
                jersey = _clean(m.group(2)) or None
                block, line_no = None, None
                continue

        label = _LINE_LABEL.match(first)
        if label:
            block, line_no = "line", _LINE_NUMBER[label.group(1).lower()]
        elif first in ("Goalies", "Extra Players"):
            block, line_no = ("goalie" if first == "Goalies" else "extra"), None
        elif first.startswith(("Head Coach", "Assistant Coach", "Referee", "Linesmen")):
            block, line_no = None, None
            continue

        if not team or not block:
            continue
        for pl in _players(cells):
            out.append(
                {
                    "game_id": game_id,
                    "team_name": team,
                    "jersey_colour": jersey,
                    "home_team": header.get("home_team"),
                    "away_team": header.get("away_team"),
                    "block": block,
                    "line_number": line_no,
                    "player_number": pl["number"],
                    "player_name": pl["name"],
                }
            )

    # Samma spelare kan sta med tva ganger i en rad som upprepas i layouten.
    seen: set[tuple] = set()
    unique = []
    for r in out:
        key = (r["team_name"], r["block"], r["line_number"], r["player_number"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique
