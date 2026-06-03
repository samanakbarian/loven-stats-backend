import os
import json
import logging
import requests
import unicodedata
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.cloud import storage
from google.cloud import bigquery
from datetime import datetime, timezone, timedelta
from cachetools import cached, TTLCache

import re
from silly_season_data import SILLY_SEASON_BASELINE

app = FastAPI(
    title="LÃ¶ven Stats Hub API",
    description="Backend API for LÃ¶ven Stats Hub, serving data from BigQuery & GCS",
    version="1.0.0"
)

# TillÃ¥t CORS fÃ¶r frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Byt till Netlify-domÃ¤nen i produktion
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
BQ_PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "loven_marts")
BQ_LOVENLAGET_TABLE = os.environ.get("BQ_LOVENLAGET_TABLE", "mart_lovenlaget_snapshot")
BQ_FINANCIALS_TABLE = os.environ.get("BQ_FINANCIALS_TABLE", "serving_team_economy_dashboard")
BQ_FINANCIALS_RAW_DATASET = os.environ.get("BQ_FINANCIALS_RAW_DATASET", "raw_content")
BQ_FINANCIALS_RAW_TABLE = os.environ.get("BQ_FINANCIALS_RAW_TABLE", "bjorkloven_financials_raw")
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_QUERY_DEFAULT = os.environ.get(
    "X_QUERY_DEFAULT",
    '(BjÃ¶rklÃ¶ven OR Bjorkloven OR #BjÃ¶rklÃ¶ven OR #Bjorkloven) -is:retweet -is:reply lang:sv'
)
X_MAX_RESULTS_DEFAULT = int(os.environ.get("X_MAX_RESULTS_DEFAULT", "40"))
X_QUERY_BROAD_DEFAULT = os.environ.get(
    "X_QUERY_BROAD_DEFAULT",
    '((BjÃ¶rklÃ¶ven OR Bjorkloven OR #BjÃ¶rklÃ¶ven OR #Bjorkloven OR LÃ¶ven OR #LÃ¶ven) (hockey OR SHL OR allsvenskan OR nyfÃ¶rvÃ¤rv OR fÃ¶rlÃ¤nger OR lÃ¤mnar OR silly)) -is:retweet -is:reply lang:sv'
)
X_QUERY_OFFICIAL_DEFAULT = os.environ.get(
    "X_QUERY_OFFICIAL_DEFAULT",
    '(from:Bjorkloven OR from:IFBjorkloven) -is:retweet -is:reply'
)
X_CACHE_BLOB = os.environ.get("X_CACHE_BLOB", "derived/x_feed/latest.json")
X_CACHE_MINUTES = int(os.environ.get("X_CACHE_MINUTES", "60"))
X_AI_ENABLED = os.environ.get("X_AI_ENABLED", "false").lower() == "true"
X_AI_MODEL = os.environ.get("X_AI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
X_BQ_DATASET = os.environ.get("X_BQ_DATASET", "raw_content")
X_BQ_POSTS_TABLE = os.environ.get("X_BQ_POSTS_TABLE", "x_posts")
X_BQ_RUNS_TABLE = os.environ.get("X_BQ_RUNS_TABLE", "x_fetch_runs")
SWEHOCKEY_TEAM_ID = os.environ.get("SWEHOCKEY_TEAM_ID", "1139")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Welcome to LÃ¶ven Stats Hub API"}

@app.get("/api/v1/health")
def health_check():
    return {"status": "healthy"}


# â”€â”€ Season lookup â”€â”€
_season_cache = {}

def lookup_season(season_key=None):
    """Lookup season config from BQ. Caches results."""
    cache_key = season_key or "__active__"
    if cache_key in _season_cache:
        return _season_cache[cache_key]
    
    bq = bigquery.Client(project=BQ_PROJECT_ID or None)
    proj = bq.project
    if season_key:
        sql = f"SELECT * FROM `{proj}.raw_sports.swehockey_seasons` WHERE season_key = @key"
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", season_key)
        ])
    else:
        sql = f"SELECT * FROM `{proj}.raw_sports.swehockey_seasons` WHERE is_active = TRUE LIMIT 1"
        job_config = None
    
    rows = list(bq.query(sql, job_config=job_config).result())
    if not rows:
        # Fallback to hardcoded
        return {"key": "ha_2526", "name": "HockeyAllsvenskan 2025/26", "regular": 18266, "playoff": 19979}
    
    r = dict(rows[0].items())
    result = {
        "key": r["season_key"],
        "name": r["season_name"],
        "regular": r["regular_season_id"],
        "playoff": r.get("playoff_id"),
    }
    _season_cache[cache_key] = result
    return result

@app.get("/api/v1/seasons")
def get_seasons():
    bq = bigquery.Client(project=BQ_PROJECT_ID or None)
    rows = [dict(r.items()) for r in bq.query(
        f"SELECT * FROM `{bq.project}.raw_sports.swehockey_seasons` ORDER BY start_date DESC"
    ).result()]
    active = next((r["season_key"] for r in rows if r.get("is_active")), None)
    return {
        "seasons": [{"key": r["season_key"], "name": r["season_name"], "is_active": r.get("is_active", False)} for r in rows],
        "active": active,
    }


@app.get("/api/v1/statistics")
def get_statistics_snapshot(season: str = None, team_query: str = Query(default="ifb,bjo,bjÃ¶rklÃ¶ven,bjorkloven,if bjÃ¶rklÃ¶ven")):
    """
    Returns Swehockey snapshot from raw_sports tables.
    Serves both league-wide stats and BjÃ¶rklÃ¶ven-specific data.
    """
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        def _normalize_for_match(s: str) -> str:
            raw = str(s or "").strip().lower()
            # Handle common mojibake variants seen in upstream HTML/DB payloads.
            raw = (
                raw.replace("bjã¶rklã¶ven", "björklöven")
                .replace("if bjã¶rklã¶ven", "if björklöven")
                .replace("lã¶ven", "löven")
            )
            normalized = unicodedata.normalize("NFKD", raw)
            ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return re.sub(r"\s+", " ", ascii_only)

        tokens = [_normalize_for_match(t) for t in str(team_query or "").split(",") if t.strip()]
        if not tokens:
            tokens = [_normalize_for_match(t) for t in ["ifb", "bjo", "björklöven", "bjorkloven", "if björklöven"]]

        def _matches(value: str) -> bool:
            v = _normalize_for_match(value)
            for token in tokens:
                if len(token) <= 3:
                    # Short tokens like IFB/BJO should still match full team strings.
                    if v == token or re.search(rf"\b{re.escape(token)}\b", v):
                        return True
                else:
                    if token in v:
                        return True
            return False

        def _query_season(table_name: str, season_ids: list[int]):
            """Return rows from the table filtered by season_group_id."""
            if not season_ids:
                return []
            ids_str = ",".join(str(sid) for sid in season_ids if sid)
            q = f"""
            SELECT * FROM `{bq_client.project}.raw_sports.{table_name}`
            WHERE season_group_id IN ({ids_str}) -- cache bust 1
            """
            return [dict(row.items()) for row in bq_client.query(q).result()]

        # Lookup season
        active = lookup_season(season)
        HA_REGULAR = active["regular"]
        HA_PLAYOFF = active.get("playoff")
        season_ids = list(set([sid for sid in [HA_REGULAR, HA_PLAYOFF] if sid]))

        all_players = _query_season("swehockey_player_stats", season_ids)
        all_goalies = _query_season("swehockey_goalie_stats", season_ids)
        standings = _query_season("swehockey_standings", season_ids)
        schedule = _query_season("swehockey_schedule", season_ids)

        # Split players by season type
        regular_players = [p for p in all_players if p.get("season_group_id") == HA_REGULAR]
        playoff_players = [p for p in all_players if p.get("season_group_id") == HA_PLAYOFF] if HA_PLAYOFF else []
        regular_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_REGULAR]
        playoff_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_PLAYOFF] if HA_PLAYOFF else []

        # BJK-specific data
        bjk_skaters_regular = sorted(
            [p for p in regular_players if _matches(str(p.get("team_code", "")))],
            key=lambda p: (int(p.get("points") or 0), int(p.get("goals") or 0)),
            reverse=True
        )
        bjk_skaters_playoff = sorted(
            [p for p in playoff_players if _matches(str(p.get("team_code", "")))],
            key=lambda p: (int(p.get("points") or 0), int(p.get("goals") or 0)),
            reverse=True
        )
        bjk_goalies_regular = sorted(
            [g for g in regular_goalies if _matches(str(g.get("team_code", "")))],
            key=lambda g: int(g.get("games_played") or 0),
            reverse=True
        )

        # League-wide top scorers (regular season)
        top_scorers = sorted(
            regular_players,
            key=lambda p: (int(p.get("points") or 0), int(p.get("goals") or 0)),
            reverse=True
        )[:25]
        top_goalies = sorted(
            regular_goalies,
            key=lambda g: int(g.get("games_played") or 0),
            reverse=True
        )[:15]

        # Team standing
        team_standing = next((s for s in standings if _matches(str(s.get("team_name", "")))), None)

        # Team games â€” use team_id when present; fallback to robust name matching
        team_games = sorted(
            [
                m for m in schedule
                if _matches(str(m.get("home_team", "")))
                or _matches(str(m.get("away_team", "")))
            ],
            key=lambda g: str(g.get("date", "") or g.get("match_date", "")),
            reverse=True,
        )

        # Compute record from team_standing or from games
        record = {}
        if team_standing:
            record = {
                "gp": team_standing.get("games_played", 0),
                "wins": team_standing.get("wins", 0),
                "losses": team_standing.get("losses", 0),
                "otl": team_standing.get("ot_losses", 0),
                "otw": team_standing.get("ot_wins", 0),
                "points": team_standing.get("points", 0),
                "gf": 0, "ga": 0,
            }
        elif team_games:
            wins = losses = otl = gf = ga = 0
            for g in team_games:
                result = str(g.get("result", "") or "")
                m = re.search(r"(\d+)\s*-\s*(\d+)", result)
                if not m:
                    continue
                hg, ag = int(m.group(1)), int(m.group(2))
                home = str(g.get("home_team", "") or "")
                is_home = _matches(home)
                bjk_gf = hg if is_home else ag
                bjk_ga = ag if is_home else hg
                gf += bjk_gf
                ga += bjk_ga
                if bjk_gf > bjk_ga:
                    wins += 1
                elif bjk_gf < bjk_ga:
                    has_ot = any(x in str(g.get("status", "") or "").upper() for x in ["OT", "ÖT", "SO", "GWS"])
                    if has_ot:
                        otl += 1
                    else:
                        losses += 1
            gp = wins + losses + otl
            record = {"gp": gp, "wins": wins, "losses": losses, "otl": otl, "otw": 0, "points": wins * 3 + otl, "gf": gf, "ga": ga}

        latest_times = []
        for rows in (all_players, all_goalies, standings, schedule):
            for row in rows:
                sa = row.get("scraped_at")
                if sa:
                    latest_times.append(str(sa))

        return {
            "status": "ok",
            "source": "swehockey",
            "season": active["name"],
            "scope": "team",
            "team_query_tokens": tokens,
            "snapshot_scraped_at": max(latest_times) if latest_times else None,
            "counts": {
                "players_total": len(all_players),
                "goalies_total": len(all_goalies),
                "standings_total": len(standings),
                "schedule_total": len(schedule),
                "team_players_regular": len(bjk_skaters_regular),
                "team_players_playoff": len(bjk_skaters_playoff),
                "team_goalies": len(bjk_goalies_regular),
                "team_games": len(team_games),
            },
            "record": record,
            "team_standing": team_standing,
            "top_scorers": top_scorers,
            "top_goalies": top_goalies,
            "bjorkloven_skaters": {
                "regular": bjk_skaters_regular,
                "playoff": bjk_skaters_playoff,
            },
            "bjorkloven_goalies": {
                "regular": bjk_goalies_regular,
            },
            "games": team_games,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/statistics")
        return {
            "status": "error",
            "error": str(e),
        }


analytics_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching

@app.get("/api/v1/analytics")
@cached(cache=analytics_cache)
def get_analytics(season: str = None):
    """
    Compute derived analytics from existing BQ data.
    Returns 8 analysis modules for the frontend.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        proj = bq.project

        # â”€â”€ Load all source data â”€â”€
        def q(sql):
            return [dict(r.items()) for r in bq.query(sql).result()]

        active = lookup_season(season)
        REGULAR_ID = active["regular"]

        schedule = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_schedule` WHERE season_group_id = {REGULAR_ID} ORDER BY match_date")
        players = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = {REGULAR_ID}")
        goalies = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = {REGULAR_ID}")
        standings = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_standings` WHERE season_group_id = {REGULAR_ID}")
        
        # Only query events for games in the current regular season schedule to avoid loading other leagues' events
        sched_game_ids = [str(g['game_id']) for g in schedule if g.get("game_id")]
        if sched_game_ids:
            game_ids_str = ", ".join(sched_game_ids)
            events = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_game_events` WHERE game_id IN ({game_ids_str})")
        else:
            events = []



        BJK_NAMES = ["IF Björklöven", "Björklöven", "IF Bjorkloven", "Bjorkloven"]
        BJK_CODES = ["IFB"]

        def _norm_name(s: str) -> str:
            raw = str(s or "").strip().lower()
            raw = raw.replace("bjã¶rklã¶ven", "björklöven").replace("lã¶ven", "löven")
            n = unicodedata.normalize("NFKD", raw)
            return "".join(ch for ch in n if not unicodedata.combining(ch))

        def is_bjk(name):
            value = _norm_name(name)
            return any(_norm_name(b) in value for b in BJK_NAMES + BJK_CODES)

        def bjk_game(g):
            return (
                is_bjk(g.get("home_team", ""))
                or is_bjk(g.get("away_team", ""))
            )

        def parse_period_results(pr):
            """Parse '(2-1, 0-1, 1-2)' into [{period, home_gf, away_gf}]"""
            if not pr:
                return []
            pr = pr.strip("() ")
            periods = []
            for i, part in enumerate(pr.split(","), 1):
                m = re.match(r'\s*(\d+)\s*-\s*(\d+)', part.strip())
                if m:
                    periods.append({"period": i, "home_gf": int(m.group(1)), "away_gf": int(m.group(2))})
            return periods

        bjk_games = [g for g in schedule if bjk_game(g)]

        # â”€â”€ Module 1: Season Timeline â”€â”€
        timeline = []
        cum_pts = 0
        for g in bjk_games:
            result_str = (g.get("result") or "").strip()
            m = re.match(r'(\d+)\s*-\s*(\d+)', result_str)
            if not m:
                continue
            hg, ag = int(m.group(1)), int(m.group(2))
            bjk_home = is_bjk(g.get("home_team", ""))
            bjk_gf = hg if bjk_home else ag
            bjk_ga = ag if bjk_home else hg
            pr = g.get("period_results", "")
            is_ot = len(parse_period_results(pr)) > 3

            if bjk_gf > bjk_ga:
                pts = 2 if is_ot else 3
                res = "W"
            elif bjk_gf < bjk_ga:
                pts = 1 if is_ot else 0
                res = "OTL" if is_ot else "L"
            else:
                pts = 0
                res = "D"

            cum_pts += pts
            opp = g.get("away_team") if bjk_home else g.get("home_team")
            timeline.append({
                "date": g.get("match_date", ""),
                "opponent": opp,
                "result": res,
                "score": f"{bjk_gf}-{bjk_ga}",
                "pts": pts,
                "cumPts": cum_pts,
                "isHome": bjk_home,
                "gf": bjk_gf,
                "ga": bjk_ga,
            })

        # â”€â”€ Module 2: Home vs Away â”€â”€
        def empty_split():
            return {"gp": 0, "w": 0, "l": 0, "otw": 0, "otl": 0, "gf": 0, "ga": 0, "pts": 0}

        splits = {"home": empty_split(), "away": empty_split()}
        for t in timeline:
            side = "home" if t["isHome"] else "away"
            s = splits[side]
            s["gp"] += 1
            s["gf"] += t["gf"]
            s["ga"] += t["ga"]
            s["pts"] += t["pts"]
            if t["result"] == "W":
                s["w"] += 1
            elif t["result"] == "L":
                s["l"] += 1
            elif t["result"] == "OTL":
                s["otl"] += 1

        # â”€â”€ Module 3: Period Analysis â”€â”€
        period_stats = {1: {"gf": 0, "ga": 0, "games": 0}, 2: {"gf": 0, "ga": 0, "games": 0}, 3: {"gf": 0, "ga": 0, "games": 0}}
        for g in bjk_games:
            pr = parse_period_results(g.get("period_results", ""))
            bjk_home = is_bjk(g.get("home_team", ""))
            for pd in pr:
                p = pd["period"]
                if p > 3:
                    continue  # skip OT/SO
                if p not in period_stats:
                    continue
                period_stats[p]["games"] += 1
                if bjk_home:
                    period_stats[p]["gf"] += pd["home_gf"]
                    period_stats[p]["ga"] += pd["away_gf"]
                else:
                    period_stats[p]["gf"] += pd["away_gf"]
                    period_stats[p]["ga"] += pd["home_gf"]

        periods = []
        for p in [1, 2, 3]:
            ps = period_stats[p]
            periods.append({
                "period": p,
                "label": f"P{p}",
                "gf": ps["gf"],
                "ga": ps["ga"],
                "diff": ps["gf"] - ps["ga"],
                "games": ps["games"],
            })

        # â”€â”€ Module 4: Head-to-Head â”€â”€
        h2h = {}
        for t in timeline:
            opp = t["opponent"]
            if opp not in h2h:
                h2h[opp] = {"opponent": opp, "gp": 0, "w": 0, "l": 0, "otl": 0, "gf": 0, "ga": 0, "pts": 0}
            h = h2h[opp]
            h["gp"] += 1
            h["gf"] += t["gf"]
            h["ga"] += t["ga"]
            h["pts"] += t["pts"]
            if t["result"] == "W":
                h["w"] += 1
            elif t["result"] == "L":
                h["l"] += 1
            elif t["result"] == "OTL":
                h["otl"] += 1

        h2h_list = sorted(h2h.values(), key=lambda x: (-x["pts"], -(x["gf"] - x["ga"])))

        # â”€â”€ Module 5: Form Curve (Rolling 10) â”€â”€
        form = []
        window = 10
        for i in range(len(timeline)):
            start = max(0, i - window + 1)
            w = timeline[start:i + 1]
            wins = sum(1 for x in w if x["result"] == "W")
            losses = sum(1 for x in w if x["result"] == "L")
            otl = sum(1 for x in w if x["result"] == "OTL")
            gf = sum(x["gf"] for x in w)
            ga = sum(x["ga"] for x in w)
            pts = sum(x["pts"] for x in w)
            form.append({
                "date": timeline[i]["date"],
                "matchNum": i + 1,
                "w": wins,
                "l": losses,
                "otl": otl,
                "pts": pts,
                "gf_avg": round(gf / len(w), 2),
                "ga_avg": round(ga / len(w), 2),
                "window": len(w),
            })

        # â”€â”€ Module 6: Streak Analysis â”€â”€
        streaks = []
        current = {"type": "", "length": 0, "start": "", "end": ""}
        for t in timeline:
            r = t["result"]
            streak_type = "W" if r == "W" else "L"
            if streak_type == current["type"]:
                current["length"] += 1
                current["end"] = t["date"]
            else:
                if current["length"] > 0:
                    streaks.append(dict(current))
                current = {"type": streak_type, "length": 1, "start": t["date"], "end": t["date"]}
        if current["length"] > 0:
            streaks.append(dict(current))

        win_streaks = [s for s in streaks if s["type"] == "W"]
        loss_streaks = [s for s in streaks if s["type"] == "L"]
        longest_win = max(win_streaks, key=lambda s: s["length"]) if win_streaks else None
        longest_loss = max(loss_streaks, key=lambda s: s["length"]) if loss_streaks else None

        # â”€â”€ Module 7: Player Impact â”€â”€
        # Build the dynamic list of roster names (skaters and goalies)
        roster_names = []
        roster_skaters = []
        roster_goalies = []
        for r_p in SILLY_SEASON_BASELINE.get("roster", []) + SILLY_SEASON_BASELINE.get("confirmed_departures", []):
            name = r_p.get("name")
            if not name:
                continue
            if name not in roster_names:
                roster_names.append(name)
            
            pos = r_p.get("pos", "")
            if pos == "GK":
                if name not in roster_goalies: roster_goalies.append(name)
            else:
                if name not in roster_skaters: roster_skaters.append(name)

        def clean_name(name):
            if not name: return ""
            # Strip event annotation tokens, but only as whole words.
            # Important: avoid splitting inside real surnames like "Possler".
            name = re.split(r'\b(Pos|Abuse|Diving|Charging|Illegal|Unsportsmanlike|Kneeing)\b', name)[0]
            name = name.strip()
            return name

        def name_tokens(name):
            if not name: return set()
            s = name.lower()
            s = s.replace("Ã¶", "o").replace("Ã¤", "a").replace("Ã¥", "a")
            s = s.replace("\ufffd", "")
            s = s.replace(",", " ").replace("-", " ").replace("'", " ")
            return {t for t in s.split() if len(t) > 1}

        def match_player(raw_name):
            cname = clean_name(raw_name)
            tokens = name_tokens(cname)
            if not tokens: return None
            for r in roster_names:
                rtokens = name_tokens(r)
                common = tokens.intersection(rtokens)
                if len(common) >= min(len(tokens), len(rtokens)) or len(common) >= 2:
                    return r
            return None

        def _to_int(v):
            try:
                if v is None:
                    return 0
                return int(float(v))
            except Exception:
                return 0

        # Count goals and assists from events
        event_stats = {}
        for e in events:
            tc = (e.get("team_code") or "").upper()
            if tc != "IFB":
                continue
            etype = e.get("event_type")
            if etype == "goal":
                scorer = match_player(e.get("player_name"))
                if scorer:
                    if scorer not in event_stats: event_stats[scorer] = {"goals": 0, "assists": 0}
                    event_stats[scorer]["goals"] += 1
                a1 = match_player(e.get("assist1_name"))
                if a1:
                    if a1 not in event_stats: event_stats[a1] = {"goals": 0, "assists": 0}
                    event_stats[a1]["assists"] += 1
                a2 = match_player(e.get("assist2_name"))
                if a2:
                    if a2 not in event_stats: event_stats[a2] = {"goals": 0, "assists": 0}
                    event_stats[a2]["assists"] += 1

        all_gp = [p for p in players if (p.get("games_played") or 0) >= 10]

        # League averages
        if all_gp:
            avg_ppg = sum(p.get("points", 0) for p in all_gp) / sum(p.get("games_played", 1) for p in all_gp)
            avg_gpg = sum(p.get("goals", 0) for p in all_gp) / sum(p.get("games_played", 1) for p in all_gp)
            avg_apg = sum(p.get("assists", 0) for p in all_gp) / sum(p.get("games_played", 1) for p in all_gp)
            avg_pim = sum(p.get("pim", 0) for p in all_gp) / sum(p.get("games_played", 1) for p in all_gp)
        else:
            avg_ppg = avg_gpg = avg_apg = avg_pim = 0

        player_impact = []
        for name in roster_skaters:
            candidates = []
            for p in players:
                if not (is_bjk(p.get("team_code", "")) or is_bjk(p.get("team_name", ""))):
                    continue
                if match_player(p.get("player_name")) == name:
                    candidates.append(p)

            # Choose strongest/most plausible row instead of first match.
            # This prevents stale/partial zero-rows from overriding valid stats.
            bq_p = None
            if candidates:
                candidates.sort(
                    key=lambda p: (
                        _to_int(p.get("points")),
                        _to_int(p.get("goals")),
                        _to_int(p.get("assists")),
                        _to_int(p.get("games_played")),
                        str(p.get("scraped_at") or ""),
                    ),
                    reverse=True,
                )
                bq_p = candidates[0]
            
            gp = bq_p.get("games_played") if bq_p else len(bjk_games) or 52
            goals = bq_p.get("goals") if bq_p else event_stats.get(name, {}).get("goals", 0)
            assists = bq_p.get("assists") if bq_p else event_stats.get(name, {}).get("assists", 0)
            points = goals + assists

            # Guardrail: if a high-GP player has zero in selected BQ row but event feed has signal,
            # trust event-derived totals instead of a likely bad scrape row.
            if bq_p and _to_int(gp) >= 20 and _to_int(points) == 0:
                e_goals = _to_int(event_stats.get(name, {}).get("goals", 0))
                e_assists = _to_int(event_stats.get(name, {}).get("assists", 0))
                if (e_goals + e_assists) > 0:
                    goals = e_goals
                    assists = e_assists
                    points = goals + assists
            
            g_pg = round(goals / gp, 3) if gp > 0 else 0
            a_pg = round(assists / gp, 3) if gp > 0 else 0
            p_pg = round(points / gp, 3) if gp > 0 else 0
            
            position = "F"
            for r_p in SILLY_SEASON_BASELINE.get("roster", []) + SILLY_SEASON_BASELINE.get("confirmed_departures", []):
                if r_p.get("name") == name:
                    position = r_p.get("pos", "F")
                    break
            
            pim = bq_p.get("pim", 0) if bq_p else 0
            pim_pg = round(pim / gp, 3) if gp > 0 else 0
            
            player_impact.append({
                "name": name,
                "position": position,
                "number": 0,
                "gp": gp,
                "goals": goals,
                "assists": assists,
                "points": points,
                "g_per_gp": g_pg,
                "a_per_gp": a_pg,
                "p_per_gp": p_pg,
                "pim_per_gp": pim_pg,
                "plus_minus": str(bq_p.get("plus_minus", "0") if bq_p else "0"),
                "vs_league": {
                    "ppg_diff": round(p_pg - avg_ppg, 3),
                    "gpg_diff": round(g_pg - avg_gpg, 3),
                },
            })
        player_impact.sort(key=lambda x: -x["p_per_gp"])

        # â”€â”€ Module 8: Goalie Radar â”€â”€
        bjk_goalies = [g for g in goalies if is_bjk(g.get("team_code", "")) or is_bjk(g.get("team_name", ""))]
        all_goalies_min10 = sorted([g for g in goalies if (g.get("games_played") or 0) >= 10],
                                    key=lambda g: -(g.get("save_pct") or 0))

        def percentile(value, all_vals):
            if not all_vals:
                return 50
            below = sum(1 for v in all_vals if v <= value)
            return round((below / len(all_vals)) * 100)

        sv_vals = [g.get("save_pct") or 0 for g in all_goalies_min10]
        gaa_vals = [g.get("gaa") or 0 for g in all_goalies_min10]
        wp_vals = [g.get("win_pct") or 0 for g in all_goalies_min10]

        goalie_radar = []
        for g in bjk_goalies:
            gp = g.get("games_played") or 1
            # Match and clean goalie name
            matched_name = match_player(g.get("goalie_name", "")) or clean_name(g.get("goalie_name", ""))
            goalie_radar.append({
                "name": matched_name,
                "gp": gp,
                "sv_pct": g.get("save_pct", 0),
                "gaa": g.get("gaa", 0),
                "shutouts": g.get("shutouts", 0),
                "wins": g.get("wins", 0),
                "losses": g.get("losses", 0),
                "win_pct": g.get("win_pct", 0),
                "saves_per_gp": round((g.get("saves", 0) / gp), 1),
                "gsaa": round(g.get("saves", 0) - (g.get("saves", 0) / (g.get("save_pct", 0)/100 if g.get("save_pct") else 1)) * 0.90, 1), # Roughly estimating GSAA assuming 90% is avg
                "percentiles": {
                    "sv_pct": percentile(g.get("save_pct", 0), sv_vals),
                    "gaa": 100 - percentile(g.get("gaa", 0), gaa_vals),  # lower is better
                    "win_pct": percentile(g.get("win_pct", 0), wp_vals),
                },
            })

        # â”€â”€ PP/PK from game events â”€â”€
        bjk_pp_goals = sum(1 for e in events if e.get("event_type") == "goal" and e.get("is_power_play") and (e.get("team_code") or "").upper() in BJK_CODES)
        bjk_penalties_taken = sum(1 for e in events if e.get("event_type") == "penalty" and (e.get("team_code") or "").upper() in BJK_CODES)
        opp_penalties = sum(1 for e in events if e.get("event_type") == "penalty" and (e.get("team_code") or "").upper() not in BJK_CODES)
        opp_pp_goals = sum(1 for e in events if e.get("event_type") == "goal" and e.get("is_power_play") and (e.get("team_code") or "").upper() not in BJK_CODES)
        bjk_total_goals = sum(1 for e in events if e.get("event_type") == "goal" and (e.get("team_code") or "").upper() in BJK_CODES)
        opp_total_goals = sum(1 for e in events if e.get("event_type") == "goal" and (e.get("team_code") or "").upper() not in BJK_CODES)

        special_teams = {
            "pp_goals": bjk_pp_goals,
            "pp_opportunities": opp_penalties,
            "pp_pct": round((bjk_pp_goals / max(opp_penalties, 1)) * 100, 1),
            "pk_goals_against": opp_pp_goals,
            "pk_times": bjk_penalties_taken,
            "pk_pct": round(((bjk_penalties_taken - opp_pp_goals) / max(bjk_penalties_taken, 1)) * 100, 1),
            "special_teams_index": round(((bjk_pp_goals / max(opp_penalties, 1)) * 100) + (((bjk_penalties_taken - opp_pp_goals) / max(bjk_penalties_taken, 1)) * 100), 1),
            "total_pim": sum(e.get("penalty_minutes", 0) for e in events if (e.get("team_code") or "").upper() in BJK_CODES),
            "avg_pim_per_game": round(sum(e.get("penalty_minutes", 0) for e in events if (e.get("team_code") or "").upper() in BJK_CODES) / max(len(bjk_games), 1), 1),
        }

        # â”€â”€ Attendance â”€â”€
        home_games = [g for g in bjk_games if is_bjk(g.get("home_team", ""))]
        specs = [g.get("spectators") for g in home_games if g.get("spectators")]
        attendance = {
            "avg": round(sum(specs) / max(len(specs), 1)) if specs else 0,
            "max": max(specs) if specs else 0,
            "min": min(specs) if specs else 0,
            "total": sum(specs) if specs else 0,
            "home_games": len(home_games),
            "trend": [
                {"date": g.get("match_date")[:10], "opponent": g.get("away_team"), "spectators": g.get("spectators")} 
                for g in home_games if g.get("spectators")
            ]
        }

        # â”€â”€ Modul 9: Penalty Breakdown â”€â”€
        bjk_penalties = [e for e in events if e.get("event_type") == "penalty" and (e.get("team_code") or "").upper() in BJK_CODES]
        
        pen_by_type = {}
        pen_by_period = {1:0, 2:0, 3:0, 4:0} # 4 = OT
        pen_by_player = {}
        
        for p in bjk_penalties:
            ptype = p.get("penalty_type") or "OkÃ¤nd"
            pen_by_type[ptype] = pen_by_type.get(ptype, 0) + 1
            
            per = p.get("period") or 1
            if per > 3: per = 4
            pen_by_period[per] += 1
            
            name = p.get("player_name") or "OkÃ¤nd"
            mins = p.get("penalty_minutes") or 2
            if name not in pen_by_player:
                pen_by_player[name] = {"count": 0, "minutes": 0}
            pen_by_player[name]["count"] += 1
            pen_by_player[name]["minutes"] += mins
            
        penalty_breakdown = {
            "by_type": [{"type": k, "count": v} for k, v in sorted(pen_by_type.items(), key=lambda x: -x[1])[:5]],
            "by_period": [{"period": k, "count": v} for k, v in pen_by_period.items()],
            "most_penalized": [{"name": k, "count": v["count"], "minutes": v["minutes"]} for k, v in sorted(pen_by_player.items(), key=lambda x: -x[1]["minutes"])[:5]],
        }

        # â”€â”€ Modul 10: The Prediction Engine (Elo) â”€â”€
        elo = {}
        for s in standings:
            elo[s.get("team_name")] = 1500
            
        elo_history = []
        K = 20
        HFA = 40
        
        for g in schedule:
            ht = g.get("home_team")
            at = g.get("away_team")
            if not ht or not at: continue
            
            if ht not in elo: elo[ht] = 1500
            if at not in elo: elo[at] = 1500
            
            # Save history for BJK if it's a BJK game
            if is_bjk(ht) or is_bjk(at):
                bjk_name = ht if is_bjk(ht) else at
                if g.get("result"):
                    elo_history.append({"date": g.get("match_date", "")[:10], "elo": round(elo[bjk_name])})
                
            res_str = (g.get("result") or "").strip()
            m = re.match(r'(\d+)\s*-\s*(\d+)', res_str)
            if not m: continue # game not played yet
            
            hg, ag = int(m.group(1)), int(m.group(2))
            
            # Actual score
            pr = parse_period_results(g.get("period_results", ""))
            is_ot = len(pr) > 3
            
            if hg > ag:
                s_home, s_away = (1, 0) if not is_ot else (0.65, 0.35)
            elif hg < ag:
                s_home, s_away = (0, 1) if not is_ot else (0.35, 0.65)
            else:
                s_home, s_away = (0.5, 0.5)

            e_home = 1 / (1 + 10 ** ((elo[at] - (elo[ht] + HFA)) / 400))
            e_away = 1 - e_home
            
            elo[ht] += K * (s_home - e_home)
            elo[at] += K * (s_away - e_away)

        # Append current elo to history
        bjk_current_name = next((k for k in elo if is_bjk(k)), "IF BjÃ¶rklÃ¶ven")
        if not elo_history or elo_history[-1]["date"] != "Idag":
            elo_history.append({"date": "Idag", "elo": round(elo.get(bjk_current_name, 1500))})

        # Next game prediction
        future_bjk_games = [g for g in schedule if bjk_game(g) and not g.get("result")]
        next_game = future_bjk_games[0] if future_bjk_games else None
        next_game_prediction = None
        if next_game:
            ht = next_game.get("home_team")
            at = next_game.get("away_team")
            bjk_is_home = is_bjk(ht)
            opp_name = at if bjk_is_home else ht
            
            bjk_elo = elo.get(bjk_current_name, 1500)
            opp_elo = elo.get(opp_name, 1500)
            
            diff = opp_elo - (bjk_elo + (HFA if bjk_is_home else -HFA))
            win_prob = 1 / (1 + 10 ** (diff / 400))
            
            next_game_prediction = {
                "opponent": opp_name,
                "is_home": bjk_is_home,
                "date": next_game.get("match_date", "")[:10],
                "win_prob": round(win_prob * 100, 1),
                "bjk_elo": round(bjk_elo),
                "opp_elo": round(opp_elo)
            }

        # â”€â”€ Modul 11: Projected Standings â”€â”€
        TOTAL_GAMES = 52
        projected_standings = []
        for s in standings:
            name = s.get("team_name", "")
            gp = s.get("games_played", 0)
            pts = s.get("points", 0)
            rem = max(0, TOTAL_GAMES - gp)
            
            ppg = pts / gp if gp > 0 else 0
            
            # Blend current PPG and Elo for projection
            team_elo = elo.get(name, 1500)
            elo_implied_ppg = 1.5 + (team_elo - 1500) * 0.003
            
            weight_ppg = min(1.0, gp / TOTAL_GAMES)
            proj_ppg = (ppg * weight_ppg) + (elo_implied_ppg * (1 - weight_ppg))
            
            proj_pts = pts + (rem * proj_ppg)
            
            projected_standings.append({
                "team": name,
                "current_points": pts,
                "projected_points": round(proj_pts),
                "current_rank": s.get("rank", 0),
                "is_bjk": is_bjk(name)
            })
            
        projected_standings.sort(key=lambda x: -x["projected_points"])
        for i, p in enumerate(projected_standings, 1):
            p["projected_rank"] = i

        # â”€â”€ Modul 12: Game State Analysis (Clutch factor) â”€â”€
        game_state = {
            "lead_after_1": {"w": 0, "l": 0, "otl": 0},
            "trail_after_1": {"w": 0, "l": 0, "otl": 0},
            "tied_after_1": {"w": 0, "l": 0, "otl": 0},
            "lead_after_2": {"w": 0, "l": 0, "otl": 0},
            "trail_after_2": {"w": 0, "l": 0, "otl": 0},
            "tied_after_2": {"w": 0, "l": 0, "otl": 0},
            "game_types": {
                "one_goal": {"w": 0, "l": 0},
                "two_goals": {"w": 0, "l": 0},
                "three_plus_goals": {"w": 0, "l": 0}
            }
        }

        for g in bjk_games:
            res_str = (g.get("result") or "").strip()
            m = re.match(r'(\d+)\s*-\s*(\d+)', res_str)
            if not m: continue
            
            hg, ag = int(m.group(1)), int(m.group(2))
            bjk_home = is_bjk(g.get("home_team", ""))
            
            pr = parse_period_results(g.get("period_results", ""))
            if len(pr) < 2: continue # Need at least 2 periods
            
            p1_hg, p1_ag = pr[0]["home_gf"], pr[0]["away_gf"]
            p2_hg, p2_ag = p1_hg + pr[1]["home_gf"], p1_ag + pr[1]["away_gf"]
            
            bjk_gf = hg if bjk_home else ag
            bjk_ga = ag if bjk_home else hg
            is_ot = len(pr) > 3
            
            if bjk_gf > bjk_ga: final = "w"
            elif bjk_gf < bjk_ga and is_ot: final = "otl"
            else: final = "l"
            
            bjk_p1_gf = p1_hg if bjk_home else p1_ag
            bjk_p1_ga = p1_ag if bjk_home else p1_hg
            
            bjk_p2_gf = p2_hg if bjk_home else p2_ag
            bjk_p2_ga = p2_ag if bjk_home else p2_hg
            
            if bjk_p1_gf > bjk_p1_ga: game_state["lead_after_1"][final] += 1
            elif bjk_p1_gf < bjk_p1_ga: game_state["trail_after_1"][final] += 1
            else: game_state["tied_after_1"][final] += 1
            
            if bjk_p2_gf > bjk_p2_ga: game_state["lead_after_2"][final] += 1
            elif bjk_p2_gf < bjk_p2_ga: game_state["trail_after_2"][final] += 1
            else: game_state["tied_after_2"][final] += 1
            
            # Game Types
            goal_diff = abs(bjk_gf - bjk_ga)
            win_loss_key = "w" if bjk_gf > bjk_ga else "l"
            if goal_diff == 1:
                game_state["game_types"]["one_goal"][win_loss_key] += 1
            elif goal_diff == 2:
                game_state["game_types"]["two_goals"][win_loss_key] += 1
            elif goal_diff >= 3:
                game_state["game_types"]["three_plus_goals"][win_loss_key] += 1

        # â”€â”€ Modul 13: MÃ¥lklockan (Scoring Intensity) â”€â”€
        scoring_timeline = [{"interval": f"{i*10}-{(i+1)*10}", "gf": 0, "ga": 0} for i in range(6)]
        for e in events:
            if e.get("event_type") == "goal":
                t_str = e.get("time", "")
                m = re.match(r'(\d+):(\d+)', t_str)
                if not m: continue
                mins = int(m.group(1))
                if mins >= 60: continue # Skip OT
                
                bin_idx = mins // 10
                is_bjk_goal = (e.get("team_code") or "").upper() in BJK_CODES
                is_bjk_game = is_bjk(e.get("home_team")) or is_bjk(e.get("away_team"))
                
                if not is_bjk_game: continue
                
                if is_bjk_goal:
                    scoring_timeline[bin_idx]["gf"] += 1
                else:
                    scoring_timeline[bin_idx]["ga"] += 1

        # â”€â”€ Modul 14: KemimÃ¤taren (Top Combinations) â”€â”€
        chemistry = {}
        for e in events:
            if e.get("event_type") == "goal" and (e.get("team_code") or "").upper() in BJK_CODES:
                goal_scorer = e.get("player_name")
                a1 = e.get("assist1_name")
                a2 = e.get("assist2_name")
                
                if not goal_scorer: continue
                
                pairs = []
                if a1: pairs.append(tuple(sorted([goal_scorer, a1])))
                if a2: pairs.append(tuple(sorted([goal_scorer, a2])))
                if a1 and a2: pairs.append(tuple(sorted([a1, a2])))
                
                for p in pairs:
                    if p not in chemistry: chemistry[p] = 0
                    chemistry[p] += 1
                    
        top_chemistry = [{"player1": p[0], "player2": p[1], "goals_created": count} 
                         for p, count in sorted(chemistry.items(), key=lambda x: -x[1])[:5]]

        # â”€â”€ Modul 15: First Goal Impact â”€â”€
        first_goal_impact = {"scored_first": {"w":0, "l":0, "otl":0}, "conceded_first": {"w":0, "l":0, "otl":0}}
        
        events_sorted = sorted(events, key=lambda x: (x.get("game_id", ""), x.get("period", 1), x.get("time", "00:00")))
        first_goals = {}
        for e in events_sorted:
            gid = e.get("game_id")
            if gid not in first_goals and e.get("event_type") == "goal":
                first_goals[gid] = e

        for g in bjk_games:
            gid = g.get("game_id")
            if not gid: continue
            fg = first_goals.get(gid)
            if not fg: continue
            
            bjk_scored_first = (fg.get("team_code") or "").upper() in BJK_CODES
            
            res_str = (g.get("result") or "").strip()
            m = re.match(r'(\d+)\s*-\s*(\d+)', res_str)
            if not m: continue
            hg, ag = int(m.group(1)), int(m.group(2))
            bjk_home = is_bjk(g.get("home_team", ""))
            bjk_gf = hg if bjk_home else ag
            bjk_ga = ag if bjk_home else hg
            pr = parse_period_results(g.get("period_results", ""))
            is_ot = len(pr) > 3
            
            if bjk_gf > bjk_ga: final = "w"
            elif bjk_gf < bjk_ga and is_ot: final = "otl"
            else: final = "l"
            
            if bjk_scored_first:
                first_goal_impact["scored_first"][final] += 1
            else:
                first_goal_impact["conceded_first"][final] += 1

        # â”€â”€ Modul 16: Tur/Otur-index (Pythagorean) â”€â”€
        pythagorean = []
        for s in standings:
            name = s.get("team_name", "")
            gp = s.get("games_played", 0)
            gf = s.get("goals_for", 0)
            ga = s.get("goals_against", 0)
            pts = s.get("points", 0)
            
            if gp > 0 and (gf + ga) > 0:
                exp_win_pct = (gf**2) / (gf**2 + ga**2)
                exp_pts = exp_win_pct * (gp * 3)
            else:
                exp_pts = 0
                
            pythagorean.append({
                "team": name,
                "gp": gp,
                "pts": pts,
                "exp_pts": round(exp_pts, 1),
                "diff": round(pts - exp_pts, 1),
                "is_bjk": is_bjk(name)
            })
        pythagorean.sort(key=lambda x: -x["diff"])
        
        # ── Modul 18: SHL Transition Calculations ──
        leaving_names = [d["name"] for d in SILLY_SEASON_BASELINE.get("confirmed_departures", [])]
        def is_leaving(player_name):
            matched = match_player(player_name)
            if not matched:
                return False
            return any(matched == ln for ln in leaving_names)

        signings_overrides = {}
        for signing in SILLY_SEASON_BASELINE.get("confirmed_signings", []):
            if "shl_projection" in signing:
                signings_overrides[signing["name"]] = signing["shl_projection"]

        def skater_readiness_by_position(position, proj_ppg):
            pos = (position or "").upper()
            if "D" in pos:
                return "GREEN" if proj_ppg >= 0.35 else "AMBER" if proj_ppg >= 0.18 else "RED"
            return "GREEN" if proj_ppg >= 0.50 else "AMBER" if proj_ppg >= 0.25 else "RED"

        def normalized_name(name):
            return re.sub(r"\s+", " ", str(name or "").strip().lower())

        def name_match_strict(a, b):
            na = normalized_name(a)
            nb = normalized_name(b)
            if na == nb:
                return True
            ta = name_tokens(a)
            tb = name_tokens(b)
            return len(ta.intersection(tb)) >= 2

        shl_skaters = []
        for p in player_impact:
            if is_leaving(p["name"]):
                continue
            
            name = p["name"]
            matched_override = None
            for override_name, override_data in signings_overrides.items():
                if name_match_strict(name, override_name):
                    matched_override = (override_name, override_data)
                    break
                    
            if matched_override:
                override_name, override_data = matched_override
                proj_ppg = override_data["proj_ppg"]
                ha_ppg = override_data["ha_ppg"]
                display_name = f"{override_name} 🆕"
            else:
                proj_ppg = round(p["p_per_gp"] * 0.60, 2)
                ha_ppg = round(p["p_per_gp"], 2)
                display_name = name

            readiness = skater_readiness_by_position(p["position"], proj_ppg)
            shl_skaters.append({
                "name": display_name,
                "position": p["position"],
                "ha_ppg": ha_ppg,
                "proj_ppg": proj_ppg,
                "readiness": readiness
            })
        
        shl_goalies = []
        for g in goalie_radar:
            if is_leaving(g["name"]):
                continue
            proj_sv_pct = round(g["sv_pct"] - 1.8, 1)
            proj_gaa = round(g["gaa"] + 0.60, 2)
            readiness = "GREEN" if proj_sv_pct >= 91.0 else "AMBER" if proj_sv_pct >= 89.2 else "RED"
            shl_goalies.append({
                "name": g["name"],
                "ha_sv_pct": g["sv_pct"],
                "proj_sv_pct": proj_sv_pct,
                "proj_gaa": proj_gaa,
                "readiness": readiness
            })
            
        shl_benchmarks = {
            "pp_pct": {"current": special_teams["pp_pct"], "target": 18.0, "diff": round(special_teams["pp_pct"] - 18.0, 1)},
            "pk_pct": {"current": special_teams["pk_pct"], "target": 77.0, "diff": round(special_teams["pk_pct"] - 77.0, 1)},
            "goalie_sv": {"current": max([g["sv_pct"] for g in goalie_radar]) if goalie_radar else 0, "target": 90.0, "diff": round((max([g["sv_pct"] for g in goalie_radar]) if goalie_radar else 0) - 90.0, 1)},
            "special_teams_index": {"current": special_teams.get("special_teams_index", 0), "target": 95.0, "diff": round(special_teams.get("special_teams_index", 0) - 95.0, 1)}
        }
        
        shl_transition = {
            "skaters": shl_skaters,
            "goalies": shl_goalies,
            "benchmarks": shl_benchmarks
        }

        roster_ages = {
            "Lenni Killinen": 26,
            "Linus Cronholm": 26,
            "Marcus Björk": 29,
            "Gustav Possler": 32,
            "Albin Lundin": 30,
            "Fredrik Forsberg": 30,
            "Daniel Brodin": 36,
            "Joel Mustonen": 34,
            "Jacob Olofsson": 26,
            "Frans Tuohimaa": 35
        }

        age_skaters = []
        for p in shl_skaters:
            # Clean name from display name (e.g. remove the emoji " 🆕")
            raw_name = p["name"].replace(" 🆕", "").strip()
            
            # Match name to get the age (prefer exact normalized name first).
            matched_age = 26 # Default fallback age
            raw_name_norm = normalized_name(raw_name)
            exact_age_map = {normalized_name(n): a for n, a in roster_ages.items()}
            if raw_name_norm in exact_age_map:
                matched_age = exact_age_map[raw_name_norm]
            else:
                for name, age in roster_ages.items():
                    if name_match_strict(raw_name, name):
                        matched_age = age
                        break
            
            # Aging curve multiplier
            if matched_age <= 21:
                multiplier = 0.15
                trajectory = "UTVECKLING"
            elif matched_age <= 23:
                multiplier = 0.08
                trajectory = "TILLVÃ„XT"
            elif matched_age <= 28:
                multiplier = 0.00
                trajectory = "PEAK PRIME"
            elif matched_age <= 33:
                multiplier = -0.08
                trajectory = "RUTINERAD"
            else:
                multiplier = -0.22
                trajectory = "VETERANRISK"
            
            # Adjusted PPG
            adj_proj_ppg = round(p["proj_ppg"] * (1 + multiplier), 2)
            # Ensure it doesn't go below 0
            adj_proj_ppg = max(0.0, adj_proj_ppg)
            
            # Recalculate readiness based on age-adjusted PPG
            readiness = skater_readiness_by_position(p["position"], adj_proj_ppg)
            
            age_skaters.append({
                "name": p["name"],
                "position": p["position"],
                "age": matched_age,
                "ha_ppg": p["ha_ppg"],
                "base_proj_ppg": p["proj_ppg"],
                "adj_proj_ppg": adj_proj_ppg,
                "multiplier_pct": int(multiplier * 100),
                "trajectory": trajectory,
                "readiness": readiness
            })

        age_goalies = []
        for g in shl_goalies:
            raw_name = g["name"].replace(" ðŸ†•", "").strip()
            
            matched_age = 28 # Default fallback goalie age
            for name, age in roster_ages.items():
                if name_tokens(raw_name).intersection(name_tokens(name)):
                    matched_age = age
                    break
                    
            if matched_age <= 23:
                multiplier = 0.05
                trajectory = "TILLVÃ„XT"
            elif matched_age <= 29:
                multiplier = 0.00
                trajectory = "PEAK PRIME"
            elif matched_age <= 33:
                multiplier = -0.04
                trajectory = "RUTINERAD"
            else:
                multiplier = -0.10
                trajectory = "VETERANRISK"
                
            # Adjust SV% relative to average regression
            adj_proj_sv_pct = round(g["proj_sv_pct"] + (multiplier * 10.0), 1)
            # Projected GAA goes up when SV% goes down
            adj_proj_gaa = round(g["proj_gaa"] * (1 - multiplier), 2)
            
            readiness = "GREEN" if adj_proj_sv_pct >= 91.0 else "AMBER" if adj_proj_sv_pct >= 89.2 else "RED"
            
            age_goalies.append({
                "name": g["name"],
                "age": matched_age,
                "ha_sv_pct": g["ha_sv_pct"],
                "base_proj_sv_pct": g["proj_sv_pct"],
                "adj_proj_sv_pct": adj_proj_sv_pct,
                "base_proj_gaa": g["proj_gaa"],
                "adj_proj_gaa": adj_proj_gaa,
                "multiplier_pct": int(multiplier * 100),
                "trajectory": trajectory,
                "readiness": readiness
            })

        age_curve = {
            "skaters": age_skaters,
            "goalies": age_goalies
        }

        # â”€â”€ Modul 20: Predicted SHL Table (Preseason) â”€â”€
        shl_projected_table = {
            "season": "SHL 2026/27 (preseason)",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "method": "Team-strength blend (historic SHL baseline + BJK roster projection)",
            "data_quality": "ok",
            "table": [],
            "bjk_summary": {
                "projected_rank": None, "projected_points": None, "top6_chance_pct": None, "playout_risk_pct": None,
                "projected_points_p10": None, "projected_points_p50": None, "projected_points_p90": None,
                "projected_rank_p10": None, "projected_rank_p50": None, "projected_rank_p90": None,
            },
        }
        try:
            shl_season_rows = q(f"""
                SELECT season_key, season_name, regular_season_id, start_date
                FROM `{proj}.raw_sports.swehockey_seasons`
                WHERE LOWER(season_name) LIKE '%shl%'
                ORDER BY start_date DESC
                LIMIT 1
            """)
            shl_regular_id = shl_season_rows[0]["regular_season_id"] if shl_season_rows else None

            shl_standings = []
            if shl_regular_id:
                shl_standings = q(f"""
                    SELECT team_name, games_played, points, rank
                    FROM `{proj}.raw_sports.swehockey_standings`
                    WHERE season_group_id = {int(shl_regular_id)}
                      AND COALESCE(games_played, 0) >= 40
                      AND COALESCE(points, 0) > 0
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY team_name, season_group_id
                        ORDER BY scraped_at DESC
                    ) = 1
                """)

            if not shl_standings:
                shl_projected_table["data_quality"] = "missing_shl_source"
                raise ValueError("No SHL standings data available in raw_sports.swehockey_standings for latest SHL season")

            # Build baseline strength from SHL standings
            shl_rows = []
            for row in shl_standings:
                gp = max(1, int(row.get("games_played") or 52))
                pts = float(row.get("points") or 0)
                ppg = pts / gp
                rank = int(row.get("rank") or 14)
                # Robust seed: blend realized points pace with rank-based strength.
                # This dampens outliers from imperfect source snapshots.
                ppg_seed = ppg * 52.0
                rank_seed = max(42.0, 100.0 - ((rank - 1) * 4.0))
                base_points = round((ppg_seed * 0.75) + (rank_seed * 0.25))
                shl_rows.append({
                    "team": row.get("team_name", "Unknown"),
                    "ppg": ppg,
                    "base_projected_points": int(base_points),
                    "rank": rank,
                })

            # Use latest SHL season as performance baseline, but lock team set to upcoming SHL 2026/27.
            # Current business context: Björklöven promoted, MODO not in upcoming SHL roster.
            relegated_from_shl_tokens = {"modo", "leksand", "leksands"}
            promoted_to_shl = [{"team": "IF Björklöven", "seed_points": 58}]

            def _normalize_team_label(team_name):
                n = str(team_name or "").strip()
                n_low = n.lower()
                if "bjã¶rklã¶ven" in n_low or "bjã¶rkloven" in n_low or "bjã¶rklöven" in n_low:
                    return "IF Björklöven"
                if "björklöven" in n_low or "bjorkloven" in n_low:
                    return "IF Björklöven"
                return n

            def _is_relegated_team(team_name):
                n = (team_name or "").strip().lower()
                return any(tok in n for tok in relegated_from_shl_tokens)

            filtered_rows = [r for r in shl_rows if not _is_relegated_team(r.get("team", ""))]
            for p in promoted_to_shl:
                exists = any((r.get("team", "").strip().lower() == p["team"].strip().lower()) for r in filtered_rows)
                if not exists:
                    filtered_rows.append({
                        "team": p["team"],
                        "ppg": p["seed_points"] / 52.0,
                        "base_projected_points": int(p["seed_points"]),
                    })
            shl_rows = filtered_rows

            # BJK dynamic roster lift from current projections + silly season updates
            sk_adj = [s.get("adj_proj_ppg", 0) for s in age_skaters]
            g_adj = [g.get("adj_proj_sv_pct", 0) for g in age_goalies]
            avg_sk_adj = (sum(sk_adj) / len(sk_adj)) if sk_adj else 0.35
            avg_g_adj = (sum(g_adj) / len(g_adj)) if g_adj else 89.5

            signings_count = len(SILLY_SEASON_BASELINE.get("confirmed_signings", []))
            departures_count = len(SILLY_SEASON_BASELINE.get("confirmed_departures", []))
            expiring_count = len(SILLY_SEASON_BASELINE.get("expiring_contracts", []))

            bjk_points_model = 58.0
            bjk_points_model += (avg_sk_adj - 0.38) * 80.0
            bjk_points_model += (avg_g_adj - 89.5) * 2.4
            bjk_points_model += (special_teams.get("special_teams_index", 95.0) - 95.0) * 0.35
            bjk_points_model += signings_count * 1.8
            bjk_points_model -= departures_count * 0.5
            bjk_points_model -= expiring_count * 0.9
            bjk_points_model = max(46.0, min(96.0, bjk_points_model))

            found_bjk = False
            for r in shl_rows:
                r["team"] = _normalize_team_label(r.get("team"))
                if is_bjk(r["team"]) or "björklöven" in (r["team"] or "").lower() or "bjorkloven" in (r["team"] or "").lower():
                    r["base_projected_points"] = round(bjk_points_model)
                    found_bjk = True
                    break
            if not found_bjk:
                shl_rows.append({"team": "IF Björklöven", "ppg": bjk_points_model / 52.0, "base_projected_points": round(bjk_points_model)})

            shl_rows.sort(key=lambda x: -x["base_projected_points"])
            projected_table_rows = []
            volatility = 6 + (departures_count * 0.2) + (expiring_count * 0.6) - (signings_count * 0.15)
            volatility = max(4.5, min(10.0, volatility))
            for i, r in enumerate(shl_rows, 1):
                pts = int(r["base_projected_points"])
                p10_pts = int(max(35, round(pts - (volatility * 1.3))))
                p90_pts = int(min(110, round(pts + (volatility * 1.3))))
                rank_spread = 2 if i <= 6 else 3
                p10_rank = max(1, i - rank_spread)
                p90_rank = min(len(shl_rows), i + rank_spread)
                top6_chance = max(2, min(96, int(100 - (i - 1) * 6)))
                playout_risk = max(2, min(90, int((i - 8) * 8))) if i >= 8 else 2
                tier = "Topplag" if i <= 4 else "Slutspel" if i <= 10 else "Riskzon"
                projected_table_rows.append({
                    "projected_rank": i,
                    "projected_rank_p10": p10_rank,
                    "projected_rank_p50": i,
                    "projected_rank_p90": p90_rank,
                    "team": r["team"],
                    "projected_points": pts,
                    "projected_points_p10": p10_pts,
                    "projected_points_p50": pts,
                    "projected_points_p90": p90_pts,
                    "tier": tier,
                    "top6_chance_pct": top6_chance,
                    "playout_risk_pct": playout_risk,
                    "is_bjk": is_bjk(r["team"]) or "bjÃ¶rklÃ¶ven" in (r["team"] or "").lower(),
                })

            bjk_row = next((r for r in projected_table_rows if r["is_bjk"]), None)
            if bjk_row:
                shl_projected_table["bjk_summary"] = {
                    "projected_rank": bjk_row["projected_rank"],
                    "projected_points": bjk_row["projected_points"],
                    "top6_chance_pct": bjk_row["top6_chance_pct"],
                    "playout_risk_pct": bjk_row["playout_risk_pct"],
                    "projected_points_p10": bjk_row["projected_points_p10"],
                    "projected_points_p50": bjk_row["projected_points_p50"],
                    "projected_points_p90": bjk_row["projected_points_p90"],
                    "projected_rank_p10": bjk_row["projected_rank_p10"],
                    "projected_rank_p50": bjk_row["projected_rank_p50"],
                    "projected_rank_p90": bjk_row["projected_rank_p90"],
                }
            shl_projected_table["table"] = projected_table_rows
        except Exception as shl_proj_err:
            logging.warning(f"Failed to compute shl_projected_table: {shl_proj_err}")

        # â”€â”€ Modul 17: AI-Coachen (Gemini) â”€â”€
        bjk_pyth = next((p for p in pythagorean if p["is_bjk"]), None)
        opp_name = next_game_prediction['opponent'] if next_game_prediction else 'OkÃ¤nd'
        win_prob = next_game_prediction['win_prob'] if next_game_prediction else '-'
        diff = bjk_pyth['diff'] if bjk_pyth else 0
        p1 = top_chemistry[0]['player1'] if top_chemistry else 'OkÃ¤nd'
        p2 = top_chemistry[0]['player2'] if top_chemistry else 'OkÃ¤nd'
        goals_created = top_chemistry[0]['goals_created'] if top_chemistry else 0
        
        # Season Data
        recent_streak = streaks[-1] if streaks else None
        sti = special_teams.get("special_teams_index", 0)
        
        # Count RED readiness players for AI
        red_skaters = len([s for s in shl_skaters if s["readiness"] == "RED"])
        red_goalies = len([g for g in shl_goalies if g["readiness"] == "RED"])
        
        prompt = f"""
        Du Ã¤r 'Analytikern', BjÃ¶rklÃ¶vens interna AI-assisterande trÃ¤nare och sportchefens strategiska rÃ¥dgivare.
        Du MÃ…STE svara med en ren, giltig JSON-struktur (inga markdown-taggar som ```json).
        JSON-strukturen ska exakt ha dessa nycklar:
        {{
            "taktik": "Kort taktisk analys (max 3 meningar) baserad pÃ¥ att nÃ¤sta motstÃ¥ndare Ã¤r {opp_name}, vÃ¥r vinstchans Ã¤r {win_prob}%, och vÃ¥r Tur/Otur-diff Ã¤r {diff}.",
            "sasong_form": "Kort diagnos av sÃ¤songen/formen. VÃ¥r streak: {recent_streak}. Special Teams Index (PP%+PK%) Ã¤r {sti} (Ã¶ver 100 Ã¤r extremt starkt).",
            "spelar_impact": "Kort spaning om radarpar eller enskilda spelare. Hetast just nu: {p1} & {p2} ({goals_created} mÃ¥l skapade ihop).",
            "shl_sportchef": "Sportchef-analys infÃ¶r SHL (max 3 meningar). Vi har {red_skaters} utespelare och {red_goalies} mÃ¥lvakter som flaggas som 'RED' (under SHL-klass). Ge ett konkret vÃ¤rvningsrÃ¥d baserat pÃ¥ detta och lagets svagheter."
        }}
        Skriv koncist, professionellt och auktoritÃ¤rt pÃ¥ svenska.
        """
        
        ai_coach_data = {
            "taktik": "Analytikern Ã¤r fÃ¶r tillfÃ¤llet offline.",
            "sasong_form": "Analytikern kunde inte hÃ¤mta sÃ¤songsdata.",
            "spelar_impact": "Kunde inte ladda spelarscouting.",
            "shl_sportchef": "Kunde inte generera SHL-scouting."
        }
        try:
            from google import genai
            import os
            client = genai.Client(vertexai=True, project=proj, location="europe-west1")
            ai_res = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            if ai_res.text:
                # Clean up potential markdown formatting
                clean_json = ai_res.text.strip().removeprefix('```json').removesuffix('```').strip()
                try:
                    parsed = json.loads(clean_json)
                    ai_coach_data = parsed
                except json.JSONDecodeError:
                    logging.warning(f"Failed to parse AI JSON: {ai_res.text}")
                    ai_coach_data["taktik"] = ai_res.text
        except Exception as e:
            logging.warning(f"AI Coach failed: {e}")

        return {
            "status": "ok",
            "modules": {
                "timeline": timeline,
                "splits": splits,
                "periods": periods,
                "h2h": h2h_list,
                "form": form,
                "streaks": {
                    "longest_win": longest_win,
                    "longest_loss": longest_loss,
                    "current": streaks[-1] if streaks else None,
                    "all": streaks,
                },
                "player_impact": player_impact,
                "goalie_radar": goalie_radar,
                "special_teams": special_teams,
                "attendance": attendance,
                "penalty_breakdown": penalty_breakdown,
                "predictions": {
                    "elo_history": elo_history,
                    "next_game": next_game_prediction,
                    "projected_standings": projected_standings,
                    "scoring_timeline": scoring_timeline,
                    "chemistry": top_chemistry,
                    "first_goal_impact": first_goal_impact,
                    "pythagorean": pythagorean,
                    "ai_coach": ai_coach_data,
                },
                "game_state": game_state,
                "shl_transition": shl_transition,
                "age_curve": age_curve,
                "shl_projected_table": shl_projected_table,
            },
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/analytics")
        return {"status": "error", "error": str(e)}


def normalize_title(title):
    return re.sub(r'[^\wÃ¥Ã¤Ã¶\s]', '', title.lower()).strip()



WOMENS_CONTEXT_KEYWORDS = {
    "sdhl", "dam", "damlag", "damlaget", "damernas", "damerna",
    "damspelare", "kvinnliga", "women", "womens", "f19", "f18", "f17", "f16",
}


def is_womens_article(article):
    title = str(article.get("title") or "").lower()
    body = str(article.get("body") or "").lower()
    source = str(article.get("source") or "").lower()
    url = str(article.get("url") or article.get("link") or "").lower()
    blob = " ".join([title, body, source, url])
    if any(kw in blob for kw in WOMENS_CONTEXT_KEYWORDS):
        return True
    if "petra" in title and ("bj?rkl?ven" in title or "bjorkloven" in title):
        return True
    return False


def reclassify_tag(article):
    """
    Conservative keyword-based fallback: only reclassifies Ã–VRIGT articles where
    the TITLE clearly indicates a direct BjÃ¶rklÃ¶ven transfer action.
    
    Gemini is usually right to tag things Ã–VRIGT â€” we only override when the title
    unambiguously is about a player joining/leaving/extending with BjÃ¶rklÃ¶ven.
    """
    tag = article.get("tag", "Ã–VRIGT")
    if tag != "Ã–VRIGT":
        return article
    
    title = article.get("title", "").lower()
    
    # Only reclassify based on TITLE, not body (body often mentions BjÃ¶rklÃ¶ven in passing)
    title_mentions_bjorkloven = any(kw in title for kw in ['bjÃ¶rklÃ¶ven', 'bjorkloven'])
    # Be careful with 'lÃ¶ven' â€” too short, matches 'slÃ¶ven', 'GullÃ¶ven' etc.
    # Only match ' lÃ¶ven' or start-of-string 'lÃ¶ven'
    if not title_mentions_bjorkloven:
        if title.startswith('lÃ¶ven') or ' lÃ¶ven' in title:
            title_mentions_bjorkloven = True
    
    if not title_mentions_bjorkloven:
        return article
    
    # Exclude "tidigare BjÃ¶rklÃ¶ven-spelaren" / "ex-BjÃ¶rklÃ¶ven" patterns (former players, not current squad)
    if any(kw in title for kw in ['tidigare', 'ex-', 'f.d.', 'fÃ¶re detta', 'forna']):
        return article
    
    # Now check for specific transfer actions IN THE TITLE tied to BjÃ¶rklÃ¶ven
    # "X fÃ¶rlÃ¤nger/fÃ¶rlÃ¤ngde med BjÃ¶rklÃ¶ven"
    if any(kw in title for kw in ['fÃ¶rlÃ¤nger', 'fÃ¶rlÃ¤ngde', 'fÃ¶rlÃ¤ngd']):
        article["tag"] = "KONTRAKTSFÃ–RLÃ„NGNING"
        return article
    
    # "X lÃ¤mnar BjÃ¶rklÃ¶ven" / "massflykt frÃ¥n BjÃ¶rklÃ¶ven" (also handle missing spaces from HTML parsing)
    if any(phrase in title for phrase in [
        'lÃ¤mnar bjÃ¶rklÃ¶ven', 'lÃ¤mnarbjÃ¶rklÃ¶ven', 'lÃ¤mnar lÃ¶ven', 'lÃ¤mnarlÃ¶ven',
        'frÃ¥n bjÃ¶rklÃ¶ven', 'frÃ¥nbjÃ¶rklÃ¶ven', 'frÃ¥n lÃ¶ven', 'frÃ¥nlÃ¶ven',
    ]):
        article["tag"] = "BEKRÃ„FTAD_FÃ–RLUST"
        return article
    
    # "X klar fÃ¶r BjÃ¶rklÃ¶ven" / "X ansluter till BjÃ¶rklÃ¶ven" / "nyfÃ¶rvÃ¤rv"
    if any(phrase in title for phrase in [
        'klar fÃ¶r bjÃ¶rklÃ¶ven', 'klar fÃ¶rbjÃ¶rklÃ¶ven', 'klar fÃ¶r lÃ¶ven', 'klar fÃ¶rlÃ¶ven',
        'ansluter till bjÃ¶rklÃ¶ven', 'ansluter tillbjÃ¶rklÃ¶ven', 'ansluter till lÃ¶ven',
    ]):
        article["tag"] = "BEKRÃ„FTAT_NYFÃ–RVÃ„RV"
        return article
    if 'nyfÃ¶rvÃ¤rv' in title and title_mentions_bjorkloven:
        article["tag"] = "BEKRÃ„FTAT_NYFÃ–RVÃ„RV"
        return article
    
    # Don't reclassify anything else â€” trust Gemini's judgment
    return article

def deduplicate_articles(scraped, baseline):
    seen = set()
    for item in baseline:
        if is_womens_article(item):
            continue
        seen.add(normalize_title(item.get('title', '')))
    
    unique_scraped = []
    for item in scraped:
        if is_womens_article(item):
            continue
        normalized = normalize_title(item.get('title', ''))
        if normalized not in seen:
            seen.add(normalized)
            unique_scraped.append(item)
    return unique_scraped


def sync_roster_with_confirmed_signings(baseline):
    roster = baseline.get("roster", []) or []
    signings = baseline.get("confirmed_signings", []) or []
    existing = {str((p.get("name") or "")).strip().lower() for p in roster}

    for s in signings:
        name = str((s.get("name") or "")).strip()
        if not name:
            continue
        key = name.lower()
        if key in existing:
            continue
        roster.append({
            "name": name,
            "number": s.get("number"),
            "pos": s.get("pos") or "FW",
            "status": "NYFÃ–RVÃ„RV",
            "contractUntil": s.get("contractUntil"),
            "note": s.get("note") or "",
        })
        existing.add(key)

    baseline["roster"] = roster
    return baseline

def article_identity(item):
    source = (item.get("source") or "").strip().lower()
    url = (item.get("url") or item.get("link") or "").strip().lower()
    title = normalize_title(item.get("title", ""))
    return f"{source}::{url}::{title}"

def compute_new_since_previous(current_scraped, previous_scraped):
    previous_ids = {article_identity(i) for i in (previous_scraped or [])}
    return [i for i in (current_scraped or []) if article_identity(i) not in previous_ids]

def _safe_date(value):
    try:
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def build_last_24h_summary(current_scraped, previous_scraped, critical_now):
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=24)
    current_scraped = current_scraped or []
    previous_scraped = previous_scraped or []

    new_items = compute_new_since_previous(current_scraped, previous_scraped)

    def in_window(item):
        dt = _safe_date(item.get("date"))
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= window_start

    recent = [item for item in current_scraped if in_window(item)]

    def count_tag(tag_name):
        return sum(1 for item in recent if item.get("tag") == tag_name)

    return {
        "new_signals": len(new_items),
        "articles_24h": len(recent),
        "signings": count_tag("BEKRÃ„FTAT_NYFÃ–RVÃ„RV"),
        "departures": count_tag("BEKRÃ„FTAD_FÃ–RLUST"),
        "extensions": count_tag("KONTRAKTSFÃ–RLÃ„NGNING"),
        "rumors": count_tag("HETT_RYKTE"),
        "critical_open": len(critical_now or []),
    }

def build_dynamic_silly_summary(feed, roster):
    now = datetime.utcnow()
    recent_cutoff = now.timestamp() - (120 * 24 * 3600)  # ~4 months

    def is_recent(item):
        dt = _safe_date(item.get("date"))
        if not dt:
            return True
        return dt.timestamp() >= recent_cutoff

    recent = [i for i in (feed or []) if is_recent(i)]

    signings = sum(1 for i in recent if i.get("tag") == "BEKRÃ„FTAT_NYFÃ–RVÃ„RV")
    departures = sum(1 for i in recent if i.get("tag") == "BEKRÃ„FTAD_FÃ–RLUST")
    extensions = sum(1 for i in recent if i.get("tag") == "KONTRAKTSFÃ–RLÃ„NGNING")
    expiring = sum(1 for p in (roster or []) if p.get("status") == "UTGÃ…ENDE")

    return {
        "contracted": signings + extensions,
        "signings": signings,
        "expiring": expiring,
        "departures": departures,
        "extensions": extensions,
    }

def load_recent_silly_snapshots(limit=5):
    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blobs = list(bucket.list_blobs(prefix="raw/silly_season/scraped_"))
    if not blobs:
        return []
    sorted_blobs = sorted(blobs, key=lambda b: b.updated or b.time_created, reverse=True)[:limit]
    snapshots = []
    for blob in sorted_blobs:
        try:
            payload = json.loads(blob.download_as_string())
            feed = payload.get("news_feed", [])
            source_counts = {}
            for item in feed:
                source = item.get("source") or "unknown"
                source_counts[source] = source_counts.get(source, 0) + 1
            snapshots.append({
                "blob": blob.name,
                "updated_at": (blob.updated.isoformat() if blob.updated else None),
                "articles": len(feed),
                "source_counts": source_counts,
            })
        except Exception as e:
            snapshots.append({
                "blob": blob.name,
                "updated_at": (blob.updated.isoformat() if blob.updated else None),
                "articles": None,
                "error": str(e),
                "source_counts": {},
            })
    return snapshots

@app.get("/api/silly-season")
def get_silly_season():
    """
    HÃ¤mtar senaste scraper-datan frÃ¥n GCS och mergar med baseline.
    """
    scraped_articles = []
    previous_scraped_articles = []
    last_refresh = datetime.now().isoformat()
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        # HÃ¤mta blob med prefix raw/silly_season/scraped_ sorterat pÃ¥ senast uppdaterad
        blobs = list(bucket.list_blobs(prefix="raw/silly_season/scraped_"))
        
        if blobs:
            sorted_blobs = sorted(blobs, key=lambda b: b.updated or b.time_created, reverse=True)
            latest_blob = sorted_blobs[0]
            content = latest_blob.download_as_string()
            data = json.loads(content)
            scraped_articles = data.get("news_feed", [])
            last_refresh = latest_blob.updated.isoformat() if latest_blob.updated else last_refresh
            if len(sorted_blobs) > 1:
                prev_content = sorted_blobs[1].download_as_string()
                prev_data = json.loads(prev_content)
                previous_scraped_articles = prev_data.get("news_feed", [])
    except Exception as e:
        logging.error(f"Kunde inte hÃ¤mta scraper-data frÃ¥n GCS: {e}")
        # FortsÃ¤tt med bara baseline
    
    baseline = SILLY_SEASON_BASELINE.copy()
    baseline = sync_roster_with_confirmed_signings(baseline)
    
    baseline_feed = [a for a in (baseline.get("news_feed", []) or []) if not is_womens_article(a)]
    scraped_articles = [a for a in (scraped_articles or []) if not is_womens_article(a)]
    previous_scraped_articles = [a for a in (previous_scraped_articles or []) if not is_womens_article(a)]

    # Deduplicera mot baseline fÃ¶r presentation i feed
    deduped_for_feed = deduplicate_articles(scraped_articles, baseline_feed)
    # BerÃ¤kna verkligt nytt sedan fÃ¶rra scraper-snapshoten
    new_articles = compute_new_since_previous(scraped_articles, previous_scraped_articles)

    for i, article in enumerate(deduped_for_feed):
        article["id"] = f"scraped-{i}"
        article["scraped"] = True
        
        # Reclassify articles that Gemini incorrectly tagged as Ã–VRIGT
        reclassify_tag(article)
        
        # Om tiden saknas, fÃ¶rsÃ¶k extrahera den eller sÃ¤tt aktuell tid
        if "time" not in article:
            article["time"] = datetime.now().strftime("%H:%M")

    # SlÃ¥ ihop och sortera fallande pÃ¥ datum, sedan tid
    merged_feed = deduped_for_feed + baseline_feed
    merged_feed.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
    
    baseline["news_feed"] = merged_feed
    if merged_feed:
        latest = merged_feed[0]
        title = latest.get("title") or ""
        if title:
            baseline["headline"] = title
        # Ensure at least one fresh breaking candidate from latest feed item.
        latest.setdefault("priority", "breaking")
    
    if "_meta" not in baseline:
        baseline["_meta"] = {}
        
    baseline["_meta"]["lastRefresh"] = last_refresh
    baseline["_meta"]["newArticles"] = len(new_articles)
    baseline["_meta"]["scrapedArticles"] = len(scraped_articles)
    baseline["_meta"]["summary"] = build_dynamic_silly_summary(merged_feed, baseline.get("roster", []))
    baseline["_meta"]["last24h"] = build_last_24h_summary(scraped_articles, previous_scraped_articles, [])
    
    return baseline

@app.get("/api/silly-season/ops")
def get_silly_ops():
    """
    Intern driftvy fÃ¶r silly-pipeline.
    Returnerar senaste snapshot-kÃ¶rningar frÃ¥n GCS utan att pÃ¥verka publik UI.
    """
    try:
        snapshots = load_recent_silly_snapshots(limit=5)
        latest_updated = snapshots[0]["updated_at"] if snapshots else None
        return {
            "status": "ok",
            "latest_updated_at": latest_updated,
            "freshness_status": compute_freshness_status(latest_updated),
            "runs": snapshots,
        }
    except Exception as e:
        logging.error(f"Kunde inte lÃ¤sa silly ops-data: {e}")
        return {
            "status": "error",
            "error": str(e),
            "latest_updated_at": None,
            "freshness_status": "unknown",
            "runs": [],
        }


def compute_freshness_status(last_refresh_iso: str | None) -> str:
    if not last_refresh_iso:
        return "unknown"
    try:
        refreshed_at = datetime.fromisoformat(last_refresh_iso.replace("Z", "+00:00"))
    except Exception:
        return "unknown"

    age_seconds = (datetime.now(refreshed_at.tzinfo) - refreshed_at).total_seconds()
    if age_seconds <= 6 * 3600:
        return "fresh"
    if age_seconds <= 24 * 3600:
        return "stale"
    return "critical"


def _x_sentiment_score(text: str):
    def _norm(s: str) -> str:
        s = (s or "").lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = re.sub(r"http\S+", " ", s)
        s = re.sub(r"[^a-z0-9#@!?\s-]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    t = _norm(text or "")

    positive_terms = {
        "klar for": 2,
        "nyforvarv": 2,
        "rykten in": 2,
        "in till": 1,
        "forlanger": 2,
        "forlangning": 2,
        "ansluter": 2,
        "comeback": 1,
        "vinner": 2,
        "seger": 2,
        "starker": 1,
        "starkare": 1,
        "poang": 1,
        "assist": 1,
        "mal": 1,
        "bra": 1,
        "stabil": 1,
        "grym": 1,
        "toppen": 1,
        "overlever": 1,
        "lyfter": 1,
    }
    negative_terms = {
        "lamnar": 2,
        "skadad": 2,
        "skada": 2,
        "missar": 1,
        "kris": 2,
        "forlust": 2,
        "sparken": 2,
        "avslutar": 1,
        "tapp": 1,
        "oro": 1,
        "svag": 1,
        "problem": 1,
        "straffad": 1,
        "installd": 1,
        "tramsa": 1,
        "sluta": 1,
        "oroande": 1,
        "svagt": 1,
    }

    pos_score = 0
    neg_score = 0
    for term, weight in positive_terms.items():
        if term in t:
            pos_score += weight
    for term, weight in negative_terms.items():
        if term in t:
            neg_score += weight

    if "!" in (text or ""):
        if pos_score > neg_score:
            pos_score += 1
        elif neg_score > pos_score:
            neg_score += 1

    if pos_score == 0 and neg_score == 0:
        return "neutral", 50
    # Slightly less conservative than before: allow weak lean instead of hard-neutral.
    if pos_score == neg_score:
        return "neutral", 52

    if pos_score > neg_score:
        delta = pos_score - neg_score
        return "positive", min(95, 56 + delta * 8)
    delta = neg_score - pos_score
    return "negative", min(95, 56 + delta * 8)
def _fetch_x_recent(query: str, max_results: int):
    if not X_BEARER_TOKEN:
        return {"items": [], "error": "missing_token"}
    url = "https://api.x.com/2/tweets/search/recent"
    params = {
        "query": query,
        "max_results": max(10, min(100, max_results)),
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        if response.status_code != 200:
            return {"items": [], "error": f"x_http_{response.status_code}", "detail": response.text[:300]}
        payload = response.json()
        users = {u.get("id"): u for u in (payload.get("includes", {}).get("users", []) if isinstance(payload.get("includes", {}), dict) else [])}
        items = []
        for tweet in payload.get("data", []) or []:
            author = users.get(tweet.get("author_id"), {})
            username = author.get("username", "")
            text = tweet.get("text", "")
            sentiment_label, sentiment_score = _x_sentiment_score(text)
            items.append({
                "id": tweet.get("id"),
                "text": text,
                "created_at": tweet.get("created_at"),
                "author_name": author.get("name") or username or "okänd",
                "author_username": username,
                "url": f"https://x.com/{username}/status/{tweet.get('id')}" if username and tweet.get("id") else None,
                "lang": tweet.get("lang"),
                "public_metrics": tweet.get("public_metrics", {}),
                "source": "x",
                "sentiment_label": sentiment_label,
                "sentiment_score": sentiment_score,
            })
        return {"items": items, "error": None}
    except Exception as e:
        logging.error(f"X fetch failed: {e}")
        return {"items": [], "error": "x_fetch_failed"}


def _load_x_cache():
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(X_CACHE_BLOB)
        if not blob.exists():
            return None
        payload = json.loads(blob.download_as_text())
        return payload
    except Exception as e:
        logging.warning(f"Could not load X cache: {e}")
        return None


def _save_x_cache(payload):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(X_CACHE_BLOB)
        blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json")
    except Exception as e:
        logging.warning(f"Could not save X cache: {e}")


def _ensure_x_bq_tables(client: bigquery.Client):
    dataset_ref = bigquery.Dataset(f"{client.project}.{X_BQ_DATASET}")
    dataset_ref.location = "europe-west1"
    client.create_dataset(dataset_ref, exists_ok=True)

    posts_table_id = f"{client.project}.{X_BQ_DATASET}.{X_BQ_POSTS_TABLE}"
    posts_schema = [
        bigquery.SchemaField("fetched_at", "TIMESTAMP"),
        bigquery.SchemaField("query_mode", "STRING"),
        bigquery.SchemaField("query", "STRING"),
        bigquery.SchemaField("tweet_id", "STRING"),
        bigquery.SchemaField("created_at", "TIMESTAMP"),
        bigquery.SchemaField("text", "STRING"),
        bigquery.SchemaField("author_name", "STRING"),
        bigquery.SchemaField("author_username", "STRING"),
        bigquery.SchemaField("url", "STRING"),
        bigquery.SchemaField("lang", "STRING"),
        bigquery.SchemaField("sentiment_label", "STRING"),
        bigquery.SchemaField("sentiment_score", "INT64"),
        bigquery.SchemaField("like_count", "INT64"),
        bigquery.SchemaField("retweet_count", "INT64"),
        bigquery.SchemaField("reply_count", "INT64"),
        bigquery.SchemaField("quote_count", "INT64"),
        bigquery.SchemaField("bookmark_count", "INT64"),
        bigquery.SchemaField("impression_count", "INT64"),
    ]
    client.create_table(bigquery.Table(posts_table_id, schema=posts_schema), exists_ok=True)

    runs_table_id = f"{client.project}.{X_BQ_DATASET}.{X_BQ_RUNS_TABLE}"
    runs_schema = [
        bigquery.SchemaField("fetched_at", "TIMESTAMP"),
        bigquery.SchemaField("query_mode", "STRING"),
        bigquery.SchemaField("query", "STRING"),
        bigquery.SchemaField("count_items", "INT64"),
        bigquery.SchemaField("latest_item_age_hours", "FLOAT64"),
        bigquery.SchemaField("error", "STRING"),
        bigquery.SchemaField("from_cache", "BOOL"),
        bigquery.SchemaField("cache_minutes", "INT64"),
    ]
    client.create_table(bigquery.Table(runs_table_id, schema=runs_schema), exists_ok=True)


def _persist_x_payload_to_bq(payload: dict):
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        _ensure_x_bq_tables(bq)
        fetched_at = datetime.now(timezone.utc).isoformat()
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        query_mode = meta.get("query_mode")
        query = payload.get("query")

        post_rows = []
        for item in payload.get("items", []) or []:
            metrics = item.get("public_metrics", {}) if isinstance(item.get("public_metrics"), dict) else {}
            post_rows.append({
                "fetched_at": fetched_at,
                "query_mode": query_mode,
                "query": query,
                "tweet_id": item.get("id"),
                "created_at": item.get("created_at"),
                "text": item.get("text"),
                "author_name": item.get("author_name"),
                "author_username": item.get("author_username"),
                "url": item.get("url"),
                "lang": item.get("lang"),
                "sentiment_label": item.get("sentiment_label"),
                "sentiment_score": int(item.get("sentiment_score") or 0),
                "like_count": int(metrics.get("like_count") or 0),
                "retweet_count": int(metrics.get("retweet_count") or 0),
                "reply_count": int(metrics.get("reply_count") or 0),
                "quote_count": int(metrics.get("quote_count") or 0),
                "bookmark_count": int(metrics.get("bookmark_count") or 0),
                "impression_count": int(metrics.get("impression_count") or 0),
            })

        run_row = {
            "fetched_at": fetched_at,
            "query_mode": query_mode,
            "query": query,
            "count_items": int(payload.get("count") or 0),
            "latest_item_age_hours": meta.get("latest_item_age_hours"),
            "error": meta.get("error"),
            "from_cache": bool(meta.get("from_cache")),
            "cache_minutes": int(meta.get("cache_minutes") or 0),
        }

        if post_rows:
            posts_table_id = f"{bq.project}.{X_BQ_DATASET}.{X_BQ_POSTS_TABLE}"
            errors = bq.insert_rows_json(posts_table_id, post_rows)
            if errors:
                logging.warning(f"Failed inserting x_posts rows: {errors[:1]}")

        runs_table_id = f"{bq.project}.{X_BQ_DATASET}.{X_BQ_RUNS_TABLE}"
        run_errors = bq.insert_rows_json(runs_table_id, [run_row])
        if run_errors:
            logging.warning(f"Failed inserting x_fetch_runs row: {run_errors[:1]}")
    except Exception as e:
        logging.warning(f"Could not persist X payload to BigQuery: {e}")


def _cache_is_fresh(cache_payload):
    if not cache_payload:
        return False
    generated_at = cache_payload.get("meta", {}).get("generated_at")
    if not generated_at:
        return False
    dt = _safe_date(generated_at)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(minutes=X_CACHE_MINUTES)


def _latest_item_age_hours(items):
    if not items:
        return None
    latest = None
    for item in items:
        dt = _safe_date(item.get("created_at"))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if latest is None or dt > latest:
            latest = dt
    if latest is None:
        return None
    return (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0


def _has_item_from_today_utc(items):
    today = datetime.now(timezone.utc).date()
    for item in items:
        dt = _safe_date(item.get("created_at"))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc).date() == today:
            return True
    return False


def _build_x_ai_summary(items):
    if not X_AI_ENABLED:
        return {"enabled": False, "summary": "", "model": None, "error": "disabled"}
    if not GEMINI_API_KEY:
        return {"enabled": True, "summary": "", "model": X_AI_MODEL, "error": "missing_api_key"}
    if not items:
        return {"enabled": True, "summary": "Inga relevanta inlägg just nu.", "model": X_AI_MODEL, "error": None}
    top = items[:20]
    compact_lines = []
    for i, item in enumerate(top, 1):
        compact_lines.append(f"{i}. @{item.get('author_username','okand')}: {item.get('text','')[:220]}")
    prompt = (
        "Du analyserar ett svenskt socialt flöde om Björklöven.\n"
        "Skriv en kort sammanfattning på svenska (max 90 ord):\n"
        "1) Övergripande ton\n"
        "2) Viktigaste ämnen\n"
        "3) En tydlig risk eller möjlighet.\n"
        "Hitta inte på fakta utanför inläggen.\n\n"
        "Inlägg:\n" + "\n".join(compact_lines)
    )
    def fallback_summary():
        positives = sum(1 for i in items if i.get("sentiment_label") == "positive")
        negatives = sum(1 for i in items if i.get("sentiment_label") == "negative")
        neutrals = sum(1 for i in items if i.get("sentiment_label") == "neutral")
        top = sorted(items, key=lambda i: (i.get("public_metrics", {}).get("like_count", 0) + i.get("public_metrics", {}).get("retweet_count", 0) * 2), reverse=True)[:2]
        topics = ", ".join([f"@{t.get('author_username','okänd')}" for t in top]) if top else "inga tydliga toppsignaler"
        tone = "övervägande neutral" if neutrals >= max(positives, negatives) else ("övervägande positiv" if positives > negatives else "övervägande negativ")
        return (
            f"Flödet är {tone}. Positiva signaler: {positives}, negativa: {negatives}, neutrala: {neutrals}. "
            f"Mest synliga konton just nu: {topics}. Fokus ligger främst på truppsnack, rykten och SHL-uppladdning."
        )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{X_AI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 220}}
        res = requests.post(url, json=body, timeout=25)
        if res.status_code != 200:
            return {"enabled": True, "summary": fallback_summary(), "model": X_AI_MODEL, "error": f"gemini_http_{res.status_code}"}
        payload = res.json()
        parts = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = " ".join([p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]).strip()
        if len(text) < 60:
            text = fallback_summary()
        return {"enabled": True, "summary": text.strip(), "model": X_AI_MODEL, "error": None}
    except Exception as e:
        logging.warning(f"Gemini X summary failed: {e}")
        return {"enabled": True, "summary": fallback_summary(), "model": X_AI_MODEL, "error": "gemini_failed"}


def _x_apply_batch_llm_sentiment(items):
    """
    Classify sentiment for many tweets in one LLM call.
    Falls back silently to heuristic labels already present on items.
    """
    if not items:
        return items, {"enabled": False, "model": None, "error": "no_items"}
    if not X_AI_ENABLED:
        return items, {"enabled": False, "model": None, "error": "disabled"}
    if not GEMINI_API_KEY:
        return items, {"enabled": True, "model": X_AI_MODEL, "error": "missing_api_key"}

    top = items[:60]
    lines = []
    for item in top:
        tid = str(item.get("id") or "")
        text = (item.get("text") or "").replace("\n", " ").strip()
        text = text[:280]
        lines.append(f"{tid}\t{text}")

    prompt = (
        "You are classifying Swedish hockey tweets for sentiment.\n"
        "Return ONLY valid JSON as an array.\n"
        "Each element must be: {\"id\":\"<tweet_id>\",\"label\":\"positive|neutral|negative\",\"score\":0-100}.\n"
        "Use conservative labels. If uncertain, use neutral.\n\n"
        "Tweets:\n" + "\n".join(lines)
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{X_AI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1200,
                "response_mime_type": "application/json",
            },
        }
        res = requests.post(url, json=body, timeout=30)
        if res.status_code != 200:
            return items, {"enabled": True, "model": X_AI_MODEL, "error": f"gemini_http_{res.status_code}"}

        payload = res.json()
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = " ".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
        if not raw:
            return items, {"enabled": True, "model": X_AI_MODEL, "error": "empty_response"}

        # Remove code fences if present.
        raw = raw.replace("```json", "").replace("```", "").strip()
        arr = None
        # 1) Direct JSON parse (array or object)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                arr = parsed
            elif isinstance(parsed, dict):
                for key in ("items", "results", "tweets", "classifications"):
                    if isinstance(parsed.get(key), list):
                        arr = parsed.get(key)
                        break
        except Exception:
            pass

        # 2) Fallback: extract first JSON array substring
        if arr is None:
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    arr = json.loads(raw[start:end + 1])
                except Exception:
                    arr = None

        # 3) Fallback: JSON lines
        if arr is None:
            rows = []
            for line in raw.splitlines():
                line = line.strip().rstrip(",")
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
            if rows:
                arr = rows

        if not isinstance(arr, list):
            return items, {"enabled": True, "model": X_AI_MODEL, "error": "invalid_json_shape"}

        by_id = {}
        for row in arr:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            label = str(row.get("label") or "").lower()
            score = int(row.get("score") or 50)
            if not rid or label not in ("positive", "neutral", "negative"):
                continue
            score = max(0, min(100, score))
            by_id[rid] = (label, score)

        updated = 0
        out = []
        for item in items:
            rid = str(item.get("id") or "")
            if rid in by_id:
                label, score = by_id[rid]
                item = dict(item)
                item["sentiment_label"] = label
                item["sentiment_score"] = score
                updated += 1
            out.append(item)

        return out, {"enabled": True, "model": X_AI_MODEL, "error": None, "updated": updated}
    except Exception as e:
        logging.warning(f"Gemini batch sentiment failed: {e}")
        return items, {"enabled": True, "model": X_AI_MODEL, "error": "gemini_failed"}


def _build_x_payload(query: str, max_results: int):
    fetched = _fetch_x_recent(query, max_results)
    items = fetched.get("items", [])
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for item in items:
        counts[item.get("sentiment_label", "neutral")] = counts.get(item.get("sentiment_label", "neutral"), 0) + 1
    total = len(items) or 1
    ai_summary = _build_x_ai_summary(items)
    payload = {
        "query": query,
        "count": len(items),
        "items": items,
        "sentiment_summary": {
            "positive": counts["positive"],
            "neutral": counts["neutral"],
            "negative": counts["negative"],
            "positive_pct": round((counts["positive"] / total) * 100, 1),
            "negative_pct": round((counts["negative"] / total) * 100, 1),
        },
        "ai_summary": ai_summary,
        "meta": {
            "provider": "x_api_v2_recent_search",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "error": fetched.get("error"),
            "cache_minutes": X_CACHE_MINUTES,
            "ai_summary_ready": bool(ai_summary.get("summary")),
        }
    }
    return payload


def _build_x_payload_with_fallback(max_results: int):
    primary = _build_x_payload(X_QUERY_DEFAULT, max_results)
    primary_items = primary.get("items", []) or []
    official = _build_x_payload(X_QUERY_OFFICIAL_DEFAULT, max_results)
    official_items = official.get("items", []) or []
    primary_age_hours = _latest_item_age_hours(primary_items)
    needs_fallback = (
        len(primary_items) == 0
        or not _has_item_from_today_utc(primary_items)
        or (primary_age_hours is not None and primary_age_hours > 24)
    )
    fallback_items = []
    fallback_error = None
    if needs_fallback:
        fallback = _build_x_payload(X_QUERY_BROAD_DEFAULT, max_results)
        fallback_items = fallback.get("items", []) or []
        fallback_error = fallback.get("meta", {}).get("error")

    merged = []
    seen = set()
    for item in official_items + primary_items + fallback_items:
        item_id = item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        merged.append(item)

    merged.sort(
        key=lambda x: _safe_date(x.get("created_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    merged = merged[:max_results]

    # Single batch LLM call for sentiment classification (cost-efficient).
    merged, sentiment_meta = _x_apply_batch_llm_sentiment(merged)

    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for item in merged:
        counts[item.get("sentiment_label", "neutral")] = counts.get(item.get("sentiment_label", "neutral"), 0) + 1
    total = len(merged) or 1

    ai_summary = _build_x_ai_summary(merged)
    payload = {
        "query": X_QUERY_DEFAULT,
        "count": len(merged),
        "items": merged,
        "sentiment_summary": {
            "positive": counts["positive"],
            "neutral": counts["neutral"],
            "negative": counts["negative"],
            "positive_pct": round((counts["positive"] / total) * 100, 1),
            "negative_pct": round((counts["negative"] / total) * 100, 1),
        },
        "ai_summary": ai_summary,
        "meta": {
            "provider": "x_api_v2_recent_search",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "error": primary.get("meta", {}).get("error") or fallback_error or official.get("meta", {}).get("error"),
            "cache_minutes": X_CACHE_MINUTES,
            "ai_summary_ready": bool(ai_summary.get("summary")),
            "query_mode": "fallback_merged" if needs_fallback else "primary_plus_official",
            "official_query": X_QUERY_OFFICIAL_DEFAULT,
            "sentiment_model": sentiment_meta.get("model"),
            "sentiment_batch_error": sentiment_meta.get("error"),
            "sentiment_batch_updated": sentiment_meta.get("updated", 0),
        },
    }
    return payload


@app.get("/api/v1/x-feed")
def get_x_feed(force_refresh: bool = Query(False)):
    cached = _load_x_cache()
    if not force_refresh and _cache_is_fresh(cached):
        # Do not serve "fresh" cache if content itself is stale.
        cached_items = cached.get("items", []) if isinstance(cached, dict) else []
        latest_age_hours = _latest_item_age_hours(cached_items)
        if latest_age_hours is None or latest_age_hours <= 12:
            cached.setdefault("meta", {})
            cached["meta"]["from_cache"] = True
            cached["meta"]["latest_item_age_hours"] = latest_age_hours
            return JSONResponse(
                content=cached,
                headers={"Cache-Control": "no-store, max-age=0, must-revalidate"},
            )
    payload = _build_x_payload_with_fallback(X_MAX_RESULTS_DEFAULT)
    payload.setdefault("meta", {})
    payload["meta"]["from_cache"] = False
    payload["meta"]["latest_item_age_hours"] = _latest_item_age_hours(payload.get("items", []))
    _persist_x_payload_to_bq(payload)
    _save_x_cache(payload)
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0, must-revalidate"},
    )


@app.get("/api/v1/lovenlaget")
def get_lovenlaget_snapshot():
    """
    Startsides-snapshot for nya frontenden.
    Returnerar komprimerade signaler med konsekvenstext + meta/freshness.
    """

    def _derive_latest_impact_meaning(title: str, tag: str | None = None, impact_level: str | None = None) -> str:
        t = (title or "").lower()
        tg = (tag or "").upper()
        lvl = (impact_level or "").lower()

        staff_keywords = [
            "materialforvaltare", "material", "fysioterapeut", "fystranare",
            "assisterande", "tranare", "coach", "sportchef", "ledare", "stab",
        ]
        if any(k in t for k in staff_keywords):
            return "Detta ar en organisationsforandring i staben, inte en direkt forandring av lagbalansen pa isen."

        if tg == "BEKRÄFTAT_NYFÖRVÄRV" or "nyforvarv" in t or "klar for" in t or "ansluter" in t:
            return "Detta forstarker truppen direkt och paverkar konkurrensen om istid."
        if tg == "BEKRÄFTAD_FÖRLUST" or "lamnar" in t:
            return "Detta oppnar en lucka i truppen och kan krava ersattare eller ny rollfordelning."
        if tg == "KONTRAKTSFÖRLÄNGNING" or "forlanger" in t:
            return "Detta skapar kontinuitet och minskar osakerheten i lagbygget."
        if tg == "HETT_RYKTE" or "rykte" in t:
            return "Detta ar ett rykte och ska foljas, men ar inte tillrackligt for att andra lagbilden an."

        if lvl == "high":
            return "Detta ar en viktig signal som kan paverka lagbygget pa kort sikt."
        if lvl == "low":
            return "Detta ar en mindre signal med begransad direkt effekt pa lagbygget."
        return "Detta ar en relevant signal att bevaka i den lopande helhetsbilden."

    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        table_fqn = f"`{bq_client.project}.{BQ_DATASET}.{BQ_LOVENLAGET_TABLE}`"
        query = f"""
            select *
            from {table_fqn}
            order by snapshot_at desc
            limit 1
        """
        rows = list(bq_client.query(query).result())
        if rows:
            row = rows[0]
            latest_title = row.get("latest_impact_title") or "Inga nya signaler ännu"
            latest_level = row.get("latest_impact_level") or "medium"
            latest_meaning = _derive_latest_impact_meaning(latest_title, impact_level=latest_level)
            return {
                "title": "Lövenläget",
                "season": "2026/2027",
                "league": row.get("league") or "SHL",
                "readiness": {
                    "score": int(row.get("readiness_score") or 0),
                    "summary": row.get("readiness_summary") or "",
                },
                "critical_now": [
                    row.get("critical_1") or "",
                    row.get("critical_2") or "",
                    row.get("critical_3") or "",
                ],
                "latest_impact": {
                    "title": latest_title,
                    "impact_level": latest_level,
                    "meaning": latest_meaning,
                },
                "squad_status": {
                    "goalies": row.get("goalies_status") or "bevaka",
                    "defense": row.get("defense_status") or "bevaka",
                    "centers": row.get("centers_status") or "bevaka",
                    "forwards": row.get("forwards_status") or "bevaka",
                },
                "economy_status": {
                    "risk_level": row.get("economy_risk_level") or "okand",
                    "budget_pressure": row.get("economy_budget_pressure") or "okand",
                    "next_question": row.get("economy_next_question") or "Vad behover prioriteras nu?",
                },
                "meta": {
                    "schema_version": row.get("schema_version") or "v1",
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "source_updated_at": row.get("source_updated_at").isoformat() if row.get("source_updated_at") else None,
                    "freshness_status": row.get("freshness_status") or "unknown",
                    "new_signals": int(row.get("new_signals") or 0),
                    "scraped_articles": int(row.get("scraped_articles") or 0),
                    "expiring_contracts": int(row.get("expiring_contracts") or 0),
                },
            }
    except Exception as e:
        logging.warning(f"Kunde inte läsa mart_lovenlaget_snapshot från BigQuery, fallback till heuristik: {e}")

    silly = get_silly_season()
    meta = silly.get("_meta", {})
    source_updated_at = meta.get("lastRefresh")
    freshness_status = compute_freshness_status(source_updated_at)

    roster = silly.get("roster", [])
    departures = silly.get("confirmed_departures", [])
    signings = silly.get("confirmed_signings", [])
    expiring = silly.get("expiring_contracts", [])

    gk_count = sum(1 for p in roster if p.get("pos") == "GK")
    d_count = sum(1 for p in roster if p.get("pos") in ("LD", "RD"))
    c_count = sum(1 for p in roster if p.get("pos") == "CE")
    fw_count = sum(1 for p in roster if p.get("pos") in ("LW", "RW", "CE"))

    readiness_score = max(45, min(90, 62 + len(signings) * 2 - max(0, len(departures) - 5)))
    critical_now = [
        "Toppback saknas",
        "Centerdjup osakert" if c_count < 4 else "Centerdjup behover spets",
        "Ekonomiskt tryck maste bevakas",
    ]

    latest_signal = None
    if silly.get("news_feed"):
        latest_signal = silly["news_feed"][0]

    latest_title = latest_signal.get("title") if latest_signal else "Inga nya signaler ännu"
    latest_tag = latest_signal.get("tag") if latest_signal else None
    latest_level = "high" if len(departures) > len(signings) else "medium"

    return {
        "title": "Lövenläget",
        "season": silly.get("season", "2026/2027"),
        "league": silly.get("league", "SHL"),
        "readiness": {
            "score": readiness_score,
            "summary": "Nara, men tva luckor kan sanka bygget.",
        },
        "critical_now": critical_now,
        "latest_impact": {
            "title": latest_title,
            "impact_level": latest_level,
            "meaning": _derive_latest_impact_meaning(latest_title, tag=latest_tag, impact_level=latest_level) if latest_signal else "Vi väntar på nya verifierade signaler.",
        },
        "squad_status": {
            "goalies": "stabilt" if gk_count >= 2 else "bevaka",
            "defense": "kritisk lucka" if d_count < 8 else "bevaka",
            "centers": "bevaka" if c_count < 4 else "stabilt",
            "forwards": "stabilt" if fw_count >= 10 else "bevaka",
        },
        "economy_status": {
            "risk_level": "medel",
            "budget_pressure": "hogt",
            "next_question": "Har klubben rad med tva spetsnamn?",
        },
        "meta": {
            "schema_version": "v1",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "source_updated_at": source_updated_at,
            "freshness_status": freshness_status,
            "new_signals": meta.get("newArticles", 0),
            "scraped_articles": meta.get("scrapedArticles", 0),
            "expiring_contracts": len(expiring),
            "last_24h": meta.get("last24h") or {
                "new_signals": 0,
                "articles_24h": 0,
                "signings": 0,
                "departures": 0,
                "extensions": 0,
                "rumors": 0,
                "critical_open": len(critical_now),
            },
        },
    }
@app.get("/api/v1/financials")
def get_financials():
    """
    Return current financial dashboard rows when available.
    Falls back to lightweight economy status so UI is never empty.
    """
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        raw_fqn = f"`{bq_client.project}.{BQ_FINANCIALS_RAW_DATASET}.{BQ_FINANCIALS_RAW_TABLE}`"
        raw_sql = f"""
            select *
            from {raw_fqn}
            order by financial_year desc, entity
        """
        raw_rows = [dict(r.items()) for r in bq_client.query(raw_sql).result()]
        if raw_rows:
            return {
                "status": "ok",
                "source": "bigquery_raw",
                "table": f"{BQ_FINANCIALS_RAW_DATASET}.{BQ_FINANCIALS_RAW_TABLE}",
                "count": len(raw_rows),
                "items": raw_rows,
            }

        table_fqn = f"`{bq_client.project}.{BQ_DATASET}.{BQ_FINANCIALS_TABLE}`"
        rows = [dict(r.items()) for r in bq_client.query(f"select * from {table_fqn}").result()]
        if rows:
            return {
                "status": "ok",
                "source": "bigquery",
                "table": BQ_FINANCIALS_TABLE,
                "count": len(rows),
                "items": rows,
            }
    except Exception as e:
        logging.warning(f"Kunde inte lÃ¤sa {BQ_FINANCIALS_TABLE} frÃ¥n BigQuery: {e}")

    # Fallback so frontend never gets an empty economy section.
    return {
        "status": "ok",
        "source": "fallback",
        "table": BQ_FINANCIALS_TABLE,
        "count": 1,
        "items": [
            {
                "team_id": "IFB",
                "season_id": "sr_season_2026_2027_shl",
                "reporting_period": "latest",
                "confidence_level": "low",
                "revenue_total": None,
                "operating_result": None,
                "cash": None,
                "debt": None,
                "risk_level": "medel",
                "budget_pressure": "hÃ¶g",
                "next_question": "Har klubben rÃ¥d med tvÃ¥ spetsnamn?",
            }
        ],
    }

# @app.get("/api/v1/games/{game_id}/momentum")
# def get_momentum(game_id: str):
#     # Anropa BigQuery hÃ¤r
#     pass


