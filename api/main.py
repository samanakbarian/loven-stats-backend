import os
import json
import logging
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from google.cloud import bigquery
from datetime import datetime, timezone, timedelta

import re
from silly_season_data import SILLY_SEASON_BASELINE

app = FastAPI(
    title="Löven Stats Hub API",
    description="Backend API for Löven Stats Hub, serving data from BigQuery & GCS",
    version="1.0.0"
)

# Tillåt CORS för frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Byt till Netlify-domänen i produktion
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "loven-stats-raw-data-prod")
BQ_PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "loven_marts")
BQ_LOVENLAGET_TABLE = os.environ.get("BQ_LOVENLAGET_TABLE", "mart_lovenlaget_snapshot")
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_QUERY_DEFAULT = os.environ.get(
    "X_QUERY_DEFAULT",
    '(Björklöven OR Bjorkloven OR #Björklöven OR #Bjorkloven) -is:retweet -is:reply lang:sv'
)
X_MAX_RESULTS_DEFAULT = int(os.environ.get("X_MAX_RESULTS_DEFAULT", "30"))
X_QUERY_BROAD_DEFAULT = os.environ.get(
    "X_QUERY_BROAD_DEFAULT",
    '((Björklöven OR Bjorkloven OR #Björklöven OR #Bjorkloven OR Löven OR #Löven) (hockey OR SHL OR allsvenskan OR nyförvärv OR förlänger OR lämnar OR silly)) -is:retweet -is:reply lang:sv'
)
X_CACHE_BLOB = os.environ.get("X_CACHE_BLOB", "derived/x_feed/latest.json")
X_CACHE_MINUTES = int(os.environ.get("X_CACHE_MINUTES", "60"))
X_AI_ENABLED = os.environ.get("X_AI_ENABLED", "false").lower() == "true"
X_AI_MODEL = os.environ.get("X_AI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Welcome to Löven Stats Hub API"}

@app.get("/api/v1/health")
def health_check():
    return {"status": "healthy"}


@app.get("/api/v1/statistics")
def get_statistics_snapshot(team_query: str = Query(default="ifb,bjo,björklöven,bjorkloven,if björklöven")):
    """
    Returns Swehockey snapshot from raw_sports tables.
    Serves both league-wide stats and Björklöven-specific data.
    """
    try:
        bq_client = bigquery.Client(project=BQ_PROJECT_ID or None)
        tokens = [t.strip().lower() for t in str(team_query or "").split(",") if t.strip()]
        if not tokens:
            tokens = ["ifb", "bjo", "björklöven", "bjorkloven", "if björklöven"]

        def _matches(value: str) -> bool:
            v = (value or "").strip().lower()
            for token in tokens:
                if len(token) <= 3:
                    if v == token:
                        return True
                else:
                    if token in v:
                        return True
            return False

        def _query_all(table_name: str):
            """Return ALL rows from the table (no MAX(scraped_at) filter)."""
            q = f"""
            SELECT * FROM `{bq_client.project}.raw_sports.{table_name}`
            """
            return [dict(row.items()) for row in bq_client.query(q).result()]

        # HA regular season = 18266, HA playoff = 19979
        HA_REGULAR = 18266
        HA_PLAYOFF = 19979

        all_players = _query_all("swehockey_player_stats")
        all_goalies = _query_all("swehockey_goalie_stats")
        standings = _query_all("swehockey_standings")
        schedule = _query_all("swehockey_schedule")

        # Split players by season type
        regular_players = [p for p in all_players if p.get("season_group_id") == HA_REGULAR]
        playoff_players = [p for p in all_players if p.get("season_group_id") == HA_PLAYOFF]
        regular_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_REGULAR]
        playoff_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_PLAYOFF]

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

        # Team games — sorted by date descending
        team_games = sorted(
            [m for m in schedule if _matches(str(m.get("home_team", ""))) or _matches(str(m.get("away_team", "")))],
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

        latest_times = []
        for rows in (all_players, all_goalies, standings, schedule):
            for row in rows:
                sa = row.get("scraped_at")
                if sa:
                    latest_times.append(str(sa))

        return {
            "status": "ok",
            "source": "swehockey",
            "season": "HockeyAllsvenskan 2025/26",
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


@app.get("/api/v1/analytics")
def get_analytics():
    """
    Compute derived analytics from existing BQ data.
    Returns 8 analysis modules for the frontend.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        proj = bq.project

        # ── Load all source data ──
        def q(sql):
            return [dict(r.items()) for r in bq.query(sql).result()]

        schedule = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_schedule` ORDER BY match_date")
        players = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = 18266")
        goalies = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = 18266")
        standings = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_standings`")
        events = q(f"SELECT * FROM `{proj}.raw_sports.swehockey_game_events`")

        BJK_NAMES = ["IF Björklöven", "Björklöven"]
        BJK_CODES = ["IFB"]

        def is_bjk(name):
            return any(b.lower() in (name or "").lower() for b in BJK_NAMES + BJK_CODES)

        def bjk_game(g):
            return is_bjk(g.get("home_team", "")) or is_bjk(g.get("away_team", ""))

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

        # ── Module 1: Season Timeline ──
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

        # ── Module 2: Home vs Away ──
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

        # ── Module 3: Period Analysis ──
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

        # ── Module 4: Head-to-Head ──
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

        # ── Module 5: Form Curve (Rolling 10) ──
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

        # ── Module 6: Streak Analysis ──
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

        # ── Module 7: Player Impact ──
        bjk_players = [p for p in players if (p.get("team_code") or "").upper() in BJK_CODES]
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
        for p in bjk_players:
            gp = p.get("games_played") or 1
            g_pg = round((p.get("goals", 0) / gp), 3)
            a_pg = round((p.get("assists", 0) / gp), 3)
            p_pg = round((p.get("points", 0) / gp), 3)
            pim_pg = round((p.get("pim", 0) / gp), 3)
            player_impact.append({
                "name": p.get("player_name", ""),
                "position": p.get("position", ""),
                "number": p.get("jersey_number", 0),
                "gp": gp,
                "goals": p.get("goals", 0),
                "assists": p.get("assists", 0),
                "points": p.get("points", 0),
                "g_per_gp": g_pg,
                "a_per_gp": a_pg,
                "p_per_gp": p_pg,
                "pim_per_gp": pim_pg,
                "plus_minus": str(p.get("plus_minus", "")),
                "vs_league": {
                    "ppg_diff": round(p_pg - avg_ppg, 3),
                    "gpg_diff": round(g_pg - avg_gpg, 3),
                },
            })
        player_impact.sort(key=lambda x: -x["p_per_gp"])

        # ── Module 8: Goalie Radar ──
        bjk_goalies = [g for g in goalies if (g.get("team_code") or "").upper() in BJK_CODES]
        all_goalies_min10 = sorted([g for g in goalies if (g.get("games_played") or 0) >= 10],
                                    key=lambda g: -(g.get("save_pct") or 0))

        def percentile(value, all_vals):
            if not all_vals:
                return 50
            below = sum(1 for v in all_vals if v <= value)
            return round((below / len(all_vals)) * 100)

        sv_vals = [g.get("save_pct", 0) for g in all_goalies_min10]
        gaa_vals = [g.get("gaa", 0) for g in all_goalies_min10]
        wp_vals = [g.get("win_pct", 0) for g in all_goalies_min10]

        goalie_radar = []
        for g in bjk_goalies:
            gp = g.get("games_played") or 1
            goalie_radar.append({
                "name": g.get("goalie_name", ""),
                "gp": gp,
                "sv_pct": g.get("save_pct", 0),
                "gaa": g.get("gaa", 0),
                "shutouts": g.get("shutouts", 0),
                "wins": g.get("wins", 0),
                "losses": g.get("losses", 0),
                "win_pct": g.get("win_pct", 0),
                "saves_per_gp": round((g.get("saves", 0) / gp), 1),
                "percentiles": {
                    "sv_pct": percentile(g.get("save_pct", 0), sv_vals),
                    "gaa": 100 - percentile(g.get("gaa", 0), gaa_vals),  # lower is better
                    "win_pct": percentile(g.get("win_pct", 0), wp_vals),
                },
            })

        # ── PP/PK from game events ──
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
            "total_pim": sum(e.get("penalty_minutes", 0) for e in events if (e.get("team_code") or "").upper() in BJK_CODES),
            "avg_pim_per_game": round(sum(e.get("penalty_minutes", 0) for e in events if (e.get("team_code") or "").upper() in BJK_CODES) / max(len(bjk_games), 1), 1),
        }

        # ── Attendance ──
        home_games = [g for g in bjk_games if is_bjk(g.get("home_team", ""))]
        specs = [g.get("spectators") for g in home_games if g.get("spectators")]
        attendance = {
            "avg": round(sum(specs) / max(len(specs), 1)) if specs else 0,
            "max": max(specs) if specs else 0,
            "min": min(specs) if specs else 0,
            "total": sum(specs) if specs else 0,
            "home_games": len(home_games),
        }

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
            },
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/analytics")
        return {"status": "error", "error": str(e)}


def normalize_title(title):
    return re.sub(r'[^\wåäö\s]', '', title.lower()).strip()


def reclassify_tag(article):
    """
    Conservative keyword-based fallback: only reclassifies ÖVRIGT articles where
    the TITLE clearly indicates a direct Björklöven transfer action.
    
    Gemini is usually right to tag things ÖVRIGT — we only override when the title
    unambiguously is about a player joining/leaving/extending with Björklöven.
    """
    tag = article.get("tag", "ÖVRIGT")
    if tag != "ÖVRIGT":
        return article
    
    title = article.get("title", "").lower()
    
    # Only reclassify based on TITLE, not body (body often mentions Björklöven in passing)
    title_mentions_bjorkloven = any(kw in title for kw in ['björklöven', 'bjorkloven'])
    # Be careful with 'löven' — too short, matches 'slöven', 'Gullöven' etc.
    # Only match ' löven' or start-of-string 'löven'
    if not title_mentions_bjorkloven:
        if title.startswith('löven') or ' löven' in title:
            title_mentions_bjorkloven = True
    
    if not title_mentions_bjorkloven:
        return article
    
    # Exclude "tidigare Björklöven-spelaren" / "ex-Björklöven" patterns (former players, not current squad)
    if any(kw in title for kw in ['tidigare', 'ex-', 'f.d.', 'före detta', 'forna']):
        return article
    
    # Now check for specific transfer actions IN THE TITLE tied to Björklöven
    # "X förlänger/förlängde med Björklöven"
    if any(kw in title for kw in ['förlänger', 'förlängde', 'förlängd']):
        article["tag"] = "KONTRAKTSFÖRLÄNGNING"
        return article
    
    # "X lämnar Björklöven" / "massflykt från Björklöven" (also handle missing spaces from HTML parsing)
    if any(phrase in title for phrase in [
        'lämnar björklöven', 'lämnarbjörklöven', 'lämnar löven', 'lämnarlöven',
        'från björklöven', 'frånbjörklöven', 'från löven', 'frånlöven',
    ]):
        article["tag"] = "BEKRÄFTAD_FÖRLUST"
        return article
    
    # "X klar för Björklöven" / "X ansluter till Björklöven" / "nyförvärv"
    if any(phrase in title for phrase in [
        'klar för björklöven', 'klar förbjörklöven', 'klar för löven', 'klar förlöven',
        'ansluter till björklöven', 'ansluter tillbjörklöven', 'ansluter till löven',
    ]):
        article["tag"] = "BEKRÄFTAT_NYFÖRVÄRV"
        return article
    if 'nyförvärv' in title and title_mentions_bjorkloven:
        article["tag"] = "BEKRÄFTAT_NYFÖRVÄRV"
        return article
    
    # Don't reclassify anything else — trust Gemini's judgment
    return article

def deduplicate_articles(scraped, baseline):
    seen = set()
    for item in baseline:
        seen.add(normalize_title(item.get('title', '')))
    
    unique_scraped = []
    for item in scraped:
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
            "status": "NYFÖRVÄRV",
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
        "signings": count_tag("BEKRÄFTAT_NYFÖRVÄRV"),
        "departures": count_tag("BEKRÄFTAD_FÖRLUST"),
        "extensions": count_tag("KONTRAKTSFÖRLÄNGNING"),
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

    signings = sum(1 for i in recent if i.get("tag") == "BEKRÄFTAT_NYFÖRVÄRV")
    departures = sum(1 for i in recent if i.get("tag") == "BEKRÄFTAD_FÖRLUST")
    extensions = sum(1 for i in recent if i.get("tag") == "KONTRAKTSFÖRLÄNGNING")
    expiring = sum(1 for p in (roster or []) if p.get("status") == "UTGÅENDE")

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
    Hämtar senaste scraper-datan från GCS och mergar med baseline.
    """
    scraped_articles = []
    previous_scraped_articles = []
    last_refresh = datetime.now().isoformat()
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        # Hämta blob med prefix raw/silly_season/scraped_ sorterat på senast uppdaterad
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
        logging.error(f"Kunde inte hämta scraper-data från GCS: {e}")
        # Fortsätt med bara baseline
    
    baseline = SILLY_SEASON_BASELINE.copy()
    baseline = sync_roster_with_confirmed_signings(baseline)
    
    # Deduplicera mot baseline för presentation i feed
    deduped_for_feed = deduplicate_articles(scraped_articles, baseline.get("news_feed", []))
    # Beräkna verkligt nytt sedan förra scraper-snapshoten
    new_articles = compute_new_since_previous(scraped_articles, previous_scraped_articles)

    for i, article in enumerate(deduped_for_feed):
        article["id"] = f"scraped-{i}"
        article["scraped"] = True
        
        # Reclassify articles that Gemini incorrectly tagged as ÖVRIGT
        reclassify_tag(article)
        
        # Om tiden saknas, försök extrahera den eller sätt aktuell tid
        if "time" not in article:
            article["time"] = datetime.now().strftime("%H:%M")

    # Slå ihop och sortera fallande på datum, sedan tid
    merged_feed = deduped_for_feed + baseline.get("news_feed", [])
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
    Intern driftvy för silly-pipeline.
    Returnerar senaste snapshot-körningar från GCS utan att påverka publik UI.
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
        logging.error(f"Kunde inte läsa silly ops-data: {e}")
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
    t = (text or "").lower()
    positive = ["klar", "nyförvärv", "vinner", "förlänger", "stärker", "succé", "poängkung"]
    negative = ["lämnar", "skadad", "missar", "kris", "förlust", "sparken", "avslutar"]
    pos_hits = sum(1 for w in positive if w in t)
    neg_hits = sum(1 for w in negative if w in t)
    if pos_hits > neg_hits:
        return "positive", min(95, 55 + (pos_hits - neg_hits) * 10)
    if neg_hits > pos_hits:
        return "negative", min(95, 55 + (neg_hits - pos_hits) * 10)
    return "neutral", 50


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
    primary_age_hours = _latest_item_age_hours(primary_items)
    needs_fallback = (
        len(primary_items) == 0
        or not _has_item_from_today_utc(primary_items)
        or (primary_age_hours is not None and primary_age_hours > 24)
    )

    if not needs_fallback:
        primary.setdefault("meta", {})
        primary["meta"]["query_mode"] = "primary"
        return primary

    fallback = _build_x_payload(X_QUERY_BROAD_DEFAULT, max_results)
    fallback_items = fallback.get("items", []) or []

    merged = []
    seen = set()
    for item in primary_items + fallback_items:
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
            "error": primary.get("meta", {}).get("error") or fallback.get("meta", {}).get("error"),
            "cache_minutes": X_CACHE_MINUTES,
            "ai_summary_ready": bool(ai_summary.get("summary")),
            "query_mode": "fallback_merged",
        },
    }
    return payload


@app.get("/api/v1/x-feed")
def get_x_feed(force_refresh: bool = Query(False)):
    cached = _load_x_cache()
    if not force_refresh and _cache_is_fresh(cached):
        cached.setdefault("meta", {})
        cached["meta"]["from_cache"] = True
        return cached
    payload = _build_x_payload_with_fallback(X_MAX_RESULTS_DEFAULT)
    payload.setdefault("meta", {})
    payload["meta"]["from_cache"] = False
    _save_x_cache(payload)
    return payload


@app.get("/api/v1/lovenlaget")
def get_lovenlaget_snapshot():
    """
    Startsides-snapshot för nya frontenden.
    Returnerar komprimerade signaler med konsekvenstext + meta/freshness.
    """
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
                    "title": row.get("latest_impact_title") or "Inga nya signaler ännu",
                    "impact_level": row.get("latest_impact_level") or "medium",
                    "meaning": row.get("latest_impact_meaning") or "Vi väntar på nya verifierade signaler.",
                },
                "squad_status": {
                    "goalies": row.get("goalies_status") or "bevaka",
                    "defense": row.get("defense_status") or "bevaka",
                    "centers": row.get("centers_status") or "bevaka",
                    "forwards": row.get("forwards_status") or "bevaka",
                },
                "economy_status": {
                    "risk_level": row.get("economy_risk_level") or "okänd",
                    "budget_pressure": row.get("economy_budget_pressure") or "okänd",
                    "next_question": row.get("economy_next_question") or "Vad behöver prioriteras nu?",
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
        "Centerdjup osäkert" if c_count < 4 else "Centerdjup behöver spets",
        "Ekonomiskt tryck måste bevakas",
    ]

    latest_signal = None
    if silly.get("news_feed"):
        latest_signal = silly["news_feed"][0]

    return {
        "title": "Lövenläget",
        "season": silly.get("season", "2026/2027"),
        "league": silly.get("league", "SHL"),
        "readiness": {
            "score": readiness_score,
            "summary": "Nära, men två luckor kan sänka bygget.",
        },
        "critical_now": critical_now,
        "latest_impact": {
            "title": latest_signal.get("title") if latest_signal else "Inga nya signaler ännu",
            "impact_level": "high" if len(departures) > len(signings) else "medium",
            "meaning": "Det här flyttar nålen direkt och påverkar lagbalansen." if latest_signal else "Vi väntar på nya verifierade signaler.",
        },
        "squad_status": {
            "goalies": "stabilt" if gk_count >= 2 else "bevaka",
            "defense": "kritisk lucka" if d_count < 8 else "bevaka",
            "centers": "bevaka" if c_count < 4 else "stabilt",
            "forwards": "stabilt" if fw_count >= 10 else "bevaka",
        },
        "economy_status": {
            "risk_level": "medel",
            "budget_pressure": "högt",
            "next_question": "Har klubben råd med två spetsnamn?",
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

# @app.get("/api/v1/games/{game_id}/momentum")
# def get_momentum(game_id: str):
#     # Anropa BigQuery här
#     pass
