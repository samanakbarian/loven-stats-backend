import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import functions_framework
import requests
from bs4 import BeautifulSoup
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud import storage

try:
    from etl_runtime import (
        BigQueryRunLogger,
        checks_passed,
        ensure_lineage_columns,
        validate_rows,
    )
except ImportError:
    from functions.etl_runtime import (
        BigQueryRunLogger,
        checks_passed,
        ensure_lineage_columns,
        validate_rows,
    )

try:
    from game_events_parser import parse_events, parse_game_summary, parse_lineups
    from game_report_parser import parse_boxscore, parse_team_roster
except ImportError:
    from functions.game_events_parser import parse_events, parse_game_summary, parse_lineups
    from functions.game_report_parser import parse_boxscore, parse_team_roster

logging.basicConfig(level=logging.INFO)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "granskaren-d51a1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "loven-stats-raw-data-prod")
SWEHOCKEY_TEAM_ID = os.environ.get("SWEHOCKEY_TEAM_ID", "1139")
SWEHOCKEY_SEASON_GROUP_ID = os.environ.get("SWEHOCKEY_SEASON_GROUP_ID", "20961")
BASE_URL = "https://stats.swehockey.se"
BQ_DATASET = "raw_sports"
SOURCE = "swehockey"
PIPELINE_NAME = "swehockey_stats"
TEAM_TOKENS = [t.strip().lower() for t in os.environ.get("SWEHOCKEY_TEAM_TOKENS", "björklöven,bjorkloven,löven,bjo").split(",") if t.strip()]
# Handelsesidan hamtas en match i taget, cirka en sekund styck. Bara lagets
# egna matcher ar intressanta, sa en hel sasong ar ~52 anrop. Gransen finns
# for att en schemalagd korning ska rymmas med god marginal; en backfill
# hojer den med ?events_limit=all.
EVENTS_LIMIT_DEFAULT = int(os.environ.get("SWEHOCKEY_EVENTS_LIMIT", "20"))
# Swehockey publicerar matchrapporten en kvart till en timme efter slutsignal,
# men rattar den ofta i efterhand — matt over en sasong kom rattelserna oftast
# inom en vecka, i ett fall efter sexton dagar. En match vi redan hamtat maste
# darfor hamtas om ett tag till, annars fastnar vi med den forsta versionen.
REFRESH_DAYS = int(os.environ.get("SWEHOCKEY_REFRESH_DAYS", "21"))


def _now():
    return datetime.now(timezone.utc)


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if s in ("", "-", "–"):
        return 0
    try:
        return int(float(s.replace(",", ".")))
    except Exception:
        return 0


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if s in ("", "-", "–"):
        return 0.0
    try:
        return float(s.replace(",", "."))
    except Exception:
        return 0.0


def _clean(s: Any) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def _fetch_html(url: str) -> str | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        # Handelsesidan skickar "Content-Type: text/html" utan teckenupp-
        # sattning. HTTP:s standardvarde ar da ISO-8859-1, men innehallet ar
        # utf-8 — och varje svensk bokstav blev mojibake: "Tellstrom" skrevs
        # "TellstrÃ¶m" och utvisningstypen "Okand" blev "OkÃ¤nd". Schema- och
        # truppsidorna deklarerar utf-8 och paverkas inte.
        if "charset" not in (r.headers.get("Content-Type") or "").lower():
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:
        logging.error("Fetch failed for %s: %s", url, e)
        return None


def _extract_table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    # Prefer bootstrap-style stats tables when present; fall back to first table.
    table = soup.select_one("table.table") or soup.select_one("table")
    if not table:
        return []
    rows: list[list[str]] = []
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        if not cells:
            continue
        rows.append([_clean(c.get_text(" ", strip=True)) for c in cells])
    return rows


def _is_header_row(row: list[str]) -> bool:
    if not row:
        return True
    first = _clean(row[0]).lower()
    header_tokens = {"rk", "rank", "no", "name", "team", "gp", "sp", "date", "datum"}
    return first in header_tokens


def _contains_team_token(values: list[str]) -> bool:
    joined = " ".join(values).lower()
    return any(token in joined for token in TEAM_TOKENS)


def _fetch_player_stats(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"{BASE_URL}/Teams/Info/PlayersByTeam/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table")
    out = []
    current_team = ""
    for table in tables:
        if table.find("table"):
            continue  # Skip wrapper tables that contain nested tables
        rows = table.find_all("tr", recursive=False)
        if not rows and table.find("tbody"):
            rows = table.find("tbody").find_all("tr", recursive=False)
        if not rows: continue
        first_row = [_clean(c.get_text(" ", strip=True)) for c in rows[0].select("th,td")]
        
        # Check if this table starts a new team (Playing Statistics)
        if len(first_row) > 0 and "Top" in first_row[-1]:
            current_team = first_row[0]
            
        # Is this the Playing Statistics table? (Headers: Rk, No, Name, Pos...)
        if len(rows) > 3:
            headers = [_clean(c.get_text(" ", strip=True)) for c in rows[2].select("th,td")]
            if len(headers) > 3 and headers[3] == "Pos":
                for tr in rows[3:]:
                    r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
                    if len(r) < 12 or _is_header_row(r) or not _safe_int(r[0]):
                        continue
                    out.append(
                        {
                            "season_group_id": int(season_group_id),
                            "team_id": SWEHOCKEY_TEAM_ID,
                            "team_code": current_team,
                            "player_name": _clean(r[2]),
                            "jersey_number": _safe_int(r[1]),
                            "position": _clean(r[3]),
                            "games_played": _safe_int(r[4]),
                            "goals": _safe_int(r[5]),
                            "assists": _safe_int(r[6]),
                            "points": _safe_int(r[7]),
                            "plus_minus": _safe_int(r[11]),
                            "pim": _safe_int(r[8]),
                        }
                    )
    # Deduplicate by player name and team code
    unique_out = {f"{r['team_code']}_{r['player_name']}": r for r in out}
    return list(unique_out.values()), url


def _fetch_goalie_stats(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"{BASE_URL}/Teams/Info/PlayersByTeam/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table")
    out = []
    current_team = ""
    for table in tables:
        if table.find("table"):
            continue  # Skip wrapper tables that contain nested tables
        rows = table.find_all("tr", recursive=False)
        if not rows and table.find("tbody"):
            rows = table.find("tbody").find_all("tr", recursive=False)
        if not rows:
            continue
        first_row = [_clean(c.get_text(" ", strip=True)) for c in rows[0].select("th,td")]

        # Keep track of team from [Top] row
        if len(first_row) > 0 and "Top" in first_row[-1]:
            current_team = first_row[0]

        # Is this the Goalkeeping Statistics table?
        if len(rows) > 2:
            is_goalie = False
            start_idx = 0
            header_row = []
            if len(first_row) > 0 and "Goalkeeping Statistics" in first_row[0]:
                is_goalie = True
                start_idx = 2
                if len(rows) > 1:
                    header_row = [_clean(c.get_text(" ", strip=True)) for c in rows[1].select("th,td")]
            elif len(rows) > 1:
                second_row = [_clean(c.get_text(" ", strip=True)) for c in rows[1].select("th,td")]
                if len(second_row) > 0 and "Goalkeeping Statistics" in second_row[0]:
                    is_goalie = True
                    start_idx = 3
                    if len(rows) > 2:
                        header_row = [_clean(c.get_text(" ", strip=True)) for c in rows[2].select("th,td")]

            if is_goalie:
                # Build column index map from header row for robust lookup
                # Expected columns: Rk No Name GPT GKD GPI MIP GA SVS SOG SVS% GAA SO W L
                col_map = {h: i for i, h in enumerate(header_row)}

                def _col(cols_list, key, fallback_idx):
                    idx = col_map.get(key, fallback_idx)
                    if idx < len(cols_list):
                        return _clean(cols_list[idx].get_text(" ", strip=True))
                    return ""

                for tr in rows[start_idx:]:
                    cols = tr.select("th,td")
                    r = [_clean(c.get_text(" ", strip=True)) for c in cols]
                    if len(r) < 3 or _is_header_row(r) or not _safe_int(r[0]):
                        continue
                    gpi = _safe_int(_col(cols, "GPI", 5))
                    if gpi == 0:
                        continue  # Skip goalies with no games played

                    out.append(
                        {
                            "season_group_id": int(season_group_id),
                            "team_id": SWEHOCKEY_TEAM_ID,
                            "team_code": current_team,
                            "goalie_name": _clean(r[2]) if len(r) > 2 else "",
                            "games_played": gpi,
                            "shots_against": _safe_int(_col(cols, "SOG", 9)),
                            "saves": _safe_int(_col(cols, "SVS", 8)),
                            "goals_against": _safe_int(_col(cols, "GA", 7)),
                            "save_pct": _safe_float(_col(cols, "SVS%", 10)),
                            "gaa": _safe_float(_col(cols, "GAA", 11)),
                            "toi_minutes": 0,
                            "shutouts": _safe_int(_col(cols, "SO", 12)),
                            "wins": _safe_int(_col(cols, "W", 13)),
                            "losses": _safe_int(_col(cols, "L", 14)),
                        }
                    )
    # Deduplicate by goalie name and team code
    unique_out = {f"{r['team_code']}_{r['goalie_name']}": r for r in out}
    return list(unique_out.values()), url


def _fetch_standings(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/ScheduleAndResults/Standings/{season_group_id}",
    ]
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        rows = _extract_table_rows(html)
        if len(rows) < 2:
            continue
        out = []
        for r in rows:
            if r and _clean(r[0]).lower() == "home":
                # Stop parsing when we reach the Home standings sub-table
                break
            if len(r) < 13 or not _safe_int(r[0]):
                continue
            out.append(
                {
                    "season_group_id": int(season_group_id),
                    "team_name": _clean(r[1]),
                    "rank": _safe_int(r[0]),
                    "games_played": _safe_int(r[2]),
                    "wins": _safe_int(r[3]),
                    "ot_wins": _safe_int(r[9]) + _safe_int(r[11]),
                    "ot_losses": _safe_int(r[10]) + _safe_int(r[12]),
                    "losses": _safe_int(r[5]),
                    "goal_diff": _safe_int(r[7]),
                    "points": _safe_int(r[8]),
                }
            )
        if out:
            return out, url
    return [], None


def _extract_schedule_rows(html: str) -> list[dict[str, Any]]:
    """Plocka ut matchrader ur Swehockeys spelschema.

    Den generella _extract_table_rows() strippar all HTML och tappar därmed
    länken <a href=".../Game/Events/{id}">, som är enda stället matchens id
    finns. Utan game_id kan schedule inte kopplas till swehockey_game_events,
    och analytics hämtar då aldrig några matchhändelser alls.

    Radens celler (8 st när det är en match):
      [0] datum, men bara på dagens första match — annars tid
      [1] datum + tid, bara när [0] är ett datum
      [2] tid
      [3] "Hemmalag - Bortalag"
      [4] resultat, t.ex. "3 - 1" (tomt för ospelad match)
      [5] periodresultat, t.ex. "(0-0, 1-1, 2-0)"
      [6] publik
      [7] arena
    """
    soup = BeautifulSoup(html, "lxml")
    # tblContent är den faktiska matchtabellen; table.table matchar en yttre
    # wrapper som bara har en egen rad.
    table = soup.select_one("table.tblContent") or soup.select_one("table.table") or soup.select_one("table")
    if not table:
        return []

    out: list[dict[str, Any]] = []
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        # Grundserien har atta kolumner, slutspelet sju.
        if len(cells) < 7:
            continue
        texts = [_clean(c.get_text(" ", strip=True)) for c in cells]

        link = tr.select_one('a[href*="/Game/Events/"]')
        game_id = None
        if link:
            m = re.search(r"/Game/Events/(\d+)", link.get("href", ""))
            if m:
                game_id = int(m.group(1))

        out.append(
            {
                "cells": texts,
                "game_id": game_id,
            }
        )
    return out


def _collapse_repeated(text: str) -> str:
    """Swehockey upprepar lagnamnet i varje rubrikcell.

    "IF Bjorkloven IF Bjorkloven IF Bjorkloven IF Bjorkloven" -> "IF Bjorkloven".
    Hittar kortaste ordsekvens som upprepad bygger hela strangen.
    """
    words = text.split()
    n = len(words)
    if n < 2:
        return text.strip()
    for size in range(1, n // 2 + 1):
        if n % size:
            continue
        unit = words[:size]
        if all(words[i:i + size] == unit for i in range(0, n, size)):
            return " ".join(unit)
    return text.strip()


def _fetch_roster(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    """Hela den registrerade truppen per lag.

    Kompletterar swehockey_player_stats, som bara innehaller spelare som
    faktiskt spelat. PlayersByTeam listar hela truppen sa fort klubben
    registrerat den, med trojnummer och position — vilket gor den till ratt
    kalla for en trupplista fore och under sasongen.

    Sidan innehaller alla lag i serien: en rubriktabell (tblBorderNoPad) med
    lagnamnet foljd av en innehallstabell (tblContent) med spelarna.
    """
    url = f"{BASE_URL}/Teams/Info/PlayersByTeam/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None

    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table")
    out: list[dict[str, Any]] = []

    for i, table in enumerate(tables):
        if "tblBorderNoPad" not in (table.get("class") or []):
            continue
        header_cell = table.select_one("td,th")
        raw_header = _clean(header_cell.get_text(" ", strip=True)) if header_cell else ""
        raw_header = re.sub(r"\s*\[Top\].*$", "", raw_header).strip()
        team_name = _collapse_repeated(raw_header)
        if not team_name:
            continue
        # Varje lag har tva block: utespelare och en separat malvaktstabell.
        # Malvakterna finns redan i lagets huvudtabell med Pos=GK, sa
        # malvaktsblocket ar en dubblett och inte ett lag.
        if team_name.lower().startswith(("goalkeeping", "goaltending", "malvakt")):
            continue

        content = tables[i + 1] if i + 1 < len(tables) else None
        if content is None or "tblContent" not in (content.get("class") or []):
            continue

        for tr in content.select("tr"):
            cells = [_clean(c.get_text(" ", strip=True)) for c in tr.select("td,th")]
            # Spelarrader har minst Rk, No, Name, Pos plus statistikkolumner.
            if len(cells) < 8:
                continue
            number_raw, name, position = cells[1], cells[2], cells[3]
            if not name or "," not in name:
                continue
            if name.lower() in ("name", "namn"):
                continue

            out.append(
                {
                    "season_group_id": int(season_group_id),
                    "team_name": team_name,
                    "player_name": name,
                    "jersey_number": _safe_int(number_raw) if number_raw.isdigit() else None,
                    "position": position,
                    "games_played": _safe_int(cells[4]),
                    "goals": _safe_int(cells[5]),
                    "assists": _safe_int(cells[6]),
                    "points": _safe_int(cells[7]),
                    "pim": _safe_int(cells[8]) if len(cells) > 8 else 0,
                    "plus_minus": _safe_int(cells[11]) if len(cells) > 11 else 0,
                }
            )

    if not out:
        return [], None

    unique = {f"{r['season_group_id']}_{r['team_name']}_{r['player_name']}": r for r in out}
    return list(unique.values()), url


def _fetch_schedule(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    """Spelschema for grundserie och slutspel.

    De tva sidorna har olika kolumnuppsattning: grundserien har atta kolumner
    med separat tidskolumn, slutspelet sju dar forsta cellen ar omgang. Kolumnen
    med motet hittas darfor pa innehall — cellen som innehaller " - " mellan tva
    lagnamn — och ovriga falt las relativt den, eftersom ordningen efter motet
    ar densamma i bada layouterna.
    """
    url = f"{BASE_URL}/ScheduleAndResults/Schedule/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None

    rows = _extract_schedule_rows(html)
    if not rows:
        return [], None

    # "IF Bjorkloven - IK Oskarshamn Kvartsfinal 1" -> bortalaget utan rundnamn
    stage_re = re.compile(
        r"\s+((?:\u00c5ttondels|Kvarts|Semi|Kval|Slut)?final(?:\s*\d+)?)\s*$",
        re.IGNORECASE,
    )

    out: list[dict[str, Any]] = []
    current_date = ""
    for row in rows:
        cells = row["cells"]

        # Datumet star bara pa dagens forsta match; efterfoljande rader arver det.
        for c in cells[:3]:
            m = re.search(r"\d{4}-\d{2}-\d{2}", c)
            if m:
                current_date = m.group(0)
                break
        if not current_date:
            continue

        gi = next(
            (
                i
                for i, c in enumerate(cells)
                if " - " in c and re.search(r"[A-Za-z\u00c5\u00c4\u00d6\u00e5\u00e4\u00f6]", c)
            ),
            None,
        )
        if gi is None:
            continue

        home_team, away_team = cells[gi].split(" - ", 1)
        home_team = _clean(home_team)
        stage_match = stage_re.search(away_team)
        stage = _clean(stage_match.group(1)) if stage_match else None
        away_team = _clean(stage_re.sub("", away_team))
        if not home_team or not away_team or len(home_team) > 100 or len(away_team) > 100:
            continue

        def cell(offset: int) -> str:
            idx = gi + offset
            return _clean(cells[idx]) if idx < len(cells) else ""

        result_str = cell(1)
        periods = cell(2)
        spectators_raw = cell(3).replace(" ", "").replace("\u00a0", "")
        venue = cell(4)

        # Tiden star i en egen cell i grundserien och tillsammans med datumet
        # i slutspelet.
        time_str = ""
        for c in cells[:gi]:
            m = re.search(r"\b(\d{1,2}:\d{2})\b", c)
            if m:
                time_str = m.group(1)
                break

        out.append(
            {
                "season_group_id": int(season_group_id),
                "team_id": SWEHOCKEY_TEAM_ID,
                "game_id": row["game_id"],
                "match_date": current_date,
                "match_time": time_str,
                "home_team": home_team,
                "away_team": away_team,
                "result": result_str,
                "status": result_str,
                "period_results": periods if periods.startswith("(") else None,
                "spectators": _safe_int(spectators_raw) if spectators_raw.isdigit() else None,
                "venue": venue or None,
                "stage": stage,
            }
        )

    if not out:
        return [], None

    unique: dict[str, dict[str, Any]] = {}
    for r in out:
        key = str(r["game_id"]) if r.get("game_id") else f"{r['match_date']}_{r['home_team']}_{r['away_team']}"
        unique[key] = r
    return list(unique.values()), url



# Handelser, skott och malvakter star pa samma sida. Utan en cache skulle
# varje match hamtas tre ganger, en gang per datatyp, och en sasong ta tre
# gonger sa lang tid. Cachen lever bara under korningen.
_GAME_PAGES: dict[int, str] = {}
_LINEUP_PAGES: dict[int, str] = {}
# Matcher vi redan har i datalagret. Fylls en gang per korning.
_SCRAPED_GAMES: set[int] = set()


# Vad varje match redan har for innehall, per tabell: (tabell, game_id) -> hash.
# Ar hashen oforandrad skrivs matchen inte om.
_GAME_HASHES: dict[tuple[str, int], str] = {}

# Tabeller som hamtas match for match och darmed kan hoppas over per match.
_GAME_TABLES = (
    "swehockey_game_events",
    "swehockey_game_summary",
    "swehockey_game_goalies",
    "swehockey_game_lineups",
    "swehockey_game_boxscore",
)

# Tabeller som hamtas som en hel ogonblicksbild per sasongsgrupp. De ar
# radmassigt tunga — schema, trupp och spelarstatistik ar 1 400 rader per
# grupp — och skrevs om i sin helhet vid varje korning.
_SNAPSHOT_TABLES = (
    "swehockey_player_bio",
    "swehockey_schedule",
    "swehockey_standings",
    "swehockey_player_stats",
    "swehockey_goalie_stats",
    "swehockey_roster",
)

# Ogonblicksbildens hash per (tabell, sasongsgrupp).
_SNAPSHOT_HASHES: dict[tuple[str, str], str] = {}


_LINEAGE_FIELDS = frozenset({"scraped_at", "run_id", "source_url", "content_hash"})


def _content_hash(rows: list[dict[str, Any]]) -> str:
    """Fingeravtryck av en matchs rader, utan harstamningsfalt.

    scraped_at, run_id och source_url satts vid laddningen och skiljer sig at
    mellan korningar aven nar innehallet ar identiskt. De far darfor inte ingaa
    — annars vore varje hash unik och kontrollen meningslos.
    """
    payload = [
        {k: v for k, v in row.items() if k not in _LINEAGE_FIELDS}
        for row in rows
    ]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]



def _load_scraped_games(client: bigquery.Client, season_ids: list[str]) -> None:
    """Vad som redan finns i datalagret: vilka matcher, och med vilket innehall."""
    _SCRAPED_GAMES.clear()
    _GAME_HASHES.clear()
    _REPORT_ETAGS.clear()
    ids = ",".join(str(int(s)) for s in season_ids if str(s).isdigit())
    if not ids:
        return
    try:
        rows = client.query(
            f"""
            SELECT DISTINCT game_id
            FROM `{client.project}.{BQ_DATASET}.swehockey_game_events`
            WHERE season_group_id IN ({ids}) AND game_id IS NOT NULL
            """
        ).result()
        _SCRAPED_GAMES.update(int(r["game_id"]) for r in rows)
        logging.info("%s matcher finns redan i datalagret", len(_SCRAPED_GAMES))
    except Exception:
        # Utan svar hamtar vi hellre for mycket an for lite.
        logging.warning("Kunde inte lasa befintliga matcher", exc_info=True)

    # Hashen for den senaste generationen av varje match. Tabeller som annu
    # saknar kolumnen ger ett fel har; da star ordboken tom for den tabellen
    # och matcherna skrivs om en gang till, varpa kolumnen finns.
    for table in _GAME_TABLES:
        try:
            rows = client.query(
                f"""
                SELECT game_id,
                       ARRAY_AGG(content_hash ORDER BY scraped_at DESC LIMIT 1)[OFFSET(0)] AS h
                FROM `{client.project}.{BQ_DATASET}.{table}`
                WHERE season_group_id IN ({ids}) AND game_id IS NOT NULL
                GROUP BY game_id
                """
            ).result()
            for r in rows:
                if r["h"]:
                    _GAME_HASHES[(table, int(r["game_id"]))] = str(r["h"])
        except Exception as exc:
            logging.info("Ingen hash att jamfora mot i %s: %s", table, str(exc)[:120])

    # Rapporternas ETag, sa en oforandrad PDF kan besvaras med 304 i stallet
    # for 2 MB. Utan den ar villkorliga hamtningar meningslosa: vi skulle inte
    # ha nagot att skicka i If-None-Match.
    try:
        rows = client.query(
            f"""
            SELECT game_id,
                   ARRAY_AGG(source_etag ORDER BY scraped_at DESC LIMIT 1)[OFFSET(0)] AS e
            FROM `{client.project}.{BQ_DATASET}.swehockey_game_boxscore`
            WHERE season_group_id IN ({ids}) AND game_id IS NOT NULL
              AND source_etag IS NOT NULL
            GROUP BY game_id
            """
        ).result()
        for r in rows:
            if r["e"]:
                _REPORT_ETAGS[("MediaGameSummary", int(r["game_id"]))] = str(r["e"])
        logging.info("%s matchrapporter har en ETag att jamfora mot", len(_REPORT_ETAGS))
    except Exception as exc:
        logging.info("Inga ETags att jamfora mot an: %s", str(exc)[:120])

    # Samma sak for ogonblicksbilderna, men en hash per sasongsgrupp.
    for table in _SNAPSHOT_TABLES:
        try:
            rows = client.query(
                f"""
                SELECT season_group_id,
                       ARRAY_AGG(content_hash ORDER BY scraped_at DESC LIMIT 1)[OFFSET(0)] AS h
                FROM `{client.project}.{BQ_DATASET}.{table}`
                WHERE season_group_id IN ({ids})
                GROUP BY season_group_id
                """
            ).result()
            for r in rows:
                if r["h"]:
                    _SNAPSHOT_HASHES[(table, str(r["season_group_id"]))] = str(r["h"])
        except Exception as exc:
            logging.info("Ingen hash att jamfora mot i %s: %s", table, str(exc)[:120])


def _snapshot_unchanged(table: str, season_group_id: str, rows: list[dict[str, Any]]) -> bool:
    """Sant nar hela ogonblicksbilden ar identisk med den som redan ligger inne.

    Schema, tabell, trupp och spelarstatistik hamtas som en hel bild per
    sasongsgrupp — 1 475 rader — och skrevs om i sin helhet vid varje korning.
    Med fyra korningar om dygnet och tva aktiva grupper blev det 11 800 rader
    om dygnet, drygt fyra miljoner om aret, dar de allra flesta var identiska
    kopior. Bilden andras i praktiken bara nar en match spelats.
    """
    h = _content_hash(rows)
    if _SNAPSHOT_HASHES.get((table, str(season_group_id))) == h:
        return True
    for row in rows:
        row["content_hash"] = h
    return False


def _unchanged(table: str, game_id: int, rows: list[dict[str, Any]]) -> bool:
    """Sant nar matchens innehall ar identiskt med det som redan ligger inne.

    Tabellerna ar append-only. En omhamtning som skriver samma rader igen ger
    en ny generation som avdupliceringen sedan maste sortera bort — talen blir
    ratt, men bara sa lange varje lasning kommer ihag att avduplicera. Med fyra
    korningar om dygnet i tjugoen dagar hade en match kunnat fa atttiofyra
    generationer. Skriv darfor bara nar rapporten faktiskt andrats.
    """
    h = _content_hash(rows)
    if _GAME_HASHES.get((table, game_id)) == h:
        return True
    for row in rows:
        row["content_hash"] = h
    return False


# Matchrapporterna ar PDF:er pa 1-2,6 MB. Utan villkorliga hamtningar hade en
# match i tjugoendagarsfonstret hamtats om fyra ganger om dygnet i tre veckor —
# 150 MB per match. Azure svarar korrekt pa If-None-Match, sa en oforandrad
# rapport kostar ett par hundra byte i stallet.
_REPORT_ETAGS: dict[tuple[str, int], str] = {}

REPORT_URL = f"{BASE_URL}/Game/Reports/{{kind}}/{{game_id}}"


def _fetch_report(game_id: int, kind: str) -> tuple[bytes | None, str | None]:
    """Rapporten som PDF, eller (None, None) nar den ar oforandrad.

    Returnerar ocksa (None, None) nar rapporten saknas: sasongens forsta
    matcher har ingen, tva av femtiotva i bade SHL och HockeyAllsvenskan, och
    Azure svarar da med en XML-felsida i stallet for en PDF.
    """
    url = REPORT_URL.format(kind=kind, game_id=int(game_id))
    headers = {"User-Agent": "Mozilla/5.0"}
    known = _REPORT_ETAGS.get((kind, int(game_id)))
    if known:
        headers["If-None-Match"] = known
    try:
        r = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
    except Exception:
        logging.warning("Kunde inte hamta %s for match %s", kind, game_id, exc_info=True)
        return None, None
    if r.status_code == 304:
        return None, known
    if r.status_code != 200 or not r.content[:4] == b"%PDF":
        logging.info("Ingen %s for match %s (HTTP %s)", kind, game_id, r.status_code)
        return None, None
    return r.content, r.headers.get("ETag")


def _upload_raw_pdf(payload: bytes, *, game_id: int, kind: str) -> str | None:
    """Ravaran sparas sa parsern kan forbattras utan att hamta om.

    Sokvagen ar stabil per match och rapporttyp, inte per korning: PDF:en
    hamtas bara nar den faktiskt andrats, och historiken bar de tolkade
    raderna. Att spara varje generation hade kostat hundratals megabyte for
    information som redan finns i tabellen.
    """
    try:
        bucket = storage.Client(project=GCP_PROJECT).bucket(GCS_BUCKET)
        name = f"raw/web_scrapers/swehockey/reports/{kind}/{int(game_id)}.pdf"
        bucket.blob(name).upload_from_string(payload, content_type="application/pdf")
        return f"gs://{GCS_BUCKET}/{name}"
    except Exception:
        logging.warning("Kunde inte spara %s for match %s", kind, game_id, exc_info=True)
        return None


def _team_games(season_group_id: str, limit: int | None) -> list[dict[str, Any]]:
    """Lagets matcher att hamta, nyast forst.

    Med limit=None tas allt — det ar backfill. Annars hamtas bara matcher vi
    saknar, plus de som spelats inom REFRESH_DAYS: en match vi redan har kan
    ha rattats i efterhand, men en fran i host andras inte langre. Utan den
    uppdelningen hamtades de tjugo senaste matcherna om vid varje korning, och
    tabellerna vaxte med tusentals dubblettrader i veckan.
    """
    schedule, _ = _fetch_schedule(season_group_id)
    ours = [
        g
        for g in schedule
        if g.get("game_id")
        and _contains_team_token([str(g.get("home_team", "")), str(g.get("away_team", ""))])
        and str(g.get("result") or "").strip()
    ]
    ours.sort(key=lambda g: str(g.get("match_date") or ""), reverse=True)
    if limit is None:
        return ours

    cutoff = (_now().date() - timedelta(days=REFRESH_DAYS)).isoformat()
    wanted = [
        g
        for g in ours
        if int(g["game_id"]) not in _SCRAPED_GAMES or str(g.get("match_date") or "")[:10] >= cutoff
    ]
    if len(wanted) < len(ours):
        logging.info(
            "Hamtar %s av %s matcher (%s nya eller nyare an %s)",
            min(len(wanted), max(0, limit)), len(ours), len(wanted), cutoff,
        )
    # Taket ar en sakerhetsventil, inte urvalsregeln.
    return wanted[: max(0, limit)]


def _game_html(game_id: int) -> str | None:
    if game_id not in _GAME_PAGES:
        html = _fetch_html(f"{BASE_URL}/Game/Events/{game_id}")
        if html is None:
            return None
        _GAME_PAGES[game_id] = html
    return _GAME_PAGES.get(game_id)


def _lineup_html(game_id: int) -> str | None:
    """Uppstallningen ligger pa en egen sida, inte pa handelsesidan."""
    if game_id not in _LINEUP_PAGES:
        html = _fetch_html(f"{BASE_URL}/Game/LineUps/{game_id}")
        if html is None:
            return None
        _LINEUP_PAGES[game_id] = html
    return _LINEUP_PAGES.get(game_id)


def _fetch_game_lineups(season_group_id: str, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Kedjorna som klubben registrerat, match for match.

    Uppstallningen ar lagets egen indelning i forsta till fjarde kedjan med
    backpar — battre an att gissa kedjor ur vilka som gor mal tillsammans.
    Bada lagens uppstallningar sparas; vilket som ar vart avgors nedstroms.
    """
    out: list[dict[str, Any]] = []
    for game in _team_games(season_group_id, limit):
        html = _lineup_html(int(game["game_id"]))
        if not html:
            continue
        try:
            rows = parse_lineups(html, int(game["game_id"]))
        except Exception:
            logging.exception("Kunde inte tolka uppstallningen for match %s", game["game_id"])
            continue
        for r in rows:
            r["season_group_id"] = int(season_group_id)
            r["match_date"] = game.get("match_date")
            r["source"] = SOURCE
        if _unchanged("swehockey_game_lineups", int(game["game_id"]), rows):
            continue
        out.extend(rows)
    return out, f"{BASE_URL}/Game/LineUps/"


def _fetch_game_summary(season_group_id: str, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Skott, raddningar och PDO per lag och match.

    Skott finns inte i handelserna utan bara i sidhuvudets sammanfattning, och
    det ar enda vagen till skjutprocent, raddningsprocent och darmed PDO.
    """
    out: list[dict[str, Any]] = []
    for game in _team_games(season_group_id, limit):
        html = _game_html(int(game["game_id"]))
        if not html:
            continue
        try:
            summary = parse_game_summary(html, int(game["game_id"]))
        except Exception:
            logging.exception("Kunde inte tolka sammanfattning for match %s", game["game_id"])
            continue
        teams = summary["teams"]
        for row in teams:
            row["season_group_id"] = int(season_group_id)
            row["match_date"] = game.get("match_date")
            row["source"] = SOURCE
        if _unchanged("swehockey_game_summary", int(game["game_id"]), teams):
            continue
        out.extend(teams)
    return out, f"{BASE_URL}/Game/Events/"


def _fetch_game_goalies(season_group_id: str, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Vilken malvakt som stod i vilken match, med raddningsprocent.

    Sasongstabellen ger totaler men inte matchen. Utan den har gar det inte
    att visa en form kurva eller vem som stod nar det small.
    """
    out: list[dict[str, Any]] = []
    for game in _team_games(season_group_id, limit):
        html = _game_html(int(game["game_id"]))
        if not html:
            continue
        try:
            summary = parse_game_summary(html, int(game["game_id"]))
        except Exception:
            logging.exception("Kunde inte tolka malvakter for match %s", game["game_id"])
            continue
        keepers = summary["goalies"]
        for row in keepers:
            row["season_group_id"] = int(season_group_id)
            row["match_date"] = game.get("match_date")
            row["home_team"] = summary["teams"][0].get("team_name")
            row["away_team"] = summary["teams"][1].get("team_name")
            row["source"] = SOURCE
        if _unchanged("swehockey_game_goalies", int(game["game_id"]), keepers):
            continue
        out.extend(keepers)
    return out, f"{BASE_URL}/Game/Events/"


def _fetch_game_events(season_group_id: str, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Handelser match for match: mal, utvisningar och spelarna pa isen.

    Handelsesidan finns bara per match, sa den maste hamtas en i taget. Vi
    begransar oss till lagets egna matcher — ovriga lags handelser anvands
    inte — vilket gor en hel sasong till ett femtiotal anrop.

    Nyast forst, sa att en korning med lag grans anda halls aktuell. Aldre
    matcher ligger redan i tabellen och lases dedupliceradt pa senaste
    scraped_at, sa en ny korning behover inte na dem igen.

    Slutspelet saknar matchlankar hos Swehockey och far darfor inga
    handelser; se docs/SWEHOCKEY_STATS_SCRAPER.md.
    """
    ours = _team_games(season_group_id, limit)
    out: list[dict[str, Any]] = []
    failures = 0
    skipped = 0
    for game in ours:
        gid = int(game["game_id"])
        html = _game_html(gid)
        if not html:
            failures += 1
            continue
        try:
            rows = parse_events(html, gid)
        except Exception:
            logging.exception("Kunde inte tolka handelser for match %s", gid)
            failures += 1
            continue
        for r in rows:
            r["season_group_id"] = int(season_group_id)
            r["match_date"] = game.get("match_date")
            r["source"] = SOURCE
        if _unchanged("swehockey_game_events", gid, rows):
            skipped += 1
            continue
        out.extend(rows)

    if failures:
        logging.warning("Handelser saknas for %s av %s matcher", failures, len(ours))
    if skipped:
        logging.info("Handelser oforandrade for %s av %s matcher", skipped, len(ours))
    return out, f"{BASE_URL}/Game/Events/"


def _fetch_game_boxscore(season_group_id: str, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Skott, tekningar och malvakternas speltid, ur matchrapporten.

    Utespelarnas rader bar Swehockeys OFFICIELLA plus/minus. Det skiljer sig
    fran vart on-ice-tal med ungefar sexton procent — se dokumentationen — och
    de redovisas darfor bredvid varandra i stallet for att ersatta varandra.

    Vilket lag en rad hor till star inte i rapporten, bara i vilken ordning
    lagen listas. Uppstallningen avgor: spelaren finns dar med lagnamn.
    """
    ours = _team_games(season_group_id, limit)
    out: list[dict[str, Any]] = []
    unchanged = missing = 0

    for game in ours:
        gid = int(game["game_id"])
        pdf, etag = _fetch_report(gid, "MediaGameSummary")
        if pdf is None:
            if etag:
                unchanged += 1
            else:
                missing += 1
            continue

        source_url = _upload_raw_pdf(pdf, game_id=gid, kind="MediaGameSummary")
        try:
            parsed = parse_boxscore(pdf, gid)
        except Exception:
            logging.exception("Kunde inte tolka matchrapporten for match %s", gid)
            continue

        # Rapporten skriver rubriken "hemmalag - bortalag" och listar lagen i
        # den ordningen, sa side 1 ar hemmalaget. Uppstallningen far avgora per
        # spelare nar namnet finns dar; sidan ar reserven.
        sides = {1: game.get("home_team"), 2: game.get("away_team")}
        by_name = {
            r.get("player_name"): r.get("team_name")
            for r in parse_lineups(_lineup_html(gid) or "", gid)
        }

        rows = [
            {**r, "role": "skater"} for r in parsed["skaters"]
        ] + [
            {
                **r, "role": "goalie",
                "player_number": r.pop("goalie_number"),
                "player_name": r.pop("goalie_name"),
            }
            for r in parsed["goalies"]
        ]
        for r in rows:
            r["season_group_id"] = int(season_group_id)
            r["match_date"] = game.get("match_date")
            r["team_name"] = by_name.get(r["player_name"]) or sides.get(r["team_side"])
            r["source_etag"] = etag
            r["report_url"] = source_url
            r["source"] = SOURCE
        if _unchanged("swehockey_game_boxscore", gid, rows):
            continue
        out.extend(rows)

    if unchanged or missing:
        logging.info(
            "Matchrapport: %s oforandrade, %s saknas av %s matcher",
            unchanged, missing, len(ours),
        )
    return out, f"{BASE_URL}/Game/Reports/MediaGameSummary/"


def _fetch_player_bio(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    """Fodelsedatum, position och kaptensbindel ur trupprapporten.

    Innehallet ar per lag och sasong, inte per match, sa en rapport racker.
    Den senaste spelade matchen valjs: da ar truppen sa aktuell som mojligt
    och overgangar under sasongen har hunnit slaa igenom.
    """
    games = _team_games(season_group_id, None)
    if not games:
        return [], None
    for game in games[:6]:  # nyast forst; forsta matchen med rapport vinner
        gid = int(game["game_id"])
        pdf, _etag = _fetch_report(gid, "OfficialTeamRoster")
        if pdf is None:
            continue
        _upload_raw_pdf(pdf, game_id=gid, kind="OfficialTeamRoster")
        try:
            rows = parse_team_roster(pdf, gid)
        except Exception:
            logging.exception("Kunde inte tolka trupprapporten for match %s", gid)
            continue
        lineup = parse_lineups(_lineup_html(gid) or "", gid)
        by_name = {r.get("player_name"): r.get("team_name") for r in lineup}
        for r in rows:
            r["season_group_id"] = int(season_group_id)
            r["team_name"] = by_name.get(r["player_name"])
            r["source"] = SOURCE
        return rows, f"{BASE_URL}/Game/Reports/OfficialTeamRoster/"
    logging.info("Ingen trupprapport bland de senaste matcherna for %s", season_group_id)
    return [], f"{BASE_URL}/Game/Reports/OfficialTeamRoster/"


def _scrape_jobs():
    return [
        {
            "data_type": "player_stats",
            "fetcher": _fetch_player_stats,
            "table_name": "swehockey_player_stats",
            "required_fields": ("season_group_id", "team_code", "player_name"),
            "key_fields": ("season_group_id", "team_code", "player_name"),
        },
        {
            "data_type": "goalie_stats",
            "fetcher": _fetch_goalie_stats,
            "table_name": "swehockey_goalie_stats",
            "required_fields": ("season_group_id", "team_code", "goalie_name"),
            "key_fields": ("season_group_id", "team_code", "goalie_name"),
        },
        {
            "data_type": "standings",
            "fetcher": _fetch_standings,
            "table_name": "swehockey_standings",
            "required_fields": ("season_group_id", "team_name", "rank"),
            "key_fields": ("season_group_id", "team_name"),
        },
        {
            "data_type": "schedule",
            "fetcher": _fetch_schedule,
            "table_name": "swehockey_schedule",
            "required_fields": ("season_group_id", "match_date", "home_team", "away_team"),
            "key_fields": ("season_group_id", "match_date", "home_team", "away_team"),
        },
        {
            "data_type": "game_events",
            "fetcher": _fetch_game_events,
            "table_name": "swehockey_game_events",
            "required_fields": ("game_id", "event_type", "time"),
            "key_fields": ("game_id", "event_index"),
        },
        {
            "data_type": "game_summary",
            "fetcher": _fetch_game_summary,
            "table_name": "swehockey_game_summary",
            "required_fields": ("game_id", "season_group_id"),
            "key_fields": ("game_id", "is_home"),
        },
        {
            "data_type": "game_goalies",
            "fetcher": _fetch_game_goalies,
            "table_name": "swehockey_game_goalies",
            "required_fields": ("game_id", "goalie_name"),
            # Lagkoden maste ingaa: bada malvakterna i en match kan bara samma
            # nummer, och 31 ar ett av de vanligaste. Utan den blir nyckeln en
            # dubblett och kvalitetsgrinden stoppar hela laddningen.
            "key_fields": ("game_id", "team_code", "goalie_number"),
        },
        {
            "data_type": "game_lineups",
            "fetcher": _fetch_game_lineups,
            "table_name": "swehockey_game_lineups",
            "required_fields": ("game_id", "team_name", "player_number"),
            # Block och kedja maste ingaa: en spelare kan sta bade i en kedja
            # och bland extraspelarna, och da ar det tva riktiga rader.
            "key_fields": ("game_id", "team_name", "block", "line_number", "player_number"),
        },
        {
            "data_type": "game_boxscore",
            "fetcher": _fetch_game_boxscore,
            "table_name": "swehockey_game_boxscore",
            "required_fields": ("game_id", "player_name", "role"),
            "key_fields": ("game_id", "team_side", "role", "player_number"),
        },
        {
            "data_type": "player_bio",
            "fetcher": _fetch_player_bio,
            "table_name": "swehockey_player_bio",
            "required_fields": ("season_group_id", "player_name", "birthdate"),
            "key_fields": ("season_group_id", "team_side", "player_number"),
        },
        {
            "data_type": "roster",
            "fetcher": _fetch_roster,
            "table_name": "swehockey_roster",
            "required_fields": ("season_group_id", "team_name", "player_name"),
            "key_fields": ("season_group_id", "team_name", "player_name"),
        },
    ]


def _upload_raw_json(
    payload: dict[str, Any],
    *,
    run_id: str,
    season_group_id: str,
    data_type: str,
    scraped_at: str,
):
    scrape_date = scraped_at[:10]
    blob_name = (
        f"raw/web_scrapers/swehockey/{scrape_date}/{run_id}/"
        f"{season_group_id}/{data_type}.json"
    )
    storage_client = storage.Client(project=GCP_PROJECT)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")
    logging.info("Uploaded raw JSON: gs://%s/%s", GCS_BUCKET, blob_name)
    return f"gs://{GCS_BUCKET}/{blob_name}"


def _ensure_dataset(client: bigquery.Client, dataset_id: str):
    ds_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    ds_ref.location = "europe-west1"
    client.create_dataset(ds_ref, exists_ok=True)


def _bq_type_for(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"


def _ensure_columns(client: bigquery.Client, table_id: str, rows: list[dict[str, Any]]):
    """Lagg till kolumner som finns i raderna men inte i tabellen.

    Laddningen anvande tidigare autodetektering av schema. Det fungerar tills en
    parser borjar plocka ett nytt falt: BigQuery gissar da om typerna utifran
    just den satsen och avvisar hela laddningen med "JSON table encountered too
    many errors". Med kolumnerna pa plats i forvag och ett explicit schema
    behovs ingen gissning.

    Returnerar ett schema att ladda med. Nar tabellen inte finns byggs det ur
    raderna i stallet for att lamnas at BigQuerys autodetektering: den gissar
    typerna ur just den satsen, och en strang som "05:07" kan da bli en
    TIME-kolumn. Gissningen sitter kvar for all framtid, sa den ar inte vard
    att ta nar vi redan vet vad falten ar.
    """
    try:
        table = client.get_table(table_id)
    except NotFound:
        fields: dict[str, str] = {}
        for row in rows:
            for key, value in row.items():
                if value is None or key in fields:
                    continue
                fields[key] = _bq_type_for(value)
        if not fields:
            return None
        # Harstamningsfalten ar tidsstamplar i ovriga tabeller och ska vara det
        # har ocksa, sa dedupliceringen pa MAX(scraped_at) beter sig likadant.
        fields["scraped_at"] = "TIMESTAMP"
        logging.info("Skapar %s med %s kolumner", table_id, len(fields))
        return [bigquery.SchemaField(name, kind) for name, kind in sorted(fields.items())]

    existing = {f.name for f in table.schema}
    missing: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            if key in existing or key in missing or value is None:
                continue
            missing[key] = _bq_type_for(value)

    if missing:
        logging.info("Utokar %s med %s", table_id, ", ".join(sorted(missing)))
        table.schema = list(table.schema) + [
            bigquery.SchemaField(name, kind) for name, kind in missing.items()
        ]
        table = client.update_table(table, ["schema"])

    return table.schema


def _append_bq_rows(
    client: bigquery.Client,
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    scraped_at: str,
    run_id: str,
    source_url: str | None,
):
    table_id = f"{client.project}.{BQ_DATASET}.{table_name}"
    if not rows:
        return 0

    enriched = []
    for row in rows:
        item = dict(row)
        item["scraped_at"] = scraped_at
        item["source"] = SOURCE
        item["run_id"] = run_id
        item["source_url"] = source_url
        enriched.append(item)

    # ensure_lineage_columns kastar NotFound nar tabellen inte finns an. En ny
    # datatyp har ingen tabell forsta gangen, och laddningen skapar den sjalv.
    try:
        ensure_lineage_columns(client, table_id)
    except NotFound:
        logging.info("Tabellen %s finns inte an och skapas av laddningen", table_id)

    schema = _ensure_columns(client, table_id, enriched)

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    if schema:
        # Explicit schema i stallet for autodetektering: falt mappas pa namn och
        # typerna kommer fran tabellen, inte fran den har satsen.
        job_config.schema = schema

    job = client.load_table_from_json(enriched, table_id, job_config=job_config)
    try:
        job.result()
    except Exception as exc:
        # Toppnivafelet sager bara "too many errors". Radfelen bakom ar det som
        # faktiskt pekar ut vilket falt som inte gick att lasa.
        details = []
        for err in (job.errors or [])[:3]:
            logging.error("BigQuery-fel for %s: %s", table_name, err)
            msg = err.get("message") if isinstance(err, dict) else str(err)
            if msg:
                details.append(msg)
        if details:
            raise RuntimeError(f"{exc} | {' | '.join(details)}") from exc
        raise
    logging.info("Loaded %d rows into %s", len(enriched), table_id)
    return len(enriched)



def _reconcile(client: bigquery.Client, season_ids: list[str]) -> list[dict[str, Any]]:
    """Stammer de harledda talen mot det Swehockey sjalv redovisar?

    Kvalitetsgrinden kontrollerar form — att rader finns, att falt ar ifyllda,
    att nycklar ar unika. Den sager ingenting om varden. Nar utvisningarna
    tredubblades var varje rad valformad; det fanns bara tre generationer av
    dem, och en lasning som glomt avduplicera summerade alla.

    De har kontrollerna jamfor i stallet tal som maste ga ihop. En avvikelse
    betyder att nagot ar fel, aven om alla rader ser prydliga ut.
    """
    ids = ",".join(str(int(x)) for x in season_ids if str(x).isdigit())
    if not ids:
        return []
    proj = client.project
    checks: list[dict[str, Any]] = []

    def _run(name: str, sql: str, note: str = "") -> None:
        try:
            row = next(iter(client.query(sql).result()), None)
        except Exception as exc:
            checks.append({"name": name, "ok": None, "note": f"kunde inte koras: {exc}"[:180]})
            return
        if row is None:
            checks.append({"name": name, "ok": None, "note": "inget att jamfora"})
            return
        d = dict(row.items())
        a, b = d.get("a"), d.get("b")
        checks.append(
            {
                "name": name,
                "ok": a == b,
                "observed": a,
                "expected": b,
                "note": note,
            }
        )

    # Malen i handelserna maste vara lika manga som malen i matchresultaten.
    # Fangar bade dubbletter, tappade matcher och parserfel — det var precis
    # sa fjorton tomma-mal-mal upptacktes saknas.
    #
    # Kontrollen drog forst bort ett mal for varje match avgjord pa straffar,
    # eftersom avgorandet inte fanns i handelselistan. Parsern plockar upp det
    # numera, sa avdraget gjorde kontrollen fel at andra hallet: 289 mot 283.
    _run(
        "events_goals_match_results",
        f"""
        WITH ev AS (
            SELECT COUNT(*) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_events` e
            INNER JOIN (
                SELECT game_id, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_events`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON e.game_id = k.game_id AND e.scraped_at = k.m
            WHERE e.event_type = 'goal' AND e.season_group_id IN ({ids})
        ),
        sc AS (
            SELECT SUM(
                CAST(REGEXP_EXTRACT(a.result, r'^\\s*(\\d+)') AS INT64)
                + CAST(REGEXP_EXTRACT(a.result, r'-\\s*(\\d+)') AS INT64)
            ) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_schedule` a
            INNER JOIN (
                SELECT game_id, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_schedule`
                WHERE season_group_id IN ({ids}) AND game_id IS NOT NULL GROUP BY game_id
            ) k ON a.game_id = k.game_id AND a.scraped_at = k.m
            WHERE a.season_group_id IN ({ids})
              AND a.game_id IN (SELECT DISTINCT game_id
                                FROM `{proj}.{BQ_DATASET}.swehockey_game_events`
                                WHERE season_group_id IN ({ids}))
              AND REGEXP_CONTAINS(a.result, r'\\d+\\s*-\\s*\\d+')
        )
        SELECT (SELECT n FROM ev) AS a, (SELECT n FROM sc) AS b
        """,
        "mal i handelserna mot mal i matchresultaten",
    )

    # Skottsammanfattningen ska ha tva rader per match, en per lag.
    _run(
        "summary_two_rows_per_game",
        f"""
        SELECT COUNTIF(n <> 2) AS a, 0 AS b FROM (
            SELECT game_id, COUNT(*) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_summary` a
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_summary`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON a.game_id = k.g AND a.scraped_at = k.m
            WHERE a.season_group_id IN ({ids})
            GROUP BY game_id
        )
        """,
        "antal matcher med annat an tva lagrader",
    )

    # En malvakts raddningar plus inslappta ar per definition skotten mot.
    _run(
        "goalie_saves_plus_goals_equal_shots",
        f"""
        SELECT COUNTIF(saves + goals_against <> shots_against) AS a, 0 AS b
        FROM `{proj}.{BQ_DATASET}.swehockey_game_goalies` a
        INNER JOIN (
            SELECT game_id AS g, MAX(scraped_at) AS m
            FROM `{proj}.{BQ_DATASET}.swehockey_game_goalies`
            WHERE season_group_id IN ({ids}) GROUP BY game_id
        ) k ON a.game_id = k.g AND a.scraped_at = k.m
        WHERE a.season_group_id IN ({ids})
        """,
        "malvaktsrader dar raddningar plus inslappta inte ar skotten mot",
    )

    # Utvisningsminuterna star bade i handelselistan och i matchsammanfattningen,
    # och de rakas fram pa olika satt. Det var precis har det gick fel forra
    # gangen: handelserna summerade tre skorningsgenerationer och gav 223 PIM
    # dar rapporten sa 75. Jamfor bara matcher som finns i bada tabellerna.
    _run(
        "penalties_events_match_summary",
        f"""
        WITH ev AS (
            SELECT e.game_id, SUM(e.penalty_minutes) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_events` e
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_events`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON e.game_id = k.g AND e.scraped_at = k.m
            WHERE e.season_group_id IN ({ids})
            GROUP BY e.game_id
        ),
        su AS (
            SELECT a.game_id, SUM(a.pim) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_summary` a
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_summary`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON a.game_id = k.g AND a.scraped_at = k.m
            WHERE a.season_group_id IN ({ids})
            GROUP BY a.game_id
        )
        SELECT COUNTIF(ev.n <> su.n) AS a, 0 AS b
        FROM ev INNER JOIN su USING (game_id)
        """,
        "matcher dar handelsernas utvisningsminuter inte stammer med rapportens",
    )

    # Lagets skott i sammanfattningen ar motstandarmalvaktens skott emot.
    #
    # Nastan. Ett skott i tomt mal raknas for laget men mot ingen malvakt, sa
    # skillnaden per match ar exakt antalet mal i tomt mal. Matt over HA 25/26:
    # tretton matcher med skillnad, alla tretton med tomma-mal-mal, och
    # skillnaden lika med antalet sadana mal i var och en.
    _run(
        "summary_shots_match_goalie_shots_against",
        f"""
        WITH su AS (
            SELECT a.game_id, SUM(a.shots) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_summary` a
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_summary`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON a.game_id = k.g AND a.scraped_at = k.m
            WHERE a.season_group_id IN ({ids})
            GROUP BY a.game_id
        ),
        gk AS (
            SELECT a.game_id, SUM(a.shots_against) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_goalies` a
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_goalies`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON a.game_id = k.g AND a.scraped_at = k.m
            WHERE a.season_group_id IN ({ids})
            GROUP BY a.game_id
        ),
        eng AS (
            SELECT e.game_id, COUNTIF(e.is_empty_net) AS n
            FROM `{proj}.{BQ_DATASET}.swehockey_game_events` e
            INNER JOIN (
                SELECT game_id AS g, MAX(scraped_at) AS m
                FROM `{proj}.{BQ_DATASET}.swehockey_game_events`
                WHERE season_group_id IN ({ids}) GROUP BY game_id
            ) k ON e.game_id = k.g AND e.scraped_at = k.m
            WHERE e.season_group_id IN ({ids}) AND e.event_type = 'goal'
            GROUP BY e.game_id
        )
        SELECT COUNTIF(su.n - gk.n <> IFNULL(eng.n, 0)) AS a, 0 AS b
        FROM su INNER JOIN gk USING (game_id) LEFT JOIN eng USING (game_id)
        """,
        "matcher dar skott minus malvakternas skott emot inte ar antalet mal i tomt mal",
    )

    return checks


@functions_framework.http
def run_swehockey_stats_scraper(request):
    scraped_at = _now().isoformat()
    _GAME_PAGES.clear()
    _LINEUP_PAGES.clear()
    bq_client = bigquery.Client(project=GCP_PROJECT)
    _ensure_dataset(bq_client, BQ_DATASET)

    # Sasonger kan anges explicit for backfill:
    #   ?seasons=18266,19979
    # Utan parametern koras de sasonger som ar markerade aktiva. Scrapern rorde
    # tidigare bara aktiva sasonger, sa avslutade sasonger fick aldrig falt som
    # lagts till i efterhand — som game_id, period_results och trupplistan.
    requested = ""
    try:
        requested = (request.args.get("seasons") or "").strip()
    except Exception:
        requested = ""

    # Handelsesidan hamtas per match och ar det enda som skalar med antalet
    # matcher. ?events_limit=all tar hela sasongen — anvands vid backfill —
    # medan en schemalagd korning nojer sig med de senaste.
    events_limit: int | None = EVENTS_LIMIT_DEFAULT
    try:
        raw_limit = (request.args.get("events_limit") or "").strip().lower()
    except Exception:
        raw_limit = ""
    if raw_limit in ("all", "alla"):
        events_limit = None
    elif raw_limit.isdigit():
        events_limit = int(raw_limit)

    active_season_ids: list[str] = []

    if requested:
        active_season_ids = [p.strip() for p in requested.split(",") if p.strip().isdigit()]
        if not active_season_ids:
            return (
                json.dumps(
                    {"status": "error", "error": "seasons maste vara kommaseparerade heltal."},
                    ensure_ascii=False,
                ),
                400,
                {"Content-Type": "application/json"},
            )
        logging.info("Backfill for sasonger: %s", ",".join(active_season_ids))
    else:
        try:
            query = f"SELECT regular_season_id, playoff_id FROM `{bq_client.project}.{BQ_DATASET}.swehockey_seasons` WHERE is_active = TRUE"
            for row in bq_client.query(query).result():
                if row.get("regular_season_id"):
                    active_season_ids.append(str(row["regular_season_id"]))
                if row.get("playoff_id"):
                    active_season_ids.append(str(row["playoff_id"]))
        except Exception as e:
            logging.error("Failed to fetch active seasons from BQ, falling back to env var: %s", e)

    if not active_season_ids:
        active_season_ids = [SWEHOCKEY_SEASON_GROUP_ID]
    
    active_season_ids = sorted(set(active_season_ids), key=int)
    # Backfill hamtar alla matchsidor; en vanlig korning bara de som saknas
    # eller kan ha rattats. Bada behover veta vad som redan ligger inne — aven
    # backfill, som annars hade skrivit om en hel sasong identiskt.
    _load_scraped_games(bq_client, active_season_ids)
    run_logger = BigQueryRunLogger(bq_client)
    run_id = run_logger.start_run(
        pipeline_name=PIPELINE_NAME,
        source=SOURCE,
        season_group_ids=[int(value) for value in active_season_ids],
        metadata={"scraped_at": scraped_at},
    )
    result: dict[str, Any] = {
        "status": "running",
        "run_id": run_id,
        "scraped_at": scraped_at,
        "season_group_ids": [int(value) for value in active_season_ids],
        "types": {},
    }
    fetched_batches: list[dict[str, Any]] = []
    fetched_rows = 0
    loaded_rows = 0
    failed_steps = 0

    try:
        for season_group_id in active_season_ids:
            for job in _scrape_jobs():
                data_type = job["data_type"]
                if data_type in ("game_events", "game_summary", "game_goalies",
                                 "game_lineups", "game_boxscore"):
                    rows, source_url = job["fetcher"](season_group_id, events_limit)
                else:
                    rows, source_url = job["fetcher"](season_group_id)
                fetched_rows += len(rows)
                # Matchtabellerna hoppar over oforandrade matcher redan i
                # hamtaren. Ogonblicksbilderna kommer hela och jamfors har.
                unchanged = bool(rows) and job["table_name"] in _SNAPSHOT_TABLES and (
                    _snapshot_unchanged(job["table_name"], season_group_id, rows)
                )
                fetched_batches.append(
                    {
                        **job,
                        "season_group_id": season_group_id,
                        "rows": rows,
                        "source_url": source_url,
                        "unchanged": unchanged,
                    }
                )
                type_result = result["types"].setdefault(
                    data_type,
                    {"ok": True, "rows": 0, "bq_loaded": 0, "source_urls": []},
                )
                type_result["rows"] += len(rows)
                if unchanged:
                    type_result["unchanged"] = True
                if source_url:
                    type_result["source_urls"].append(source_url)

        season_has_games = {}
        for batch in fetched_batches:
            if batch["data_type"] == "standings":
                season_has_games[batch["season_group_id"]] = any(
                    int(row.get("games_played") or 0) > 0 for row in batch["rows"]
                )

        for batch in fetched_batches:
            data_type = batch["data_type"]
            season_group_id = batch["season_group_id"]
            # Tomma snapshots ar vantade i vissa lagen och far inte falla hela
            # koringen. Utover statistik fore seriestart galler det trupplistan:
            # slutspelsgrupper har ingen PlayersByTeam-sida alls, och Swehockey
            # visar bara lag vars klubb hunnit registrera sin trupp.
            allow_preseason_empty = (
                # game_events ar tomt for slutspelsgrupper, som saknar
                # matchlankar hos Swehockey, och fore seriestart.
                data_type in {
                    "roster", "standings",
                    "game_events", "game_summary", "game_goalies", "game_lineups",
                    # Rapporten saknas for sasongens forsta matcher, och en
                    # korning dar inget andrats skriver inga rader alls.
                    "game_boxscore", "player_bio",
                }
                or (
                    data_type in {"player_stats", "goalie_stats"}
                    and not season_has_games.get(season_group_id, False)
                )
            )
            checks = validate_rows(
                batch["rows"],
                required_fields=batch["required_fields"],
                key_fields=batch["key_fields"],
                empty_severity="WARNING" if allow_preseason_empty else "ERROR",
            )
            run_logger.record_checks(
                run_id=run_id,
                pipeline_name=PIPELINE_NAME,
                entity_name=data_type,
                season_group_id=int(season_group_id),
                checks=checks,
            )
            batch_ok = checks_passed(checks)
            result["types"][data_type]["ok"] = (
                result["types"][data_type]["ok"] and batch_ok
            )
            if not batch_ok:
                failed_steps += 1

        if failed_steps:
            result["status"] = "failed_quality_gate"
            run_logger.finish_run(
                run_id=run_id,
                status="FAILED_QUALITY",
                fetched_rows=fetched_rows,
                loaded_rows=0,
                failed_steps=failed_steps,
                metadata={"types": result["types"]},
                error_message="En eller flera snapshots underkändes före publicering.",
            )
            return json.dumps(result, ensure_ascii=False), 500, {"Content-Type": "application/json"}

        for batch in fetched_batches:
            data_type = batch["data_type"]
            season_group_id = batch["season_group_id"]
            # Oforandrad ogonblicksbild: raderna ar redan validerade, men de
            # ar ord for ord samma som senaste generationen. Att skriva dem
            # igen ger bara en generation till att deduplicera bort.
            if batch.get("unchanged"):
                logging.info(
                    "%s for sasong %s oforandrad — hoppar over skrivningen",
                    data_type, season_group_id,
                )
                continue
            payload = {
                "meta": {
                    "run_id": run_id,
                    "source": SOURCE,
                    "type": data_type,
                    "team_id": SWEHOCKEY_TEAM_ID,
                    "season_group_id": int(season_group_id),
                    "source_url": batch["source_url"],
                    "scraped_at": scraped_at,
                },
                "rows": batch["rows"],
            }
            # En felande datatyp far inte hindra ovriga fran att publiceras.
            # Batcharna laddas i en gemensam loop, sa ett obehandlat fel i en
            # av dem lamnade tidigare resten oladdade — och eftersom roster
            # ligger sist blev halva schemat kvar i luften.
            try:
                _upload_raw_json(
                    payload,
                    run_id=run_id,
                    season_group_id=season_group_id,
                    data_type=data_type,
                    scraped_at=scraped_at,
                )
                loaded = _append_bq_rows(
                    bq_client,
                    batch["table_name"],
                    batch["rows"],
                    scraped_at=scraped_at,
                    run_id=run_id,
                    source_url=batch["source_url"],
                )
            except Exception as load_err:
                logging.exception(
                    "Kunde inte ladda %s for sasong %s", data_type, season_group_id
                )
                result["types"][data_type]["ok"] = False
                result["types"][data_type]["error"] = str(load_err)[:400]
                failed_steps += 1
                loaded = 0
            loaded_rows += loaded
            result["types"][data_type]["bq_loaded"] += loaded

        # Sag ifran nar bara delar av koringen gick igenom, i stallet for att
        # rapportera ok och lata en tom tabell se ut som ett tomt resultat.
        result["status"] = "ok" if not failed_steps else "partial"

        # Avstamningen laser tabellerna efter laddningen och jamfor tal som
        # maste ga ihop. Den far aldrig falla en korning som annars gick bra —
        # datat ar redan skrivet — men en avvikelse ska synas i svaret och i
        # korloggen sa den gar att larma pa.
        try:
            reconciliation = _reconcile(bq_client, active_season_ids)
        except Exception:
            logging.exception("Avstamningen kunde inte koras run_id=%s", run_id)
            reconciliation = []
        if reconciliation:
            result["reconciliation"] = reconciliation
            mismatched = [c["name"] for c in reconciliation if c.get("ok") is False]
            if mismatched:
                result["reconciliation_failed"] = mismatched
                logging.error(
                    "Avstamningen gick inte ihop run_id=%s: %s", run_id, ", ".join(mismatched)
                )
        run_logger.finish_run(
            run_id=run_id,
            status="SUCCESS" if not failed_steps else "PARTIAL",
            fetched_rows=fetched_rows,
            loaded_rows=loaded_rows,
            failed_steps=failed_steps,
            metadata={"types": result["types"], "reconciliation": reconciliation},
        )
        return json.dumps(result, ensure_ascii=False), 200, {"Content-Type": "application/json"}
    except Exception as exc:
        logging.exception("Swehockey ingestion failed run_id=%s", run_id)
        result["status"] = "failed"
        result["error"] = str(exc)
        try:
            run_logger.finish_run(
                run_id=run_id,
                status="FAILED",
                fetched_rows=fetched_rows,
                loaded_rows=loaded_rows,
                failed_steps=max(failed_steps, 1),
                metadata={"types": result["types"]},
                error_message=str(exc),
            )
        except Exception:
            logging.exception("Failed to finalize ingestion run_id=%s", run_id)
        return json.dumps(result, ensure_ascii=False), 500, {"Content-Type": "application/json"}
