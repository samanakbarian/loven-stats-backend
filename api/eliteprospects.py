"""Direktlankar till spelarnas sidor pa EliteProspects.

En EP-lank kraver spelarens numeriska id — `/player/{id}/{slug}` — och det
finns inte i Swehockeys data. Sokvagen hit ar EP:s egen autocomplete, som
returnerar id, slug, fodelsear och nuvarande klubb for ett namn. Vi hamtar
alltsa ingen statistik darifran, bara den identifierare som lanken behover.

Trafiken mot EP halls nere i tre steg:

  1. En process-lokal cache (ett dygn).
  2. En tabell i BigQuery, sa att en omstartad instans inte borjar om.
  3. Uppslag sker bara for namn som saknas i bada.

For en trupp pa 25 spelare betyder det nagra anrop per sasong.

Matchningen kraver alltid att efternamnet stammer, och gar bara vidare till
en direktlank nar nagot mer bekraftar spelaren — att EP har hen i Bjorkloven,
eller att fodelsearet stammer med aldern vi kanner till. Racker det inte
lamnas en soklank i stallet: en lank till fel spelare ar samre an ingen.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from cachetools import TTLCache

AUTOCOMPLETE_URL = "https://autocomplete.eliteprospects.com/all"
PLAYER_URL = "https://www.eliteprospects.com/player/{id}/{slug}"
SEARCH_URL = "https://www.eliteprospects.com/search/player?name={q}"
TABLE = "eliteprospects_links"

# EP svarar snabbt; hellre en soklank an en langsam sida.
TIMEOUT = 6
MAX_WORKERS = 8
USER_AGENT = "lovenlaget/1.0 (+https://viskauppigen.netlify.app)"

# Ett dygn racker: en spelares EP-id andras aldrig.
_cache: TTLCache = TTLCache(maxsize=512, ttl=86400)

_TEAM = "bjorkloven"


def _fold(value: Any) -> str:
    """Gemener utan diakriter, bindestreck som mellanslag."""
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def human_name(name: str) -> str:
    """'Efternamn, Fornamn' -> 'Fornamn Efternamn'.

    Swehockey markerar vissa spelare med asterisker ("Andersson, Sebastian**");
    de hor inte till namnet och forstor bade uppslag och lank.
    """
    clean = re.sub(r"[*\u2020\u2021]+", "", str(name or "")).strip()
    parts = [p.strip() for p in clean.split(",")]
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 and parts[1] else clean


def name_key(name: str) -> str:
    return _fold(human_name(name))


def search_link(name: str) -> str:
    return SEARCH_URL.format(q=quote(human_name(name)))


def _surname(folded: str) -> str:
    """Efternamnet, dubbelnamn inraknat: 'liam dower nilsson' -> 'dower nilsson'."""
    parts = folded.split()
    return " ".join(parts[1:]) if len(parts) > 1 else folded


# Swehockeys positioner mot EP:s grovre indelning.
_POSITION = {
    "lw": "F", "rw": "F", "ce": "F", "c": "F", "f": "F", "fw": "F",
    "ld": "D", "rd": "D", "d": "D",
    "gk": "G", "g": "G",
}


def _first_names_agree(want: str, cand: str) -> bool:
    """Tal stavningsvarianter men inte olika personer.

    Lucas/Lukas och Chris/Christopher ska ga igenom; Oliwer/Sebastian inte.
    Utan den har sparren racker det att efternamnet och klubben stammer for
    att lanka till en lagkamrat med samma efternamn.
    """
    if not want or not cand:
        return False
    if want == cand or want.startswith(cand) or cand.startswith(want):
        return True
    return want[:2] == cand[:2]


def _positions_agree(want: str | None, cand: str | None) -> bool:
    """Okand position pa nagondera sidan far inte diskvalificera nagon."""
    a = _POSITION.get(_fold(want).replace(" ", ""))
    b = _fold(cand).upper()[:1] if cand else ""
    if not a or b not in ("F", "D", "G"):
        return True
    return a == b


def _score(candidate: dict, folded: str, age: int | None, position: str | None,
           year_now: int) -> int:
    """Hur val en traff stammer med spelaren vi soker.

    Efternamn, fornamn och position ar krav — brister nagot av dem ar det en
    annan person och traffen forkastas helt. Resten avgor hur sakert det ar.
    """
    cand = _fold(candidate.get("fullname"))
    if not cand:
        return -1
    want_parts, cand_parts = folded.split(), cand.split()
    if not want_parts or not cand_parts:
        return -1
    if _surname(folded) != _surname(cand):
        return -1
    if not _first_names_agree(want_parts[0], cand_parts[0]):
        return -1
    if not _positions_agree(position, candidate.get("position")):
        return -1

    score = 0
    if _TEAM in _fold(candidate.get("team")):
        score += 3
    if want_parts[0] == cand_parts[0]:
        score += 2
    if folded == cand:
        score += 1

    born = str(candidate.get("age") or "")
    if age and born.isdigit() and abs((year_now - int(born)) - int(age)) <= 1:
        score += 2

    return score


def _query(term: str) -> list[dict]:
    try:
        r = requests.get(
            AUTOCOMPLETE_URL,
            params={"q": term},
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return [x for x in data if isinstance(x, dict) and x.get("_type") == "player"]
    except Exception:
        return []


def _resolve_one(name: str, age: int | None, position: str | None = None) -> dict[str, Any]:
    """Ett uppslag mot EP. Returnerar alltid en anvandbar lank."""
    folded = name_key(name)
    fallback = {"url": search_link(name), "confidence": "search", "id": None, "slug": None}
    if not folded:
        return fallback

    year_now = datetime.now(timezone.utc).year
    candidates = _query(human_name(name))
    # Andra forsoket: bara efternamnet. Fangar Lukas/Lucas och Chris/
    # Christopher, dar hela namnet inte ger nagon traff alls.
    if not any(_score(c, folded, age, position, year_now) >= 3 for c in candidates):
        seen = {str(c.get("id")) for c in candidates}
        candidates += [c for c in _query(_surname(folded)) if str(c.get("id")) not in seen]

    scored = [((_score(c, folded, age, position, year_now)), c) for c in candidates]
    scored = [pair for pair in scored if pair[0] >= 0]
    if not scored:
        return fallback

    scored.sort(key=lambda pair: pair[0], reverse=True)
    score, hit = scored[0]

    # Ett namn som stammer exakt och som ingen annan spelare delar duger aven
    # utan klubbtraff — sa far spelare som lamnat foreningen ocksa en lank.
    exact_name = [c for _, c in scored if _fold(c.get("fullname")) == folded]
    unique_exact = len(exact_name) == 1 and _fold(hit.get("fullname")) == folded

    if score < 3 and not unique_exact:
        return fallback
    if not hit.get("id") or not hit.get("slug"):
        return fallback

    return {
        "url": PLAYER_URL.format(id=hit["id"], slug=hit["slug"]),
        "confidence": "exact" if score >= 5 else "probable",
        "id": str(hit["id"]),
        "slug": str(hit["slug"]),
        "ep_name": hit.get("fullname"),
        "ep_team": hit.get("team"),
        "born": hit.get("age"),
    }


def _read_bq(bq, keys: list[str]) -> dict[str, dict]:
    """Tidigare uppslag. Senaste raden per namn vinner, sa en felaktig
    matchning kan rattas genom att lagga till en ny rad."""
    if not keys:
        return {}
    quoted = ",".join("'" + k.replace("'", "") + "'" for k in keys)
    try:
        rows = bq.query(
            f"""
            SELECT a.*
            FROM `{bq.project}.raw_sports.{TABLE}` a
            INNER JOIN (
                SELECT name_key, MAX(resolved_at) AS latest
                FROM `{bq.project}.raw_sports.{TABLE}`
                WHERE name_key IN ({quoted})
                GROUP BY name_key
            ) b ON a.name_key = b.name_key AND a.resolved_at = b.latest
            """
        ).result()
    except Exception:
        # Tabellen finns inte forsta gangen — det ar inget fel.
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r.items())
        if d.get("ep_id") and d.get("ep_slug"):
            out[d["name_key"]] = {
                "url": PLAYER_URL.format(id=d["ep_id"], slug=d["ep_slug"]),
                "confidence": d.get("confidence") or "probable",
                "id": d["ep_id"],
                "slug": d["ep_slug"],
                # Samma form som ett farskt uppslag ger. Falten skrevs till
                # tabellen men lastes aldrig tillbaka, sa en spelare hade
                # namn, lag och fodelsear forsta gangen han slogs upp och
                # tappade dem sa fort raden cachats.
                "ep_name": d.get("ep_name"),
                "ep_team": d.get("ep_team"),
                "born": d.get("born"),
            }
        else:
            out[d["name_key"]] = {
                "url": SEARCH_URL.format(q=quote(d.get("display_name") or "")),
                "confidence": "search",
                "id": None,
                "slug": None,
            }
    return out


def _write_bq(bq, rows: list[dict]) -> None:
    if not rows:
        return
    from google.cloud import bigquery

    table_id = f"{bq.project}.raw_sports.{TABLE}"
    schema = [
        bigquery.SchemaField("name_key", "STRING"),
        bigquery.SchemaField("display_name", "STRING"),
        bigquery.SchemaField("ep_id", "STRING"),
        bigquery.SchemaField("ep_slug", "STRING"),
        bigquery.SchemaField("ep_name", "STRING"),
        bigquery.SchemaField("ep_team", "STRING"),
        bigquery.SchemaField("confidence", "STRING"),
        bigquery.SchemaField("resolved_at", "TIMESTAMP"),
    ]
    try:
        bq.load_table_from_json(
            rows,
            table_id,
            job_config=bigquery.LoadJobConfig(
                schema=schema,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            ),
        ).result()
    except Exception:
        # Cachen ar en optimering. Misslyckas skrivningen slar vi upp igen
        # nasta gang i stallet for att fela sidan.
        logging.warning("Kunde inte spara EliteProspects-lankar", exc_info=True)


def links_for(players: list[dict], bq=None) -> dict[str, dict]:
    """Lank per spelarnamn, i samma form som spelaren angavs.

    `players` ar dictar med minst `name`, garna `age`. Anropet ar fail-soft:
    varje spelare far minst en soklank.
    """
    wanted: dict[str, dict] = {}
    for p in players:
        raw = p.get("name") or p.get("player_name") or ""
        key = name_key(raw)
        if key and key not in wanted:
            wanted[key] = {"name": raw, "age": p.get("age"), "position": p.get("position")}
    if not wanted:
        return {}

    resolved: dict[str, dict] = {k: v for k, v in _cache.items() if k in wanted}

    missing = [k for k in wanted if k not in resolved]
    if missing and bq is not None:
        stored = _read_bq(bq, missing)
        resolved.update(stored)
        _cache.update(stored)
        missing = [k for k in wanted if k not in resolved]

    if missing:
        fresh: dict[str, dict] = {}
        try:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    key: pool.submit(
                        _resolve_one, wanted[key]["name"], wanted[key]["age"],
                        wanted[key].get("position"),
                    )
                    for key in missing
                }
                for key, fut in futures.items():
                    try:
                        fresh[key] = fut.result(timeout=TIMEOUT + 4)
                    except Exception:
                        fresh[key] = {
                            "url": search_link(wanted[key]["name"]),
                            "confidence": "search", "id": None, "slug": None,
                        }
        except Exception:
            logging.warning("EliteProspects-uppslag misslyckades", exc_info=True)
            fresh = {
                key: {"url": search_link(wanted[key]["name"]), "confidence": "search",
                      "id": None, "slug": None}
                for key in missing
            }

        resolved.update(fresh)
        _cache.update(fresh)

        if bq is not None:
            now = datetime.now(timezone.utc).isoformat()
            _write_bq(bq, [
                {
                    "name_key": key,
                    "display_name": human_name(wanted[key]["name"]),
                    "ep_id": v.get("id"),
                    "ep_slug": v.get("slug"),
                    "ep_name": v.get("ep_name"),
                    "ep_team": v.get("ep_team"),
                    "confidence": v.get("confidence"),
                    "resolved_at": now,
                }
                for key, v in fresh.items()
            ])

    # Nyckla om till spelarnas egna namn sa anroparen slipper normalisera.
    out: dict[str, dict] = {}
    for p in players:
        raw = p.get("name") or p.get("player_name") or ""
        key = name_key(raw)
        out[raw] = resolved.get(key) or {
            "url": search_link(raw), "confidence": "search", "id": None, "slug": None,
        }
    return out
