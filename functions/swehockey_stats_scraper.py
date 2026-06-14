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


def _fetch_schedule(season_group_id: str) -> tuple[list[dict[str, Any]], str | None]:
    urls = [
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
    ensure_lineage_columns(client, table_id)
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
    
    active_season_ids = sorted(set(active_season_ids), key=int)
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
                rows, source_url = job["fetcher"](season_group_id)
                fetched_rows += len(rows)
                fetched_batches.append(
                    {
                        **job,
                        "season_group_id": season_group_id,
                        "rows": rows,
                        "source_url": source_url,
                    }
                )
                type_result = result["types"].setdefault(
                    data_type,
                    {"ok": True, "rows": 0, "bq_loaded": 0, "source_urls": []},
                )
                type_result["rows"] += len(rows)
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
            allow_preseason_empty = (
                data_type in {"player_stats", "goalie_stats"}
                and not season_has_games.get(season_group_id, False)
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
            loaded_rows += loaded
            result["types"][data_type]["bq_loaded"] += loaded

        result["status"] = "ok"
        run_logger.finish_run(
            run_id=run_id,
            status="SUCCESS",
            fetched_rows=fetched_rows,
            loaded_rows=loaded_rows,
            failed_steps=0,
            metadata={"types": result["types"]},
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
