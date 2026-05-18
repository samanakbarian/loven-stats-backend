import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import functions_framework
import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.cloud import storage

logging.basicConfig(level=logging.INFO)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "granskaren-d51a1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "loven-stats-raw-data-prod")
SWEHOCKEY_TEAM_ID = os.environ.get("SWEHOCKEY_TEAM_ID", "1139")
SWEHOCKEY_SEASON_GROUP_ID = os.environ.get("SWEHOCKEY_SEASON_GROUP_ID", "18263")
BASE_URL = "https://stats.swehockey.se"
BQ_DATASET = "raw_sports"
SOURCE = "swehockey"
TEAM_TOKENS = [t.strip().lower() for t in os.environ.get("SWEHOCKEY_TEAM_TOKENS", "björklöven,bjorkloven,löven,bjo").split(",") if t.strip()]


def _now():
    return datetime.now(timezone.utc)


def _timestamp_str() -> str:
    return _now().strftime("%Y%m%d_%H%M%S")


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


def _fetch_player_stats() -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/Teams/Info/PlayersByTeam/{SWEHOCKEY_TEAM_ID}",
        f"{BASE_URL}/Players/Statistics/ScoringLeaders/{SWEHOCKEY_SEASON_GROUP_ID}",
    ]
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        tables = soup.select("table")
        if not tables:
            continue
        out = []
        for table in tables:
            for tr in table.select("tr"):
                r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
                if len(r) < 12 or _is_header_row(r) or not _safe_int(r[0]):
                    continue
                team_code = _clean(r[3])
                out.append(
                    {
                        "season_group_id": SWEHOCKEY_SEASON_GROUP_ID,
                        "team_id": SWEHOCKEY_TEAM_ID,
                        "team_code": team_code,
                        "player_name": _clean(r[2]),
                        "jersey_number": _safe_int(r[1]),
                        "position": _clean(r[4]),
                        "games_played": _safe_int(r[5]),
                        "goals": _safe_int(r[6]),
                        "assists": _safe_int(r[7]),
                        "points": _safe_int(r[8]),
                        "plus_minus": _safe_int(r[11]),
                        "pim": _safe_int(r[10]),
                    }
                )
        if out:
            filtered = [row for row in out if _contains_team_token([row.get("team_code", ""), row.get("player_name", "")])]
            if filtered:
                return filtered, url
            logging.warning("Player stats: no team-token matches for team_id=%s, returning league rows", SWEHOCKEY_TEAM_ID)
            return out, url
    return [], None


def _fetch_goalie_stats() -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/Players/Statistics/LeadingGoaliesSVS/{SWEHOCKEY_SEASON_GROUP_ID}",
    ]
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        tables = soup.select("table")
        if not tables:
            continue
        out = []
        for table in tables:
            for tr in table.select("tr"):
                r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
                if len(r) < 13 or _is_header_row(r) or not _safe_int(r[0]):
                    continue
                team_code = _clean(r[3])
                out.append(
                    {
                        "season_group_id": SWEHOCKEY_SEASON_GROUP_ID,
                        "team_id": SWEHOCKEY_TEAM_ID,
                        "team_code": team_code,
                        "goalie_name": _clean(r[2]),
                        "games_played": _safe_int(r[5]),
                        "shots_against": _safe_int(r[10]),
                        "saves": _safe_int(r[11]),
                        "goals_against": _safe_int(r[7]),
                        "save_pct": _safe_float(r[12]),
                        "gaa": _safe_float(r[13] if len(r) > 13 else None),
                        "toi_minutes": 0,
                    }
                )
        if out:
            filtered = [row for row in out if _contains_team_token([row.get("team_code", ""), row.get("goalie_name", "")])]
            if filtered:
                return filtered, url
            logging.warning("Goalie stats: no team-token matches for team_id=%s, returning league rows", SWEHOCKEY_TEAM_ID)
            return out, url
    return [], None


def _fetch_standings() -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/ScheduleAndResults/Standings/{SWEHOCKEY_SEASON_GROUP_ID}",
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
            if len(r) < 9 or not _safe_int(r[0]):
                continue
            out.append(
                {
                    "season_group_id": SWEHOCKEY_SEASON_GROUP_ID,
                    "team_name": _clean(r[1]),
                    "rank": _safe_int(r[0]),
                    "games_played": _safe_int(r[2]),
                    "wins": _safe_int(r[3]),
                    "ot_wins": _safe_int(r[4]),
                    "ot_losses": _safe_int(r[5]),
                    "losses": _safe_int(r[6]),
                    "goal_diff": _safe_int(r[7]),
                    "points": _safe_int(r[8]),
                }
            )
        if out:
            filtered = [row for row in out if _contains_team_token([row.get("team_name", "")])]
            if filtered:
                return filtered, url
            logging.warning("Standings: no team-token matches for team_id=%s, returning league rows", SWEHOCKEY_TEAM_ID)
            return out, url
    return [], None


def _fetch_schedule() -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/Teams/Info/Schedule/{SWEHOCKEY_TEAM_ID}",
        f"{BASE_URL}/ScheduleAndResults/Schedule/{SWEHOCKEY_SEASON_GROUP_ID}",
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
            if len(r) < 4:
                continue
            home_team = _clean(r[1] if len(r) > 1 else "")
            away_team = _clean(r[2] if len(r) > 2 else "")
            if not home_team or not away_team:
                continue
            out.append(
                {
                    "season_group_id": SWEHOCKEY_SEASON_GROUP_ID,
                    "team_id": SWEHOCKEY_TEAM_ID,
                    "match_date": _clean(r[0]),
                    "home_team": home_team,
                    "away_team": away_team,
                    "result": _clean(r[3] if len(r) > 3 else ""),
                    "status": _clean(r[4] if len(r) > 4 else ""),
                }
            )
        if out:
            filtered = [row for row in out if _contains_team_token([row.get("home_team", ""), row.get("away_team", "")])]
            if filtered:
                return filtered, url
            logging.warning("Schedule: no team-token matches for team_id=%s, returning league rows", SWEHOCKEY_TEAM_ID)
            return out, url
    return [], None


def _upload_raw_json(payload: dict[str, Any], data_type: str):
    blob_name = f"raw/web_scrapers/shl_stats/{_timestamp_str()}_{data_type}.json"
    storage_client = storage.Client(project=GCP_PROJECT)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")
    logging.info("Uploaded raw JSON: gs://%s/%s", GCS_BUCKET, blob_name)


def _ensure_dataset(client: bigquery.Client, dataset_id: str):
    ds_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    ds_ref.location = "europe-west1"
    client.create_dataset(ds_ref, exists_ok=True)


def _append_bq_rows(client: bigquery.Client, table_name: str, rows: list[dict[str, Any]], scraped_at: str):
    if not rows:
        return 0
    enriched = []
    for row in rows:
        item = dict(row)
        item["scraped_at"] = scraped_at
        item["source"] = SOURCE
        enriched.append(item)

    table_id = f"{client.project}.{BQ_DATASET}.{table_name}"
    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    job = client.load_table_from_json(enriched, table_id, job_config=job_config)
    job.result()
    logging.info("Loaded %d rows into %s", len(enriched), table_id)
    return len(enriched)


@functions_framework.http
def run_swehockey_stats_scraper(request):
    scraped_at = _now().isoformat()
    bq_client = bigquery.Client(project=GCP_PROJECT)
    _ensure_dataset(bq_client, BQ_DATASET)

    result: dict[str, Any] = {"status": "ok", "scraped_at": scraped_at, "types": {}}

    jobs = [
        ("player_stats", _fetch_player_stats, "swehockey_player_stats"),
        ("goalie_stats", _fetch_goalie_stats, "swehockey_goalie_stats"),
        ("standings", _fetch_standings, "swehockey_standings"),
        ("schedule", _fetch_schedule, "swehockey_schedule"),
    ]

    for data_type, fetcher, table_name in jobs:
        try:
            rows, source_url = fetcher()
            payload = {
                "meta": {
                    "source": SOURCE,
                    "type": data_type,
                    "team_id": SWEHOCKEY_TEAM_ID,
                    "season_group_id": SWEHOCKEY_SEASON_GROUP_ID,
                    "source_url": source_url,
                    "scraped_at": scraped_at,
                },
                "rows": rows,
            }
            _upload_raw_json(payload, data_type)
            loaded = _append_bq_rows(bq_client, table_name, rows, scraped_at)
            result["types"][data_type] = {"ok": True, "rows": len(rows), "bq_loaded": loaded, "source_url": source_url}
        except Exception as e:
            logging.exception("Failed scrape type=%s", data_type)
            result["types"][data_type] = {"ok": False, "error": str(e)}

    status_code = 200 if any(v.get("ok") for v in result["types"].values()) else 500
    return json.dumps(result, ensure_ascii=False), status_code, {"Content-Type": "application/json"}
