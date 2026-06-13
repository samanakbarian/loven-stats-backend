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
        rows = table.find_all("tr", recursive=False)
        if not rows and table.find("tbody"):
            rows = table.find("tbody").find_all("tr", recursive=False)
        if not rows: continue
        first_row = [_clean(c.get_text(" ", strip=True)) for c in rows[0].select("th,td")]
        
        # Keep track of team from [Top] row
        if len(first_row) > 0 and "Top" in first_row[-1]:
            current_team = first_row[0]
            
        # Is this the Goalkeeping Statistics table?
        if len(first_row) > 0 and "Goalkeeping Statistics" in first_row[0]:
            if len(rows) > 2:
                for tr in rows[2:]:
                    r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
                    if len(r) < 13 or _is_header_row(r) or not _safe_int(r[0]):
                        continue
                    out.append(
                        {
                            "season_group_id": int(season_group_id),
                            "team_id": SWEHOCKEY_TEAM_ID,
                            "team_code": current_team,
                            "goalie_name": _clean(r[2]),
                            "games_played": _safe_int(r[5]),
                            "shots_against": _safe_int(r[9]),
                            "saves": _safe_int(r[8]),
                            "goals_against": _safe_int(r[7]),
                            "save_pct": _safe_float(r[10]),
                            "gaa": _safe_float(r[11]),
                            "toi_minutes": 0,
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


def _fetch_schedule(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    urls = [
        f"{BASE_URL}/Teams/Info/Schedule/{SWEHOCKEY_TEAM_ID}",
        f"{BASE_URL}/ScheduleAndResults/Schedule/{season_group_id}",
    ]
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        rows = _extract_table_rows(html)
        if len(rows) < 2:
            continue
        out = []
        current_date = ""
        for r in rows:
            if len(r) < 3:
                continue
                
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[0]))
            if date_match:
                current_date = date_match.group(0)
            elif re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[1] if len(r) > 1 else "")):
                current_date = re.search(r"\d{4}-\d{2}-\d{2}", _clean(r[1])).group(0)

            game_str = ""
            result_str = ""
            for i, col in enumerate(r):
                c = _clean(col)
                if " - " in c and len(c) > 7:
                    if re.search(r"[a-zA-ZÅÄÖåäö]", c):
                        game_str = c
                        if i + 1 < len(r):
                            result_str = _clean(r[i+1])
                        break
            
            if not game_str or " - " not in game_str:
                continue
                
            home_team, away_team = game_str.split(" - ", 1)
            home_team = _clean(home_team)
            away_team = _clean(away_team)
            if not home_team or not away_team or len(home_team) > 100 or len(away_team) > 100:
                continue

            out.append(
                {
                    "season_group_id": int(season_group_id),
                    "team_id": SWEHOCKEY_TEAM_ID,
                    "match_date": current_date,
                    "home_team": home_team,
                    "away_team": away_team,
                    "result": result_str,
                    "status": result_str,
                }
            )
        if out:
            # Deduplicate by match_date, home_team, away_team
            unique_out = {f"{r['match_date']}_{r['home_team']}_{r['away_team']}": r for r in out}
            return list(unique_out.values()), url
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

    # Fetch active season IDs from BigQuery
    active_season_ids = []
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
    
    # Deduplicate active season IDs
    active_season_ids = list(set(active_season_ids))

    result: dict[str, Any] = {"status": "ok", "scraped_at": scraped_at, "types": {}}

    for season_group_id in active_season_ids:
        jobs = [
            ("player_stats", _fetch_player_stats, "swehockey_player_stats"),
            ("goalie_stats", _fetch_goalie_stats, "swehockey_goalie_stats"),
            ("standings", _fetch_standings, "swehockey_standings"),
            ("schedule", _fetch_schedule, "swehockey_schedule"),
        ]

        for data_type, fetcher, table_name in jobs:
            try:
                rows, source_url = fetcher(season_group_id)
                payload = {
                    "meta": {
                        "source": SOURCE,
                        "type": data_type,
                        "team_id": SWEHOCKEY_TEAM_ID,
                        "season_group_id": int(season_group_id),
                        "source_url": source_url,
                        "scraped_at": scraped_at,
                    },
                    "rows": rows,
                }
                
                # Use data_type + season_group_id to avoid overwriting different seasons in raw upload
                gcs_key = f"{data_type}_{season_group_id}"
                _upload_raw_json(payload, gcs_key)
                
                loaded = _append_bq_rows(bq_client, table_name, rows, scraped_at)
                
                if data_type not in result["types"]:
                    result["types"][data_type] = {"ok": True, "rows": 0, "bq_loaded": 0, "source_urls": []}
                    
                result["types"][data_type]["rows"] += len(rows)
                result["types"][data_type]["bq_loaded"] += loaded
                if source_url:
                    result["types"][data_type]["source_urls"].append(source_url)
                    
            except Exception as e:
                logging.exception("Failed scrape type=%s season=%s", data_type, season_group_id)
                if data_type not in result["types"]:
                    result["types"][data_type] = {"ok": False, "error": str(e)}

    status_code = 200 if any(v.get("ok") for v in result["types"].values()) else 500
    return json.dumps(result, ensure_ascii=False), status_code, {"Content-Type": "application/json"}
