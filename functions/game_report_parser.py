"""Spelarstatistik ur Swehockeys matchrapporter i PDF.

Varken handelsesidan eller uppstallningssidan bar statistik per spelare. Skott,
tekningar och malvakternas speltid finns bara i rapporterna under
`/Game/Reports/`, och de ar PDF:er.

Tva av dem anvands:

  MediaGameSummary   spelarsummeringen: skott, mal, assist, poang, +/-,
                     utvisningsminuter, tekningar vunna och forlorade
                     Malvakter: raddningar, inslappta, skott emot,
                     raddningsprocent, hallen nolla, speltid.

  OfficialTeamRoster position, trojnummer, namn, fodelsedatum och
                     kaptensmarkering (C respektive A).

Vad som INTE finns, i varken SHL eller HockeyAllsvenskan: Hits, Blocks,
Shifts och speltid for utespelare. Kolumnerna star i mallen men ar tomma
genom hela serien. Ratta inte parsern for att fa fram dem — de finns inte.

Modulen ror varken natet eller BigQuery, sa den gar att prova mot en sparad
PDF utan vare sig moln eller sasong.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

# Radformat i spelarsummeringen:
#   71 POSSLER Gustav 3 2 1 3 1 0 0 - 1 0,00
#   <nr> <NAMN> SOG G A P +/- PIM <tekn.vunna> - <tekn.forlorade> <FO%>
#
# Tekningskolumnen skrivs "14 - 4" och delas darfor upp i tre tecken av
# textextraktionen. Hits- och Blocks-kolumnerna ar tomma och syns inte alls,
# vilket ar varfor monstret gar direkt fran PIM till tekningarna.
_SKATER = re.compile(
    r"^(\d+)\s+(.+?)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)"
    r"\s+(\d+)\s+-\s+(\d+)\s+(N/A|[\d,]+)$"
)

# 38 GUNNARSSON Jonas 26 4 30 86,67 0 0 60:09
# <nr> <NAMN> raddningar inslappta skott% hallen-nolla pim speltid
# PP- och EQ-kolumnerna daremellan ar tomma i bada serierna.
_GOALIE = re.compile(
    r"^(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(N/A|[\d,]+)\s+(\d+)\s+(\d+)\s+(\d{1,3}:\d{2})$"
)

# GK 31 ERIKSSON EK,Olle 1999-06-22       (ingen bindel)
# CE 18 OTTOSSON,Axel C 1996-04-19        (kapten)
_ROSTER = re.compile(
    r"^(GK|LD|RD|CE|LW|RW|D|F)\s+(\d+)\s+(.+?)\s*(?:\s([CA]))?\s+((?:19|20)\d\d-\d\d-\d\d)$"
)

_PCT = lambda v: None if v == "N/A" else float(v.replace(",", "."))  # noqa: E731


def _pages(pdf_bytes: bytes) -> str:
    """All text i PDF:en, eller tom strang om den inte ar en PDF.

    Rapporten saknas for sasongens forsta matcher — tva av femtiotva i bade
    SHL och HockeyAllsvenskan. Azure svarar da med en XML-felsida i stallet
    for en PDF, och utan den har kontrollen kastar lasaren.
    """
    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        logging.error("pypdf saknas — matchrapporterna kan inte lasas")
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        logging.exception("Kunde inte lasa PDF:en")
        return ""


def _name(raw: str) -> str:
    """'DOWER NILSSON Liam' -> 'Dower Nilsson, Liam'.

    Rapporterna skriver EFTERNAMN Fornamn i versaler, ovriga kallor
    'Efternamn, Fornamn'. Utan den har omskrivningen gar raderna inte att
    joina mot handelserna eller mot sasongsstatistiken.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    parts = text.split(" ")
    # Fornamnet ar den sista biten som inte ar helt versal; efternamn kan
    # besta av flera ord ("DOWER NILSSON", "IHS-WOZNIAK", "ERIKSSON EK").
    given, family = "", parts
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] != parts[i].upper():
            given, family = " ".join(parts[i:]), parts[:i]
            break
    if not given:
        given, family = parts[-1], parts[:-1]
    surname = " ".join(w.capitalize() if w.isupper() else w for w in family)
    surname = re.sub(r"\b(\w)", lambda m: m.group(1).upper(), surname.lower())
    return f"{surname}, {given}".strip(", ")


def parse_boxscore(pdf_bytes: bytes, game_id: int) -> dict[str, list[dict[str, Any]]]:
    """Spelarsummeringen: en rad per utespelare och per malvakt.

    `side` ar 1 for det forst listade laget och 2 for det andra, i samma
    ordning som rapportens rubrik. Vilket lag det ar avgors nedstroms mot
    uppstallningen — rapporten upprepar inte lagnamnet vid varje rad.
    """
    text = _pages(pdf_bytes)
    skaters: list[dict[str, Any]] = []
    goalies: list[dict[str, Any]] = []
    side = 0

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Skaters "):
            side += 1
            continue
        if not side:
            continue

        hit = _SKATER.match(line)
        if hit:
            g = hit.groups()
            skaters.append({
                "game_id": game_id,
                "team_side": side,
                "player_number": int(g[0]),
                "player_name": _name(g[1]),
                "shots": int(g[2]),
                "goals": int(g[3]),
                "assists": int(g[4]),
                "points": int(g[5]),
                "official_plus_minus": int(g[6]),
                "pim": int(g[7]),
                "faceoffs_won": int(g[8]),
                "faceoffs_lost": int(g[9]),
                "faceoff_pct": _PCT(g[10]),
            })
            continue

        hit = _GOALIE.match(line)
        if hit:
            g = hit.groups()
            goalies.append({
                "game_id": game_id,
                "team_side": side,
                "goalie_number": int(g[0]),
                "goalie_name": _name(g[1]),
                "saves": int(g[2]),
                "goals_against": int(g[3]),
                "shots_against": int(g[4]),
                "save_pct": _PCT(g[5]),
                "shutout": int(g[6]),
                "pim": int(g[7]),
                "time_on_ice": g[8],
            })

    return {"skaters": skaters, "goalies": goalies}


def _roster_name(raw: str) -> str:
    """'ERIKSSON EK,Olle' -> 'Eriksson Ek, Olle'.

    Trupprapporten skriver redan efternamn och fornamn atskilda med komma,
    till skillnad fran spelarsummeringen som separerar med mellanslag. Att
    kora den genom _name gav ett komma for mycket: 'Brattstrom,, Victor'.
    """
    text = " ".join(str(raw or "").split())
    if "," not in text:
        return _name(text)
    family, _, given = text.partition(",")
    family = re.sub(r"\b(\w)", lambda m: m.group(1).upper(), family.strip().lower())
    return f"{family}, {given.strip()}".strip(", ")


def parse_team_roster(pdf_bytes: bytes, game_id: int) -> list[dict[str, Any]]:
    """Position, fodelsedatum och kaptensbindel per spelare.

    Innehallet ar per lag och sasong snarare an per match, sa rapporten
    behover inte lasas for varje match — en per lag och manad racker for att
    fanga overgangar.
    """
    text = _pages(pdf_bytes)
    out: list[dict[str, Any]] = []
    side = 0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Pos. No. Name"):
            side += 1
            continue
        if not side:
            continue
        hit = _ROSTER.match(line)
        if not hit:
            continue
        position, number, name, mark, born = hit.groups()
        out.append({
            "game_id": game_id,
            "team_side": side,
            "position": position,
            "player_number": int(number),
            "player_name": _roster_name(name),
            "birthdate": born,
            "is_captain": mark == "C",
            "is_assistant_captain": mark == "A",
        })
    return out
