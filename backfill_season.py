"""
Backfill HA 23/24 season data for Björklöven into BigQuery.

Steps:
1. Discover the correct season_group_id for HA 23/24 from stats.swehockey.se
2. Scrape player stats, goalie stats, standings, and schedule
3. Upload to BigQuery raw_sports tables
4. Insert season config into swehockey_seasons
"""

import json
import re
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from google.cloud import bigquery, storage

# ── Config ──
GCP_PROJECT = "granskaren-d51a1"
GCS_BUCKET = "loven-stats-raw-data-prod"
BQ_DATASET = "raw_sports"
TEAM_ID = "1139"  # Björklöven team_id on swehockey
TEAM_TOKENS = ["björklöven", "bjorkloven", "löven", "bjo", "ifb"]
SOURCE = "swehockey"
BASE_URL = "https://stats.swehockey.se"

# ── Known season IDs to try for HA 23/24 ──
# Swehockey IDs are not sequential but typically in a range.
# HA 25/26 regular = 18266, playoff = 19979
# HA 24/25 regular should be roughly 2000 lower
# We'll scan a range and look for "HockeyAllsvenskan" + "2023" or "2024"
CANDIDATE_IDS_2324 = list(range(14500, 16500))

def _clean(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()

def _safe_int(v):
    if v is None:
        return 0
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if s in ("", "-", "–"):
        return 0
    try:
        return int(float(s.replace(",", ".")))
    except Exception:
        return 0

def _safe_float(v):
    if v is None:
        return 0.0
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if s in ("", "-", "–"):
        return 0.0
    try:
        return float(s.replace(",", "."))
    except Exception:
        return 0.0

def _fetch_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        r.encoding = 'utf-8'
        return r.text
    except Exception as e:
        return None


def find_ha_season_id(target_years="2023/24"):
    """Find the season_group_id for HA 23/24 by scanning swehockey standings pages."""
    print(f"Searching for HockeyAllsvenskan {target_years} season_group_id...")
    print("This may take a while — scanning swehockey.se...\n")
    
    # Try some known likely ranges based on HA 25/26 = 18266
    # HA seasons are typically ~2000 apart in IDs
    # Try a focused approach first: known approximate ranges
    likely_ranges = [
        range(15200, 15400),  # ~3000 before 18266
        range(14800, 15200),
        range(15400, 15700),
        range(14500, 14800),
    ]
    
    for id_range in likely_ranges:
        for sid in id_range:
            url = f"{BASE_URL}/ScheduleAndResults/Standings/{sid}"
            html = _fetch_html(url)
            if not html:
                continue
            
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string if soup.title else ""
            
            # Look for page headers/breadcrumbs containing "HockeyAllsvenskan" and "2023" or "2024"
            page_text = soup.get_text(" ", strip=True)
            
            if "HockeyAllsvenskan" in page_text and ("2023/24" in page_text or "2023/2024" in page_text):
                # Verify it has actual standings data
                table = soup.select_one("table")
                if table:
                    rows = table.select("tr")
                    if len(rows) > 3:  # header + at least 3 teams
                        print(f"✅ FOUND! HA 23/24 season_group_id = {sid}")
                        print(f"   Title: {title}")
                        print(f"   URL: {url}")
                        return sid
            
            # Rate limiting: be nice to swehockey
            import time
            time.sleep(0.3)
    
    print("❌ Could not find HA 23/24 automatically.")
    return None


def scrape_player_stats(season_group_id):
    """Scrape player stats for the given season."""
    urls = [
        f"{BASE_URL}/Players/Statistics/ScoringLeaders/{season_group_id}",
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
                if len(r) < 12 or not _safe_int(r[0]):
                    continue
                first = _clean(r[0]).lower()
                if first in ("rk", "rank", "no", "name", "team", "gp", "sp", "date", "datum"):
                    continue
                team_code = _clean(r[3])
                out.append({
                    "season_group_id": season_group_id,
                    "team_id": TEAM_ID,
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
                })
            # Break after the first table to avoid duplicates from All, Forwards, Defensemen tables
            if out:
                break
        if out:
            # Filter for Björklöven
            bjk = [row for row in out if any(t in (row.get("team_code", "")).lower() for t in TEAM_TOKENS)]
            print(f"  Player stats: {len(out)} total, {len(bjk)} Björklöven")
            return out, url
    return [], None


def scrape_goalie_stats(season_group_id):
    """Scrape goalie stats for the given season."""
    url = f"{BASE_URL}/Players/Statistics/LeadingGoaliesSVS/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table")
    out = []
    for table in tables:
        for tr in table.select("tr"):
            r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
            if len(r) < 13 or not _safe_int(r[0]):
                continue
            first = _clean(r[0]).lower()
            if first in ("rk", "rank", "no", "name"):
                continue
            team_code = _clean(r[3])
            out.append({
                "season_group_id": season_group_id,
                "team_id": TEAM_ID,
                "team_code": team_code,
                "goalie_name": _clean(r[2]),
                "games_played": _safe_int(r[4]),
                "shots_against": _safe_int(r[7]),
                "saves": _safe_int(r[10]),
                "goals_against": _safe_int(r[8]),
                "save_pct": _safe_float(r[11]),
                "gaa": _safe_float(r[9]),
                "shutouts": _safe_int(r[12] if len(r) > 12 else 0),
                "wins": _safe_int(r[13] if len(r) > 13 else 0),
                "losses": _safe_int(r[14] if len(r) > 14 else 0),
                "win_pct": _safe_float(r[15] if len(r) > 15 else 0),
                "toi_minutes": 0,
            })
        if out:
            break
    if out:
        bjk = [row for row in out if any(t in (row.get("team_code", "")).lower() for t in TEAM_TOKENS)]
        print(f"  Goalie stats: {len(out)} total, {len(bjk)} Björklöven")
    return out, url


def scrape_standings(season_group_id):
    """Scrape standings for the given season."""
    url = f"{BASE_URL}/ScheduleAndResults/Standings/{season_group_id}"
    html = _fetch_html(url)
    if not html:
        return [], None
    soup = BeautifulSoup(html, "lxml")
    # Usually the first table is the overall standings. Let's just grab the first table.
    table = soup.select_one("table.table") or soup.select_one("table")
    if not table:
        return [], None
    out = []
    for tr in table.select("tr"):
        r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
        if len(r) < 9 or not _safe_int(r[0]):
            continue
        out.append({
            "season_group_id": season_group_id,
            "team_name": _clean(r[1]),
            "rank": _safe_int(r[0]),
            "games_played": _safe_int(r[2]),
            "wins": _safe_int(r[3]),
            "ot_wins": _safe_int(r[4]),
            "ot_losses": _safe_int(r[5]),
            "losses": _safe_int(r[6]),
            "goal_diff": _safe_int(r[7]),
            "points": _safe_int(r[8]),
        })
        # If we reach 14 teams, we are done with the first table
        if len(out) >= 14:
            break
    if out:
        bjk = [row for row in out if any(t in (row.get("team_name", "")).lower() for t in TEAM_TOKENS)]
        print(f"  Standings: {len(out)} teams, Björklöven rank: {bjk[0]['rank'] if bjk else 'NOT FOUND'}")
    return out, url


def scrape_schedule(season_group_id):
    """Scrape schedule/results for the given season."""
    urls = [
        f"{BASE_URL}/ScheduleAndResults/Schedule/{season_group_id}",
    ]
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.table") or soup.select_one("table")
        if not table:
            continue
        out = []
        for tr in table.select("tr"):
            r = [_clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
            if len(r) < 4:
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", _clean(r[0])):
                continue
            game_str = _clean(r[3] if len(r) > 3 else "")
            if " - " in game_str:
                home_team, away_team = game_str.split(" - ", 1)
            else:
                home_team, away_team = "", ""
            home_team = _clean(home_team)
            away_team = _clean(away_team)
            if not home_team or not away_team:
                continue
            result_str = _clean(r[4] if len(r) > 4 else "")
            out.append({
                "season_group_id": season_group_id,
                "team_id": TEAM_ID,
                "match_date": _clean(r[0]),
                "home_team": home_team,
                "away_team": away_team,
                "result": result_str,
                "status": result_str,
            })
        if out:
            bjk = [g for g in out if any(t in g.get("home_team", "").lower() or t in g.get("away_team", "").lower() for t in TEAM_TOKENS)]
            print(f"  Schedule: {len(out)} games, {len(bjk)} with Björklöven")
            return out, url
    return [], None


def upload_to_bq(client, table_name, rows, scraped_at):
    """Upload rows to BigQuery."""
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
    print(f"  ✅ Loaded {len(enriched)} rows into {table_id}")
    return len(enriched)


def upload_raw_json(payload, data_type):
    """Upload raw JSON to GCS."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"raw/web_scrapers/shl_stats/{ts}_{data_type}_backfill_ha2324.json"
    storage_client = storage.Client(project=GCP_PROJECT)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")
    print(f"  ✅ Uploaded gs://{GCS_BUCKET}/{blob_name}")


def insert_season_config(client, season_group_id, playoff_id=None):
    """Insert HA 23/24 season into swehockey_seasons."""
    sql = f"""
    MERGE `{client.project}.raw_sports.swehockey_seasons` T
    USING (
        SELECT 
            'ha_2324' as season_key,
            'HockeyAllsvenskan 2023/24' as season_name,
            'HA' as league,
            {season_group_id} as regular_season_id,
            {playoff_id or 'NULL'} as playoff_id,
            DATE('2023-09-15') as start_date,
            DATE('2024-03-10') as end_date,
            FALSE as is_active
    ) S
    ON T.season_key = S.season_key
    WHEN NOT MATCHED THEN
        INSERT (season_key, season_name, league, regular_season_id, playoff_id, start_date, end_date, is_active)
        VALUES (S.season_key, S.season_name, S.league, S.regular_season_id, S.playoff_id, S.start_date, S.end_date, S.is_active);
    """
    client.query(sql).result()
    print("✅ Season config inserted: ha_2324")


def main():
    print("=" * 60)
    print("  BACKFILL: HockeyAllsvenskan 2023/24 — Björklöven")
    print("=" * 60)
    print()
    
    # Step 1: Find season_group_id
    season_id = find_ha_season_id("2023/24")
    if not season_id:
        # Try manual known IDs — these are common for HA around that era
        print("\nTrying known IDs from web search...")
        for test_id in [15293, 15294, 15295, 15296, 15297, 15298, 15299, 15300]:
            url = f"{BASE_URL}/ScheduleAndResults/Standings/{test_id}"
            html = _fetch_html(url)
            if html and "björklöven" in html.lower():
                soup = BeautifulSoup(html, "lxml")
                if "2023" in soup.get_text():
                    season_id = test_id
                    print(f"  Found via manual scan: {test_id}")
                    break
    
    if not season_id:
        print("\n❌ Could not find HA 23/24 season_group_id.")
        print("You can try running with a known ID:")
        print("  python backfill_season.py <season_group_id>")
        sys.exit(1)
    
    print(f"\n📋 Using season_group_id: {season_id}")
    print("-" * 40)
    
    # Step 2: Scrape all data types
    scraped_at = datetime.now(timezone.utc).isoformat()
    bq_client = bigquery.Client(project=GCP_PROJECT)
    
    print("\n📊 Scraping player stats...")
    players, p_url = scrape_player_stats(season_id)
    
    print("\n🧤 Scraping goalie stats...")
    goalies, g_url = scrape_goalie_stats(season_id)
    
    print("\n📈 Scraping standings...")
    standings, s_url = scrape_standings(season_id)
    
    print("\n📅 Scraping schedule...")
    schedule, sch_url = scrape_schedule(season_id)
    
    if not any([players, goalies, standings, schedule]):
        print("\n❌ No data scraped. The season_group_id might be wrong.")
        sys.exit(1)
    
    # Step 3: Upload raw JSON to GCS
    print("\n☁️  Uploading raw JSON to GCS...")
    for dtype, rows, url in [
        ("player_stats", players, p_url),
        ("goalie_stats", goalies, g_url),
        ("standings", standings, s_url),
        ("schedule", schedule, sch_url),
    ]:
        if rows:
            upload_raw_json({
                "meta": {
                    "source": SOURCE,
                    "type": dtype,
                    "team_id": TEAM_ID,
                    "season_group_id": season_id,
                    "source_url": url,
                    "scraped_at": scraped_at,
                    "backfill": True,
                    "season": "ha_2324",
                },
                "rows": rows,
            }, dtype)
    
    # Step 4: Upload to BigQuery
    print("\n🗄️  Loading into BigQuery...")
    for table_name, rows in [
        ("swehockey_player_stats", players),
        ("swehockey_goalie_stats", goalies),
        ("swehockey_standings", standings),
        ("swehockey_schedule", schedule),
    ]:
        upload_to_bq(bq_client, table_name, rows, scraped_at)
    
    # Step 5: Insert season config
    print("\n⚙️  Inserting season config...")
    insert_season_config(bq_client, season_id)
    
    # Summary
    print("\n" + "=" * 60)
    print("  ✅ BACKFILL COMPLETE")
    print("=" * 60)
    print(f"  Season:     HockeyAllsvenskan 2023/24")
    print(f"  Season ID:  {season_id}")
    print(f"  Players:    {len(players)} rows")
    print(f"  Goalies:    {len(goalies)} rows")
    print(f"  Standings:  {len(standings)} rows")
    print(f"  Schedule:   {len(schedule)} rows")
    print()
    print("  Next steps:")
    print("  1. Test: curl https://loven-stats-api-.../api/v1/seasons")
    print("  2. Test: curl https://loven-stats-api-.../api/v1/statistics?season=ha_2324")
    print("  3. Verify in frontend Statistics tab")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Allow passing season_group_id directly
        manual_id = int(sys.argv[1])
        print(f"Using manually provided season_group_id: {manual_id}")
        
        scraped_at = datetime.now(timezone.utc).isoformat()
        bq_client = bigquery.Client(project=GCP_PROJECT)
        
        print("\n📊 Scraping player stats...")
        players, p_url = scrape_player_stats(manual_id)
        print("\n🧤 Scraping goalie stats...")
        goalies, g_url = scrape_goalie_stats(manual_id)
        print("\n📈 Scraping standings...")
        standings, s_url = scrape_standings(manual_id)
        print("\n📅 Scraping schedule...")
        schedule, sch_url = scrape_schedule(manual_id)
        
        if any([players, goalies, standings, schedule]):
            print("\n☁️  Uploading raw JSON to GCS...")
            for dtype, rows, url in [
                ("player_stats", players, p_url),
                ("goalie_stats", goalies, g_url),
                ("standings", standings, s_url),
                ("schedule", schedule, sch_url),
            ]:
                if rows:
                    upload_raw_json({
                        "meta": {"source": SOURCE, "type": dtype, "team_id": TEAM_ID,
                                 "season_group_id": manual_id, "source_url": url,
                                 "scraped_at": scraped_at, "backfill": True, "season": "ha_2324"},
                        "rows": rows,
                    }, dtype)
            
            print("\n🗄️  Loading into BigQuery...")
            for table_name, rows in [
                ("swehockey_player_stats", players),
                ("swehockey_goalie_stats", goalies),
                ("swehockey_standings", standings),
                ("swehockey_schedule", schedule),
            ]:
                upload_to_bq(bq_client, table_name, rows, scraped_at)
            
            print("\n⚙️  Inserting season config...")
            insert_season_config(bq_client, manual_id)
            
            print(f"\n✅ DONE! Players={len(players)}, Goalies={len(goalies)}, Standings={len(standings)}, Schedule={len(schedule)}")
        else:
            print("❌ No data found for this season_group_id")
    else:
        main()
