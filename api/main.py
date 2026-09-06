import os
import json
import logging

import eliteprospects
import random
import requests
import unicodedata
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.cloud import storage
from google.cloud import bigquery
from collections import Counter
from datetime import datetime, timezone, timedelta
import functools

from cachetools import cached, TTLCache
from cachetools.keys import hashkey

import re
from silly_season_data import SILLY_SEASON_BASELINE

app = FastAPI(
    title="LÃ¶ven Stats Hub API",
    description="Backend API for LÃ¶ven Stats Hub, serving data from BigQuery & GCS",
    version="1.0.0"
)

analytics_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
stats_cache = TTLCache(maxsize=10, ttl=21600) # 6 hours caching
silly_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching
xfeed_cache = TTLCache(maxsize=5, ttl=1800) # 30 mins caching

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


def cached_ok(cache):
    """Som @cached, men lagrar bara lyckade svar.

    Endpointsen returnerar {"status": "error"} i stallet for att kasta, sa
    @cached lagrade aven misslyckade svar. Med sex timmars TTL innebar det att
    ett ogonblickligt fel — en tabell som annu inte hunnit skapas, en timeout
    mot BigQuery — fastnade i sex timmar efter att orsaken var borta.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # refresh=1 kringgar cachen. Utan den lever ett svar i sex timmar,
            # vilket ar en evighet direkt efter en scraperkorning: modulerna
            # rakans om, men API:t fortsatter servera de gamla nollorna.
            force = bool(kwargs.pop("refresh", False))
            # Funktionens namn maste inga i nyckeln. Flera endpoints delar
            # samma cache och anropas med samma argument — get_standings,
            # get_statistics och get_players tar alla bara `season` — sa utan
            # namnet far de identisk nyckel och skriver over varandra. Den som
            # kordes forst serverades sedan for alla tre, och /api/v1/standings
            # kunde svara med spelarlistan.
            key = hashkey(fn.__qualname__, *args, **kwargs)
            if not force:
                try:
                    return cache[key]
                except KeyError:
                    pass
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("status") in ("error", "not_found"):
                return result
            cache[key] = result
            return result

        return wrapper

    return decorator


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
        sql = f"SELECT * FROM `{proj}.core.season` WHERE season_key = @key"
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", season_key)
        ])
    else:
        sql = f"""
        SELECT *
        FROM `{proj}.core.season`
        WHERE is_active = TRUE
        ORDER BY CASE WHEN league = 'SHL' THEN 0 ELSE 1 END,
                 start_date DESC,
                 season_key
        LIMIT 1
        """
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
        f"""
        SELECT *
        FROM `{bq.project}.core.season`
        ORDER BY start_date DESC,
                 CASE WHEN league = 'SHL' THEN 0 ELSE 1 END,
                 season_key
        """
    ).result()]
    active = next((r["season_key"] for r in rows if r.get("is_active")), None)

    # Flera sasonger finns bara som jamforelsedata for prognosmodellen och
    # innehaller inga Bjorkloven-matcher alls. Utan den har flaggan hamnar de
    # i frontendens sasongsvaljare och ser ut som valbara alternativ.
    team_seasons: set = set()
    try:
        season_ids = {}
        for r in rows:
            for sid in (r.get("regular_season_id"), r.get("playoff_id")):
                if sid:
                    season_ids[int(sid)] = r["season_key"]
        if season_ids:
            ids_csv = ",".join(str(i) for i in season_ids)
            hits = bq.query(
                f"""
                SELECT DISTINCT season_group_id
                FROM `{bq.project}.core.schedule`
                WHERE season_group_id IN ({ids_csv})
                  AND (LOWER(home_team) LIKE '%rkl%ven%' OR LOWER(away_team) LIKE '%rkl%ven%')
                """
            ).result()
            for h in hits:
                key = season_ids.get(int(h["season_group_id"]))
                if key:
                    team_seasons.add(key)
    except Exception:
        logging.exception("Kunde inte avgora vilka sasonger laget deltar i")
        team_seasons = set()

    return {
        "seasons": [
            {
                "key": r["season_key"],
                "name": r["season_name"],
                "league": r.get("league"),
                "is_active": r.get("is_active", False),
                # None nar kontrollen inte kunde koras, sa klienten kan skilja
                # "vet inte" fran "laget spelar inte i denna sasong".
                "has_team_data": (r["season_key"] in team_seasons) if team_seasons else None,
            }
            for r in rows
        ],
        "active": active,
    }



def clean_person(name) -> str:
    """Rensar namn som burit med sig granntexten fran Swehockeys handelsesida.

    Malcellen ser ut sa har:

        71. Possler, Gustav Pos. Part.: 10, 26, ... Neg. Part.: 16, ...

    Skorningen strippar taggarna utan mellanrum, sa sista assisten blir
    "Possler, GustavPos". Det ar inte bara fult: `/api/v1/player` matchar pa
    namn, sa en assist med paklistrat "Pos" raknas inte alls och forsvinner ur
    spelarens poangkurva.

    Ratt losning ar att skorningen slutar klistra ihop dem, men raderna som
    redan ligger i BigQuery maste rensas nagonstans, och det ar har.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    # Bara nar markoren sitter direkt efter ett gement tecken — annars kunde
    # ett riktigt namn kapas.
    text = re.sub(r"(?<=[a-zaaoAAO\u00e0-\u00ff])(?:Pos|Neg)\.?(?:\s*Part\.?:?.*)?$", "", text)
    return text.strip().rstrip(",").strip()



BJK_HOME = re.compile(r"bj[oö]rkl[oö]ven", re.IGNORECASE)


def _to_float(value):
    """Swehockey blandar punkt och komma, och tomma celler ar strangar."""
    try:
        return round(float(str(value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def _is_ours(team_code) -> bool:
    """Bjorklovens lagkod i Swehockeys handelser ar IFB."""
    low = str(team_code or "").lower()
    return "ifb" in low or "rkl" in low or "kloven" in low or "klöven" in low


def parse_period_results(pr):
    """'(2-1, 0-1, 1-2)' -> [{period, home_gf, away_gf}].

    Lag pa modulniva. Den var tidigare definierad inuti get_analytics, men
    get_projection anvander den ocksa — och foll darfor pa NameError. Hela
    slutplaceringsmodellen svarade
    {"status": "error", "error": "name 'parse_period_results' is not defined"}.
    """
    if not pr:
        return []
    pr = pr.strip("() ")
    periods = []
    for i, part in enumerate(pr.split(","), 1):
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)", part.strip())
        if m:
            periods.append({"period": i, "home_gf": int(m.group(1)), "away_gf": int(m.group(2))})
    return periods


def clean_penalty_type(detail) -> str:
    """'Crosschecking(10:07 - 12:07)' -> 'Crosschecking'.

    Tidsintervallet star redan som utvisningens tid och minuter.
    """
    text = str(detail or "").strip()
    text = re.sub(r"\s*\(\s*\d{1,3}:\d{2}\s*-\s*\d{1,3}:\d{2}\s*\)\s*$", "", text)
    return text.strip()


@app.get("/api/v1/standings")
@cached_ok(cache=stats_cache)
def get_standings(season: str = None, refresh: bool = False):
    """Hela serietabellen for vald sasong.

    Tabellen fanns tidigare bara inbakad i /api/v1/statistics, och da enbart
    som lagets egen rad. Frontend behover alla lag for att kunna visa en
    tabell alls.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular_id = active["regular"]

        rows = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.standings` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.standings`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                ORDER BY a.rank
                """
            ).result()
        ]

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "count": len(rows),
            "standings": rows,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/standings")
        return {"status": "error", "error": str(e), "standings": []}


@app.get("/api/v1/statistics")
@cached_ok(cache=stats_cache)
def get_statistics_snapshot(season: str = None, team_query: str = Query(default="ifb,bjo,bjÃ¶rklÃ¶ven,bjorkloven,if bjÃ¶rklÃ¶ven"), refresh: bool = False):
    """
    Ogonblicksbild ur core-vyerna, som ar avduplicerade.
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
            """Rader ur en core-vy, filtrerade pa sasongsgrupp."""
            if not season_ids:
                return []
            ids_str = ",".join(str(sid) for sid in season_ids if sid)
            q = f"""
            SELECT a.* FROM `{bq_client.project}.core.{table_name}` a
            INNER JOIN (
                SELECT season_group_id, MAX(scraped_at) as max_scraped
                FROM `{bq_client.project}.core.{table_name}`
                WHERE season_group_id IN ({ids_str})
                GROUP BY season_group_id
            ) b ON a.season_group_id = b.season_group_id AND a.scraped_at = b.max_scraped
            """
            return [dict(row.items()) for row in bq_client.query(q).result()]

        # Lookup season
        active = lookup_season(season)
        HA_REGULAR = active["regular"]
        HA_PLAYOFF = active.get("playoff")
        season_ids = list(set([sid for sid in [HA_REGULAR, HA_PLAYOFF] if sid]))

        all_players = _query_season("player_season_stats", season_ids)
        all_goalies = _query_season("goalie_season_stats", season_ids)
        standings = _query_season("standings", season_ids)
        schedule = _query_season("schedule", season_ids)

        # Split players by season type
        regular_players = [p for p in all_players if p.get("season_group_id") == HA_REGULAR]
        playoff_players = [p for p in all_players if p.get("season_group_id") == HA_PLAYOFF] if HA_PLAYOFF else []
        regular_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_REGULAR]
        playoff_goalies = [g for g in all_goalies if g.get("season_group_id") == HA_PLAYOFF] if HA_PLAYOFF else []

        # BJK-specific data
        bjk_skaters_regular = sorted(
            [p for p in regular_players if _matches(str(p.get("team_code", "")))],
            key=lambda p: (-int(p.get("points") or 0), -int(p.get("goals") or 0),
                           str(p.get("player_name") or "")),
        )
        bjk_skaters_playoff = sorted(
            [p for p in playoff_players if _matches(str(p.get("team_code", "")))],
            key=lambda p: (-int(p.get("points") or 0), -int(p.get("goals") or 0),
                           str(p.get("player_name") or "")),
        )
        bjk_goalies_regular = sorted(
            [g for g in regular_goalies if _matches(str(g.get("team_code", "")))],
            key=lambda g: (-int(g.get("games_played") or 0),
                           -float(g.get("save_pct") or 0), str(g.get("goalie_name") or "")),
        )

        # League-wide top scorers (regular season)
        top_scorers = sorted(
            regular_players,
            key=lambda p: (-int(p.get("points") or 0), -int(p.get("goals") or 0),
                           str(p.get("player_name") or "")),
        )[:25]
        top_goalies = sorted(
            regular_goalies,
            key=lambda g: (-int(g.get("games_played") or 0),
                           -float(g.get("save_pct") or 0), str(g.get("goalie_name") or "")),
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
            key=lambda g: (str(g.get("date", "") or g.get("match_date", "")),
                           str(g.get("game_id") or "")),
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



@app.get("/api/v1/roster")
@cached_ok(cache=stats_cache)
def get_roster(season: str = None, team: str = "björklöven", refresh: bool = False):
    """Truppen med Swehockey som sanningskalla.

    Trupplistan lag tidigare bara i SILLY_SEASON_BASELINE, en handunderhallen
    dict som slutade uppdateras 2026-06-13 och darmed missade sommarens
    varvningar. Swehockeys PlayersByTeam listar hela den registrerade truppen
    med trojnummer sa fort klubben anmalt den, och skrapas nu veckovis.

    Kontraktsuppgifterna (status, kontraktslangd, alder) finns bara i den
    manuella listan och laggs pa som berikning dar namnen matchar. Saknas de
    visas spelaren anda — truppen blir aldrig fel bara for att kontraktsdatan
    slapar efter.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = [sid for sid in [active["regular"], active.get("playoff")] if sid]
        if not season_ids:
            return {"status": "error", "error": "Sasongen saknar id.", "players": []}

        ids_csv = ",".join(str(s) for s in season_ids)
        rows = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.roster` a
                INNER JOIN (
                    SELECT season_group_id, MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.roster`
                    WHERE season_group_id IN ({ids_csv})
                    GROUP BY season_group_id
                ) b
                  ON a.season_group_id = b.season_group_id
                 AND a.scraped_at = b.max_s
                WHERE a.season_group_id IN ({ids_csv})
                  AND LOWER(a.team_name) LIKE @team
                ORDER BY a.jersey_number
                """,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("team", "STRING", f"%{team.lower()}%")
                    ]
                ),
            ).result()
        ]

        def _key(name: str) -> str:
            raw = str(name or "").strip().lower()
            if "," in raw:
                last, first = [p.strip() for p in raw.split(",", 1)]
                raw = f"{first} {last}"
            n = unicodedata.normalize("NFKD", raw)
            n = "".join(ch for ch in n if not unicodedata.combining(ch))
            return re.sub(r"[^a-z ]", "", n).strip()

        def _display(name: str) -> str:
            if "," in str(name or ""):
                last, first = [p.strip() for p in str(name).split(",", 1)]
                return f"{first} {last}"
            return str(name or "").strip()

        def _surname_initial(name: str) -> str:
            """Efternamn + forsta bokstaven i fornamnet.

            Reservnyckel nar exakt namnmatchning missar. Stavningen skiljer sig
            mellan kallorna — "Chris DiDomenico" mot "Didomenico, Christopher",
            "Lucas" mot "Lukas" — men efternamn plus initial ar unikt nog inom
            en trupp pa 25 och ger inga felmatchningar i praktiken.
            """
            parts = _key(name).split()
            if len(parts) < 2:
                return ""
            return f"{parts[-1]}|{parts[0][:1]}"

        contracts = {}
        contracts_loose = {}
        for p in SILLY_SEASON_BASELINE.get("roster", []):
            name = p.get("name", "")
            contracts[_key(name)] = p
            loose = _surname_initial(name)
            if loose:
                # Krockar lamnas utanfor hellre an att gissa fel.
                contracts_loose[loose] = None if loose in contracts_loose else p

        players = []
        for r in rows:
            raw_name = r.get("player_name", "")
            c = contracts.get(_key(raw_name))
            if c is None:
                c = contracts_loose.get(_surname_initial(raw_name))
            players.append(
                {
                    "name": _display(r.get("player_name")),
                    "jersey_number": r.get("jersey_number"),
                    "position": r.get("position"),
                    "games_played": r.get("games_played", 0),
                    "goals": r.get("goals", 0),
                    "assists": r.get("assists", 0),
                    "points": r.get("points", 0),
                    "pim": r.get("pim", 0),
                    "plus_minus": r.get("plus_minus", 0),
                    # Berikning; None nar kontraktsdatan inte kanner spelaren.
                    "status": (c or {}).get("status"),
                    "contract_until": (c or {}).get("contractUntil"),
                    "age": (c or {}).get("age"),
                    "note": (c or {}).get("note"),
                    "has_contract_info": bool(c),
                }
            )

        # Direktlank till varje spelares EP-sida. Uppslaget cachas bade i
        # minnet och i BigQuery, sa det kostar nagot enstaka anrop per sasong.
        for name, link in eliteprospects.links_for(players, bq=bq).items():
            for pl in players:
                if pl["name"] == name:
                    pl["eliteprospects"] = link

        scraped = max((str(r.get("scraped_at")) for r in rows if r.get("scraped_at")), default=None)

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "team": rows[0]["team_name"] if rows else None,
            "count": len(players),
            "source": "swehockey",
            "roster_scraped_at": scraped,
            "contract_data_updated": SILLY_SEASON_BASELINE.get("last_manual_update"),
            "contract_matches": sum(1 for p in players if p["has_contract_info"]),
            "players": players,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/roster")
        return {"status": "error", "error": str(e), "players": []}


@app.get("/api/v1/players")
@cached_ok(cache=stats_cache)
def get_players(season: str = None, refresh: bool = False):
    """Lagets utespelare med percentil mot hela serien.

    Percentilen kraver hela ligans fordelning — over 700 spelare — och den
    berakningen hor hemma har, dar raderna redan finns i minnet, i stallet for
    att skickas till klienten.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular_id = active["regular"]

        rows = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.player_season_stats` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.player_season_stats`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                """
            ).result()
        ]

        # Spelare med nagon enstaka match ger brus i fordelningen.
        MIN_GP = 10
        pool = [r for r in rows if int(r.get("games_played") or 0) >= MIN_GP]

        def pct(field: str, value: float, higher_is_better: bool = True) -> int:
            vals = [float(r.get(field) or 0) for r in pool]
            if not vals:
                return 0
            below = sum(1 for v in vals if (v < value if higher_is_better else v > value))
            return round(below / len(vals) * 100)

        def is_bjk(code: str) -> bool:
            return "ifb" in str(code or "").lower() or "rkl" in str(code or "").lower()

        players = []
        for r in sorted(rows, key=lambda x: (-int(x.get("points") or 0),
                                            str(x.get("player_name") or ""))):
            if not is_bjk(r.get("team_code")):
                continue
            gp = int(r.get("games_played") or 0)
            pts = int(r.get("points") or 0)
            players.append(
                {
                    "name": r.get("player_name"),
                    "jersey_number": r.get("jersey_number"),
                    "position": r.get("position"),
                    "games_played": gp,
                    "goals": int(r.get("goals") or 0),
                    "assists": int(r.get("assists") or 0),
                    "points": pts,
                    "pim": int(r.get("pim") or 0),
                    "plus_minus": int(r.get("plus_minus") or 0),
                    "points_per_game": round(pts / gp, 2) if gp else 0,
                    # Percentil mot alla i serien med minst MIN_GP matcher.
                    "percentiles": None
                    if gp < MIN_GP
                    else {
                        "points": pct("points", pts),
                        "goals": pct("goals", int(r.get("goals") or 0)),
                        "assists": pct("assists", int(r.get("assists") or 0)),
                        "plus_minus": pct("plus_minus", int(r.get("plus_minus") or 0)),
                        # Fa utvisningsminuter ar battre, sa skalan vands.
                        "pim": pct("pim", int(r.get("pim") or 0), higher_is_better=False),
                    },
                }
            )

        for name, link in eliteprospects.links_for(players, bq=bq).items():
            for pl in players:
                if pl["name"] == name:
                    pl["eliteprospects"] = link

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "league_players": len(rows),
            "percentile_pool": len(pool),
            "min_games_for_percentile": MIN_GP,
            "count": len(players),
            "players": players,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/players")
        return {"status": "error", "error": str(e), "players": []}


def _goalie_profile(bq, keeper: dict, active: dict, season_ids: str, wanted: str) -> dict:
    """Malvaktens sasong, med exakt speltid ur matchrapporten.

    GAA raknas pa verklig istid, inte pa antal matcher. En malvakt som byts
    ut efter en period har inte spelat en match, och skillnaden ar stor: en
    utbytt malvakt drog tidigare med sig hela matchens langd i namnaren.
    """
    log = list(keeper.get("game_log") or [])
    toi: dict[int, dict] = {}
    try:
        for r in bq.query(
            f"""
            SELECT game_id, time_on_ice, shutout
            FROM `{bq.project}.marts.fact_goalie_game`
            WHERE season_group_id IN ({season_ids}) AND player_key = @who
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("who", "STRING", keeper.get("name"))
            ]),
        ).result():
            d = dict(r.items())
            toi[int(d["game_id"])] = d
    except Exception:
        logging.info("Ingen speltid for malvakten %s", keeper.get("name"), exc_info=True)

    def _minutes(text) -> float | None:
        try:
            mm, ss = str(text or "").split(":")[:2]
            return int(mm) + int(ss) / 60
        except Exception:
            return None

    total_minutes = 0.0
    for i, g in enumerate(log, 1):
        extra = toi.get(int(g.get("game_id") or 0)) or {}
        minutes = _minutes(extra.get("time_on_ice"))
        g["game_number"] = i
        g["time_on_ice"] = extra.get("time_on_ice")
        g["shutout"] = bool(extra.get("shutout"))
        if minutes:
            total_minutes += minutes

    saves = int(keeper.get("saves") or 0)
    against = int(keeper.get("goals_against") or 0)
    shots = int(keeper.get("shots_against") or 0)
    profile = dict(keeper)
    profile.update({
        "save_pct": round(100 * saves / shots, 2) if shots else None,
        # Med speltid blir GAA exakt. Utan den faller vi tillbaka pa matcher,
        # och da ska det sagas — inte doljas bakom samma etikett.
        "gaa": round(against * 60 / total_minutes, 2) if total_minutes else keeper.get("gaa"),
        "gaa_basis": "speltid" if total_minutes else "matcher",
        "minutes": round(total_minutes) if total_minutes else None,
    })
    return {
        "status": "ok",
        "role": "goalie",
        "season": active["name"],
        "season_key": active["key"],
        "player": profile,
        "game_log": log,
        "games_with_points": 0,
        "points_from_events": 0,
    }


@app.get("/api/v1/player/{name}")
@cached_ok(cache=stats_cache)
def get_player(name: str, season: str = None, refresh: bool = False):
    """En spelares sasong, match for match.

    Loggen kommer ur marts.fact_player_game och tacker ALLA matcher spelaren
    var med i, inte bara de dar han fick poang. Tidigare byggdes den ur
    malhandelserna, sa en 57-poangare fick 34 rader av 51 och en back med
    femton poang fick nastan ingenting. Nollmatcherna ar halva bilden.

    Matchrapporten bidrar med skott, tekningar och Swehockeys officiella
    plus/minus. Den saknas for sasongens forsta matcher, sa raderna bar
    has_report: "noll skott" och "ingen rapport" ar inte samma sak.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular_id = active["regular"]
        season_ids = ",".join(str(sid) for sid in {regular_id, active.get("playoff")} if sid)

        def _key(n: str) -> str:
            raw = str(n or "").strip().lower()
            if "," in raw:
                last, first = [p.strip() for p in raw.split(",", 1)]
                raw = f"{first} {last}"
            x = unicodedata.normalize("NFKD", raw)
            x = "".join(c for c in x if not unicodedata.combining(c))
            return re.sub(r"[^a-z ]", "", x).strip()

        wanted = _key(name)

        # Malvakter far ett eget svar. En utespelarlogg med noll skott och noll
        # skjutprocent sager ingenting om en malvakt, och tack vare speltiden i
        # matchrapporten gar GAA att rakna exakt i stallet for att uppskattas.
        keepers = get_goalies(season=season)
        keeper = next((g for g in keepers.get("goalies", []) if _key(g.get("name")) == wanted), None)
        if keeper:
            return _goalie_profile(bq, keeper, active, season_ids, wanted)

        season_stats = get_players(season=season)
        me = next((p for p in season_stats.get("players", []) if _key(p["name"]) == wanted), None)
        if not me:
            return {"status": "not_found", "name": name, "error": "Spelaren finns inte i truppens statistik."}

        rows = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT f.game_id, f.player_key, f.team_key,
                       f.goals, f.assists, f.points, f.pim, f.penalties,
                       f.gf_on, f.ga_on, f.plus_minus_on_ice,
                       f.shots, f.official_plus_minus,
                       f.faceoffs_won, f.faceoffs_lost, f.faceoff_pct,
                       f.has_report, f.in_lineup,
                       g.match_date, g.home_team_key, g.away_team_key,
                       g.home_goals, g.away_goals, g.went_beyond_regulation, g.venue
                FROM `{bq.project}.marts.fact_player_game` f
                INNER JOIN `{bq.project}.marts.dim_game` g USING (game_id)
                WHERE f.season_group_id IN ({season_ids})
                  AND REGEXP_CONTAINS(f.team_key, r'(?i)bj[oö]rkl[oö]ven')
                ORDER BY g.match_date, f.game_id
                """
            ).result()
        ]
        mine = [r for r in rows if _key(r.get("player_key")) == wanted]
        if not mine:
            # Kan handa fore forsta matchen, eller for en spelare som finns i
            # sasongstabellen men inte i nagon uppstallning vi hamtat.
            return {
                "status": "ok", "season": active["name"], "season_key": active["key"],
                "player": me, "game_log": [], "games_with_points": 0,
                "points_from_events": 0,
                "note": "Ingen matchlogg för spelaren i den här säsongen.",
            }

        log, running = [], 0
        for i, r in enumerate(mine, 1):
            home = bool(BJK_HOME.search(str(r.get("home_team_key") or "")))
            gf = r.get("home_goals") if home else r.get("away_goals")
            ga = r.get("away_goals") if home else r.get("home_goals")
            running += int(r.get("points") or 0)
            log.append({
                "game_number": i,
                "game_id": r.get("game_id"),
                "date": str(r.get("match_date") or "")[:10],
                "opponent": r.get("away_team_key") if home else r.get("home_team_key"),
                "is_home": home,
                "goals_for": gf,
                "goals_against": ga,
                "result": ("W" if (gf or 0) > (ga or 0) else "L"),
                "beyond_regulation": bool(r.get("went_beyond_regulation")),
                "goals": int(r.get("goals") or 0),
                "assists": int(r.get("assists") or 0),
                "points": int(r.get("points") or 0),
                "cumulative_points": running,
                "pim": int(r.get("pim") or 0),
                "shots": r.get("shots"),
                "official_plus_minus": r.get("official_plus_minus"),
                "plus_minus_on_ice": int(r.get("plus_minus_on_ice") or 0),
                "gf_on": int(r.get("gf_on") or 0),
                "ga_on": int(r.get("ga_on") or 0),
                "faceoffs_won": r.get("faceoffs_won"),
                "faceoffs_lost": r.get("faceoffs_lost"),
                "has_report": bool(r.get("has_report")),
                "in_lineup": bool(r.get("in_lineup")),
            })

        # Situationer och kedjekompisar ur malhandelserna. Rapporten sager hur
        # manga poang, handelserna sager i vilket lage och med vem.
        events = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT e.game_id, e.time, e.period, e.team_code, e.score_state,
                       e.player_name, e.assist1_name, e.assist2_name,
                       e.is_power_play, e.is_short_handed, e.is_empty_net,
                       e.home_goals, e.away_goals, e.event_index
                FROM `{bq.project}.core.game_events` e
                WHERE e.season_group_id IN ({season_ids}) AND e.event_type = 'goal'
                ORDER BY e.game_id, e.event_index
                """
            ).result()
        ]

        situations = {"power_play": 0, "even_strength": 0, "short_handed": 0,
                      "game_winning": 0, "first_goal_of_game": 0, "empty_net": 0}
        assisted_by = Counter()   # vem som lade fram at spelaren
        assists_to = Counter()    # vem spelaren lade fram at
        first_seen: set[int] = set()
        our_goals_by_game: dict[int, list[dict]] = {}
        for e in events:
            if _is_ours(e.get("team_code")):
                our_goals_by_game.setdefault(e["game_id"], []).append(e)

        for gid, goals in our_goals_by_game.items():
            for e in goals:
                scorer = _key(clean_person(e.get("player_name")))
                a1 = _key(clean_person(e.get("assist1_name")))
                a2 = _key(clean_person(e.get("assist2_name")))
                if wanted not in (scorer, a1, a2):
                    continue
                if scorer == wanted:
                    if e.get("is_short_handed"):
                        situations["short_handed"] += 1
                    elif e.get("is_power_play"):
                        situations["power_play"] += 1
                    else:
                        situations["even_strength"] += 1
                    if e.get("is_empty_net"):
                        situations["empty_net"] += 1
                    if gid not in first_seen and goals and goals[0] is e:
                        situations["first_goal_of_game"] += 1
                        first_seen.add(gid)
                    for other in (clean_person(e.get("assist1_name")),
                                  clean_person(e.get("assist2_name"))):
                        if other:
                            assisted_by[other] += 1
                else:
                    who = clean_person(e.get("player_name"))
                    if who:
                        assists_to[who] += 1

        # Matchavgorande mal: sista malet for det vinnande laget som andrade
        # stallningen till en ledning laget behold.
        for row in log:
            if row["result"] != "W":
                continue
            goals = our_goals_by_game.get(row["game_id"]) or []
            needed = (row["goals_against"] or 0) + 1
            winner = next((g for g in goals
                           if max(int(g.get("home_goals") or 0), int(g.get("away_goals") or 0)) == needed
                           and min(int(g.get("home_goals") or 0), int(g.get("away_goals") or 0)) < needed), None)
            if winner and _key(clean_person(winner.get("player_name"))) == wanted:
                situations["game_winning"] += 1

        # Sviter over ALLA matcher, inte bara de med poang — det ar poangen
        # med en fullstandig logg.
        best = cur = 0
        worst = dry = 0
        for row in log:
            if row["points"] > 0:
                cur += 1
                best = max(best, cur)
                dry = 0
            else:
                cur = 0
                dry += 1
                worst = max(worst, dry)
        current_streak = 0
        for row in reversed(log):
            if row["points"] > 0:
                current_streak += 1
            else:
                break

        def _sum(rows_, field):
            vals = [r[field] for r in rows_ if r.get(field) is not None]
            return sum(vals) if vals else None

        home_rows = [r for r in log if r["is_home"]]
        away_rows = [r for r in log if not r["is_home"]]

        def _split(rows_):
            return {
                "games": len(rows_),
                "goals": sum(r["goals"] for r in rows_),
                "assists": sum(r["assists"] for r in rows_),
                "points": sum(r["points"] for r in rows_),
                "shots": _sum(rows_, "shots"),
                "plus_minus_on_ice": sum(r["plus_minus_on_ice"] for r in rows_),
            }

        # Skjutprocenten far bara raknas over matcher som HAR en rapport.
        # Sasongens alla mal delat med skotten fran halva sasongen gav 91 %.
        with_report = [r for r in log if r["has_report"]]
        shots_total = _sum(with_report, "shots")
        goals_in_reported = sum(r["goals"] for r in with_report)
        fw = _sum(with_report, "faceoffs_won")
        fl = _sum(with_report, "faceoffs_lost")

        player = dict(me)
        player.update({
            "shots": shots_total,
            "shooting_pct": round(100 * goals_in_reported / shots_total, 1) if shots_total else None,
            "goals_in_reported_games": goals_in_reported,
            "faceoffs_won": fw,
            "faceoffs_lost": fl,
            "faceoff_pct": round(100 * fw / (fw + fl), 1) if (fw or fl) else None,
            "official_plus_minus_sum": _sum(log, "official_plus_minus"),
            "plus_minus_on_ice": sum(r["plus_minus_on_ice"] for r in log),
        })

        # Biografin ur trupprapporten: alder, kaptensbindel, detaljerad position.
        try:
            bio = next(iter(bq.query(
                f"""
                SELECT birthdate, age, is_captain, is_assistant_captain, detailed_position
                FROM `{bq.project}.marts.dim_player`
                WHERE player_key = @who
                """,
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("who", "STRING", mine[0]["player_key"])
                ]),
            ).result()), None)
            if bio:
                player.update({k: v for k, v in dict(bio.items()).items() if v is not None})
        except Exception:
            logging.info("Ingen biografi for %s", name, exc_info=True)

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "player": player,
            "game_log": log,
            "games_with_points": sum(1 for r in log if r["points"] > 0),
            "points_from_events": sum(r["points"] for r in log),
            "splits": {"home": _split(home_rows), "away": _split(away_rows)},
            "situations": situations,
            "streaks": {
                "current_points": current_streak,
                "longest_points": best,
                "longest_drought": worst,
            },
            "linemates": {
                "assisted_by": [{"name": n, "count": c} for n, c in assisted_by.most_common(6)],
                "assists_to": [{"name": n, "count": c} for n, c in assists_to.most_common(6)],
            },
            "report_coverage": {
                "games_with_report": len(with_report),
                "games_total": len(log),
            },
        }
    except Exception as e:
        logging.exception("get_player misslyckades")
        return {"status": "error", "name": name, "error": str(e), "game_log": []}


@app.get("/api/v1/projection")
@cached_ok(cache=analytics_cache)
def get_projection(season: str = None, sims: int = 5000, refresh: bool = False):
    """Slutplacering simulerad match for match over det som aterstar.

    Elo raknas ur spelade matcher, och de matcher som inte spelats simuleras
    med utfallssannolikheter ur ratingskillnaden. Varje simulering ger en hel
    sluttabell; over manga simuleringar blir andelen av dem dar laget hamnar
    pa en viss plats sannolikheten for den placeringen.

    Poangen foljer svensk praxis: tre for vinst i ordinarie tid, tva for vinst
    efter forlangning, ett for forlust efter forlangning. Hur ofta en match gar
    till forlangning hamtas ur sasongens egna matcher i stallet for att antas.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular_id = active["regular"]

        schedule = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.home_team, a.away_team, a.result, a.period_results, a.match_date
                FROM `{bq.project}.core.schedule` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.schedule`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                ORDER BY a.match_date
                """
            ).result()
        ]
        standings = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.team_name, a.points, a.games_played, a.rank
                FROM `{bq.project}.core.standings` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.standings`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                """
            ).result()
        ]
        if not standings or not schedule:
            return {"status": "not_found", "error": "Tabell eller spelschema saknas.", "teams": []}

        K, HFA = 20, 40
        elo = {str(s.get("team_name")): 1500.0 for s in standings}
        remaining: list[tuple[str, str]] = []
        ot_games = played = 0

        for g in schedule:
            home, away = str(g.get("home_team") or ""), str(g.get("away_team") or "")
            if not home or not away:
                continue
            elo.setdefault(home, 1500.0)
            elo.setdefault(away, 1500.0)

            m = re.match(r"(\d+)\s*-\s*(\d+)", str(g.get("result") or "").strip())
            if not m:
                remaining.append((home, away))
                continue

            played += 1
            hg, ag = int(m.group(1)), int(m.group(2))
            is_ot = len(parse_period_results(g.get("period_results", ""))) > 3
            ot_games += 1 if is_ot else 0

            if hg > ag:
                s_home = 1.0 if not is_ot else 0.65
            elif hg < ag:
                s_home = 0.0 if not is_ot else 0.35
            else:
                s_home = 0.5
            e_home = 1 / (1 + 10 ** ((elo[away] - (elo[home] + HFA)) / 400))
            elo[home] += K * (s_home - e_home)
            elo[away] += K * ((1 - s_home) - (1 - e_home))

        # Hur ofta matcher gar till forlangning hamtas ur sasongen sjalv;
        # utan spelade matcher far ett normalvarde duga.
        ot_rate = (ot_games / played) if played >= 20 else 0.20

        base = {str(s.get("team_name")): int(s.get("points") or 0) for s in standings}
        teams = sorted(base)
        idx = {t: i for i, t in enumerate(teams)}

        games = [(idx[h], idx[a]) for h, a in remaining if h in idx and a in idx]

        # Arbetet vaxer med bade simuleringar och kvarvarande matcher. Taket
        # haller svarstiden nere nar en hel sasong aterstar, utan att strypa
        # precisionen i slutet nar det bara ar nagra matcher kvar.
        n = max(200, min(int(sims or 5000), 20000))
        if games:
            n = max(1500, min(n, 1_500_000 // len(games)))

        # Ratingen ar skattad, inte kand, och en simulering som latsas annat
        # ger for smala intervall. En dragning per lag och simulering later
        # osakerheten sla igenom. Sigma ar kalibrerat mot HA 25/26 vid fyra
        # tidpunkter: utan den hamnade 9,8 av 14 slutresultat inom p10-p90,
        # med sigma 55 blir det 11,2 — vilket ar vad ett attioprocentigt
        # intervall ska ge. Se docs/SWEHOCKEY_STATS_SCRAPER.md.
        RATING_SIGMA = 55.0

        start = [base.get(t, 0) for t in teams]
        elo_base = [elo.get(t, 1500.0) for t in teams]
        rank_counts = [[0] * len(teams) for _ in teams]
        totals = [[] for _ in teams]
        rnd = random.Random(20260919)

        for _ in range(n):
            drawn = [e + rnd.gauss(0, RATING_SIGMA) for e in elo_base]
            pts = start[:]
            for hi, ai in games:
                p_home = 1 / (1 + 10 ** ((drawn[ai] - (drawn[hi] + HFA)) / 400))
                if rnd.random() < ot_rate:
                    # Forlangning: vinnaren far tva poang, forloraren ett.
                    if rnd.random() < p_home:
                        pts[hi] += 2
                        pts[ai] += 1
                    else:
                        pts[ai] += 2
                        pts[hi] += 1
                elif rnd.random() < p_home:
                    pts[hi] += 3
                else:
                    pts[ai] += 3

            order = sorted(range(len(teams)), key=lambda i: -pts[i])
            for place, i in enumerate(order, 1):
                rank_counts[i][place - 1] += 1
                totals[i].append(pts[i])

        def _pct(v: int) -> float:
            return round(v / n * 100, 1)

        def _quantile(values: list[int], q: float) -> int:
            ordered = sorted(values)
            return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

        out = []
        for t in teams:
            i = idx[t]
            counts = rank_counts[i]
            vals = totals[i]
            expected = sum((place + 1) * c for place, c in enumerate(counts)) / n
            out.append(
                {
                    "team": t,
                    "is_bjk": bool(BJK_HOME.search(t)),
                    "elo": round(elo.get(t, 1500)),
                    "current_points": base.get(t, 0),
                    "expected_points": round(sum(vals) / n, 1),
                    "points_p10": _quantile(vals, 0.10),
                    "points_p50": _quantile(vals, 0.50),
                    "points_p90": _quantile(vals, 0.90),
                    "expected_rank": round(expected, 1),
                    "rank_p10": _quantile([r for r in range(1, len(teams) + 1) for _ in range(counts[r - 1])], 0.10),
                    "rank_p90": _quantile([r for r in range(1, len(teams) + 1) for _ in range(counts[r - 1])], 0.90),
                    "top6_pct": _pct(sum(counts[:6])),
                    "top10_pct": _pct(sum(counts[:10])),
                    "bottom2_pct": _pct(sum(counts[-2:])),
                    "win_league_pct": _pct(counts[0]),
                    "rank_distribution": [_pct(c) for c in counts],
                }
            )
        out.sort(key=lambda x: x["expected_rank"])

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "teams_in_league": len(teams),
            "games_played": played,
            "games_remaining": len(games),
            "simulations": n,
            "rating_sigma": RATING_SIGMA,
            "ot_rate_pct": round(ot_rate * 100, 1),
            # Innan sasongen borjat delar alla lag rating, och siffrorna sager
            # da bara nagot om spelschemat. Sag det i stallet for att lata dem
            # se ut som en prognos.
            "reliability": "none" if played == 0 else "low" if played < len(teams) * 4 else "ok",
            "teams": out,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/projection")
        return {"status": "error", "error": str(e), "teams": []}


@app.get("/api/v1/shots")
@cached_ok(cache=stats_cache)
def get_shots(season: str = None, refresh: bool = False):
    """Skottandel och PDO, match for match och over sasongen.

    Skotten star bara i matchsidans sammanfattning och finns i
    swehockey_game_summary. Med bada lagens rader per match gar det att rakna
    andelen av skotten, och PDO — skjutprocent plus raddningsprocent.

    PDO ar det narmaste vi kommer ett turmatt. Runt 100 ar normalt; ett lag
    som ligger klart hogre har haft mer tur an spel, och over tid dras det mot
    100. Skottandelen sager det motsatta: den beskriver vem som styrde
    matchen, oavsett vad pucken gjorde.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = ",".join(str(sid) for sid in {active["regular"], active.get("playoff")} if sid)

        try:
            rows = [
                dict(r.items())
                for r in bq.query(
                    f"""
                    SELECT a.*
                    FROM `{bq.project}.core.game_team_summary` a
                    INNER JOIN (
                        SELECT game_id, MAX(scraped_at) AS max_s
                        FROM `{bq.project}.core.game_team_summary`
                        WHERE season_group_id IN ({season_ids})
                        GROUP BY game_id
                    ) b ON a.game_id = b.game_id AND a.scraped_at = b.max_s
                    WHERE a.season_group_id IN ({season_ids})
                    ORDER BY a.match_date
                    """
                ).result()
            ]
        except Exception:
            # Tabellen finns forst efter en skorning med den nya scrapern.
            return {"status": "not_found", "error": "Skottdata saknas for sasongen.", "game_log": []}

        # Bada lagens rader hor ihop per match.
        per_game: dict[int, dict] = {}
        for r in rows:
            per_game.setdefault(int(r["game_id"]), {})["home" if r.get("is_home") else "away"] = r

        def _pct(part: float, whole: float) -> float | None:
            return round(part / whole * 100, 2) if whole else None

        log = []
        for gid, sides in per_game.items():
            us_home = BJK_HOME.search(str((sides.get("home") or {}).get("team_name") or ""))
            us = sides.get("home") if us_home else sides.get("away")
            them = sides.get("away") if us_home else sides.get("home")
            if not us or not them:
                continue
            sf = int(us.get("shots") or 0)
            sa = int(them.get("shots") or 0)
            # Malen gar att harleda: motstandarens malvakt raddade en del av
            # vara skott, resten blev mal.
            gf = sf - int(them.get("saves") or 0)
            ga = sa - int(us.get("saves") or 0)
            log.append(
                {
                    "game_id": gid,
                    "date": str(us.get("match_date") or ""),
                    "is_home": bool(us_home),
                    "opponent": them.get("team_name"),
                    "shots_for": sf,
                    "shots_against": sa,
                    "shot_share_pct": _pct(sf, sf + sa),
                    "goals_for": gf,
                    "goals_against": ga,
                    "shooting_pct": _to_float(us.get("shooting_pct")),
                    "save_pct": _to_float(us.get("save_pct")),
                    "pdo": _to_float(us.get("pdo")),
                }
            )
        log.sort(key=lambda g: g["date"])

        def _agg(sel: list[dict]) -> dict:
            sf = sum(g["shots_for"] for g in sel)
            sa = sum(g["shots_against"] for g in sel)
            gf = sum(g["goals_for"] for g in sel)
            ga = sum(g["goals_against"] for g in sel)
            shooting = _pct(gf, sf)
            saving = _pct(sa - ga, sa)
            return {
                "games": len(sel),
                "shots_for": sf,
                "shots_against": sa,
                "shots_for_per_game": round(sf / len(sel), 1) if sel else None,
                "shots_against_per_game": round(sa / len(sel), 1) if sel else None,
                "shot_share_pct": _pct(sf, sf + sa),
                "goals_for": gf,
                "goals_against": ga,
                "shooting_pct": shooting,
                "save_pct": saving,
                # Sasongens PDO raknas ur totalerna, inte som ett snitt av
                # matchernas — en match med fa skott ska inte vaga lika tungt.
                "pdo": round(shooting + saving, 2) if shooting is not None and saving is not None else None,
            }

        # Rullande fonster: enskilda matcher svanger for mycket for att saga
        # nagot, det ar riktningen over tid som ar intressant.
        WINDOW = 10
        rolling = []
        for i in range(len(log)):
            window = log[max(0, i - WINDOW + 1) : i + 1]
            agg = _agg(window)
            rolling.append(
                {
                    "date": log[i]["date"],
                    "match": i + 1,
                    "window": len(window),
                    "pdo": agg["pdo"],
                    "shot_share_pct": agg["shot_share_pct"],
                }
            )

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "games": len(log),
            "totals": _agg(log),
            "home": _agg([g for g in log if g["is_home"]]),
            "away": _agg([g for g in log if not g["is_home"]]),
            "rolling": rolling,
            "game_log": log,
            "window": WINDOW,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/shots")
        return {"status": "error", "error": str(e), "game_log": []}


@app.get("/api/v1/goalies")
@cached_ok(cache=stats_cache)
def get_goalies(season: str = None, refresh: bool = False):
    """Malvakterna: sasongstotaler, match for match och hemma mot borta.

    Sasongstabellen ger totaler men inte vem som stod i vilken match. Den
    uppgiften finns bara i matchernas sammanfattning, som scrapern lagger i
    swehockey_game_goalies — utan den gar det varken att rita en formkurva
    eller att se vem som stod nar det small.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular_id = active["regular"]
        season_ids = ",".join(str(sid) for sid in {regular_id, active.get("playoff")} if sid)

        totals = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.goalie_season_stats` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.goalie_season_stats`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                  AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
                """
            ).result()
        ]

        try:
            log_rows = [
                dict(r.items())
                for r in bq.query(
                    f"""
                    SELECT a.*
                    FROM `{bq.project}.core.game_goalies` a
                    INNER JOIN (
                        SELECT game_id, MAX(scraped_at) AS max_s
                        FROM `{bq.project}.core.game_goalies`
                        WHERE season_group_id IN ({season_ids})
                        GROUP BY game_id
                    ) b ON a.game_id = b.game_id AND a.scraped_at = b.max_s
                    WHERE a.season_group_id IN ({season_ids})
                      AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
                    ORDER BY a.match_date
                    """
                ).result()
            ]
        except Exception:
            # Tabellen finns forst efter en skorning med den nya scrapern.
            log_rows = []

        def _key(n) -> str:
            return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(n or "").lower()))

        by_goalie: dict[str, list[dict]] = {}
        for r in log_rows:
            by_goalie.setdefault(_key(r.get("goalie_name")), []).append(r)

        def _side(rows: list[dict], home: bool | None) -> dict:
            sel = [
                r for r in rows
                if home is None or bool(BJK_HOME.search(str(r.get("home_team") or ""))) == home
            ]
            shots = sum(int(r.get("shots_against") or 0) for r in sel)
            saves = sum(int(r.get("saves") or 0) for r in sel)
            return {
                "games": len(sel),
                "shots_against": shots,
                "saves": saves,
                "goals_against": shots - saves,
                "save_pct": round(saves / shots * 100, 2) if shots else None,
            }

        goalies = []
        for t in sorted(totals, key=lambda x: (-int(x.get("games_played") or 0),
                                              str(x.get("goalie_name") or ""))):
            name = t.get("goalie_name")
            rows = by_goalie.get(_key(name), [])
            goalies.append(
                {
                    "name": name,
                    # Malvaktstabellen lamnar trojnumret tomt; matchloggen har det.
                    "jersey_number": t.get("jersey_number")
                    or next((r.get("goalie_number") for r in rows if r.get("goalie_number")), None),
                    "games_played": int(t.get("games_played") or 0),
                    "wins": int(t.get("wins") or 0),
                    "losses": int(t.get("losses") or 0),
                    "shutouts": int(t.get("shutouts") or 0),
                    "goals_against": int(t.get("goals_against") or 0),
                    "shots_against": int(t.get("shots_against") or 0),
                    "saves": int(t.get("saves") or 0),
                    "save_pct": _to_float(t.get("save_pct")),
                    "gaa": _to_float(t.get("gaa")),
                    # Hemma och borta ur matchloggen; sasongstabellen delar inte upp det.
                    "home": _side(rows, True),
                    "away": _side(rows, False),
                    "game_log": [
                        {
                            "game_id": r.get("game_id"),
                            "date": str(r.get("match_date") or ""),
                            "is_home": bool(BJK_HOME.search(str(r.get("home_team") or ""))),
                            "opponent": (
                                r.get("away_team")
                                if BJK_HOME.search(str(r.get("home_team") or ""))
                                else r.get("home_team")
                            ),
                            "save_pct": _to_float(r.get("save_pct")),
                            "saves": int(r.get("saves") or 0),
                            "shots_against": int(r.get("shots_against") or 0),
                            "goals_against": int(r.get("goals_against") or 0),
                        }
                        for r in sorted(rows, key=lambda x: str(x.get("match_date") or ""))
                    ],
                }
            )

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "count": len(goalies),
            "games_with_log": len({r.get("game_id") for r in log_rows}),
            "goalies": goalies,
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/goalies")
        return {"status": "error", "error": str(e), "goalies": []}


@app.get("/api/v1/onice")
@cached_ok(cache=stats_cache)
def get_onice(season: str = None, refresh: bool = False):
    """Vem som star pa isen nar mal gors och slapps in.

    Swehockeys handelsesida listar bada lagens spelare vid varje mal, och
    scrapern lagger dem i on_ice_for och on_ice_against som trojnummer. Det ar
    ratt underlag for ett plus/minus vi raknar sjalva — till skillnad fran
    tabellens, som inte gar att bryta ner.

    Vi redovisar bade alla situationer och enbart lika styrka. Powerplaymal
    snedvrider annars bilden: den som spelar mycket i overtal far ett hogt tal
    utan att det sager nagot om spelet fem mot fem.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = ",".join(str(sid) for sid in {active["regular"], active.get("playoff")} if sid)

        # Handelsetabellen ar append-only: valj senaste skorningen per match.
        goals = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT e.game_id, e.team_code, e.score_state, e.on_ice_for, e.on_ice_against
                FROM `{bq.project}.core.game_events` e
                INNER JOIN (
                    SELECT game_id, MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.game_events`
                    WHERE season_group_id IN ({season_ids})
                    GROUP BY game_id
                ) m ON e.game_id = m.game_id AND e.scraped_at = m.max_s
                WHERE e.event_type = 'goal'
                  AND e.season_group_id IN ({season_ids})
                  AND (e.on_ice_for IS NOT NULL OR e.on_ice_against IS NOT NULL)
                """
            ).result()
        ]

        # Trojnummer -> namn. Truppen har hela laget, aven spelare utan poang.
        # Namn och tabellsiffror hamtas ur grundserien. Slutspelsgruppen har
        # egna, mycket lagre plus/minus-varden, och kom de med skrevs
        # grundseriens siffror over — Marcus Nilssons +30 blev +0.
        regular_id = active["regular"]
        roster = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.player_name, a.jersey_number, a.position, a.team_name
                FROM `{bq.project}.core.roster` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.roster`
                    WHERE season_group_id = {regular_id}
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id = {regular_id}
                """
            ).result()
        ]

        def _ours(value: str) -> bool:
            low = str(value or "").lower()
            return "ifb" in low or "rkl" in low or "kloven" in low or "klöven" in low

        by_number: dict[int, dict] = {}
        for r in roster:
            if not _ours(r.get("team_name")):
                continue
            num = r.get("jersey_number")
            if num is None:
                continue
            by_number.setdefault(int(num), {"name": r.get("player_name"), "position": r.get("position")})

        # Truppen listar bara spelare klubben registrerat. Nagon som spelat men
        # inte star dar skulle annars bli ett namnlost nummer i tabellen.
        for r in bq.query(
            f"""
            SELECT a.player_name, a.jersey_number, a.position, a.games_played
            FROM `{bq.project}.core.player_season_stats` a
            INNER JOIN (
                SELECT MAX(scraped_at) AS max_s
                FROM `{bq.project}.core.player_season_stats`
                WHERE season_group_id = {regular_id}
            ) b ON a.scraped_at = b.max_s
            WHERE a.season_group_id = {regular_id}
              AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
            -- Tva spelare kan bara samma nummer under en sasong: Lundin och
            -- malvakten Salasca Naas har bada 33. Den som spelat flest matcher
            -- far numret, annars skrev en inhoppare over ordinarien.
            ORDER BY a.games_played DESC
            """
        ).result():
            d = dict(r.items())
            num = d.get("jersey_number")
            if num is None:
                continue
            by_number.setdefault(
                int(num), {"name": d.get("player_name"), "position": d.get("position")}
            )

        # En spelare som bytt trojnummer under sasongen star i truppen bara
        # under sitt nya. Handelserna bar bade nummer och namn, sa de fyller
        # luckorna — annars blev nummer 77 en namnlos rad.
        for r in bq.query(
            f"""
            -- Ingen avduplicering behovs har: fragan grupperar och rankar
            -- bara namn per nummer, och flera skorningsgenerationer skalar
            -- alla antal lika mycket. Ordningen blir densamma.
            SELECT e.player_number, e.player_name, COUNT(*) AS n
            FROM `{bq.project}.core.game_events` e
            WHERE e.season_group_id IN ({season_ids})
              AND e.player_number IS NOT NULL
              AND e.player_name IS NOT NULL
              AND (LOWER(e.team_code) LIKE '%ifb%' OR LOWER(e.team_code) LIKE '%rkl%')
            GROUP BY e.player_number, e.player_name
            ORDER BY n DESC
            """
        ).result():
            d = dict(r.items())
            by_number.setdefault(int(d["player_number"]), {"name": d.get("player_name"), "position": None})

        def _nums(value) -> list[int]:
            return [int(n) for n in re.findall(r"\d{1,2}", str(value or ""))]

        stats: dict[int, dict] = {}
        pairs: dict[tuple[int, int], int] = {}
        games = set()
        team_gf = team_ga = 0

        for g in goals:
            games.add(g.get("game_id"))
            scored_by_us = _ours(g.get("team_code"))
            # Pos ar det gorande lagets spelare, Neg det slappande lagets.
            ours = _nums(g.get("on_ice_for") if scored_by_us else g.get("on_ice_against"))
            if not ours:
                continue
            state = str(g.get("score_state") or "").upper()
            # Plus/minus-konventionen: mal vid lika styrka och i underlage
            # raknas, powerplaymal gor det inte — for nagotdera laget. Att bara
            # rakna (EQ) gav backar som spelar mycket boxplay ett for lagt tal,
            # eftersom deras mal i underlage foll bort. `score_state` beskriver
            # det gorande lagets situation, sa regeln ar densamma at bada hall.
            even = "(EQ)" in state or "SH" in state
            if scored_by_us:
                team_gf += 1
            else:
                team_ga += 1

            for num in ours:
                slot = stats.setdefault(
                    num, {"gf_on": 0, "ga_on": 0, "gf_on_ev": 0, "ga_on_ev": 0}
                )
                if scored_by_us:
                    slot["gf_on"] += 1
                    slot["gf_on_ev"] += 1 if even else 0
                else:
                    slot["ga_on"] += 1
                    slot["ga_on_ev"] += 1 if even else 0

            if scored_by_us:
                # Malvakten star pa isen vid nastan varje mal och skulle annars
                # ta over listan over vanligaste kombinationer.
                skaters = sorted(
                    n for n in ours
                    if not str((by_number.get(n) or {}).get("position") or "").upper().startswith("G")
                )
                for i, a in enumerate(skaters):
                    for b in skaters[i + 1 :]:
                        pairs[(a, b)] = pairs.get((a, b), 0) + 1

        # Tabellens eget plus/minus, sa att bada talen kan visas sida vid sida.
        official: dict[int, int] = {}
        for r in bq.query(
            f"""
            SELECT a.jersey_number, a.plus_minus, a.games_played
            FROM `{bq.project}.core.player_season_stats` a
            INNER JOIN (
                SELECT MAX(scraped_at) AS max_s
                FROM `{bq.project}.core.player_season_stats`
                WHERE season_group_id = {regular_id}
            ) b ON a.scraped_at = b.max_s
            WHERE a.season_group_id = {regular_id}
              AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
            -- Samma nummerkrock som ovan: Lundins +25 skrevs over av
            -- reservmalvaktens 0 eftersom bada bar 33.
            ORDER BY a.games_played DESC
            """
        ).result():
            d = dict(r.items())
            num = d.get("jersey_number")
            if num is not None and int(num) not in official:
                official[int(num)] = int(d.get("plus_minus") or 0)

        players = []
        for num, slot in stats.items():
            info = by_number.get(num) or {}
            # Malvakter star pa isen vid nastan varje mal och hor inte hemma i
            # ett plus/minus for utespelare.
            position = str(info.get("position") or "")
            players.append(
                {
                    "jersey_number": num,
                    "name": info.get("name") or f"#{num}",
                    "position": position or None,
                    "is_goalie": position.upper().startswith("G"),
                    **slot,
                    "diff": slot["gf_on"] - slot["ga_on"],
                    "diff_ev": slot["gf_on_ev"] - slot["ga_on_ev"],
                    # Andel av lagets mal som spelaren var med pa.
                    "gf_share_pct": round(slot["gf_on"] / team_gf * 100, 1) if team_gf else 0,
                    "official_plus_minus": official.get(num),
                }
            )
        players.sort(key=lambda p: (-p["diff_ev"], -p["diff"], str(p.get("name") or "")))

        top_pairs = [
            {
                "numbers": [a, b],
                "names": [
                    (by_number.get(a) or {}).get("name"),
                    (by_number.get(b) or {}).get("name"),
                ],
                "goals_for": n,
            }
            for (a, b), n in sorted(pairs.items(), key=lambda kv: (-kv[1], str(kv[0])))[:12]
            if a in by_number and b in by_number
        ]

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "games_with_events": len(games),
            "team_goals_for": team_gf,
            "team_goals_against": team_ga,
            "count": len(players),
            "players": players,
            "top_pairs": top_pairs,
            # Var siffra ar inte tabellens. Swehockeys egna +/- tillskriver
            # ungefar 16 procent fler on-ice-tillfallen an deras Pos. Part.-
            # listor ger, konsekvent at bada hallen, och skillnaden gar inte
            # att harleda ur handelsesidan. Talen redovisas darfor bredvid
            # varandra i stallet for att ett av dem utges for att vara det
            # andra. Se docs/SWEHOCKEY_STATS_SCRAPER.md.
            "note": (
                "on_ice-talen raknas ur Swehockeys uppgift om vilka som stod pa "
                "isen vid varje mal. De sammanfaller inte alltid med tabellens "
                "plus/minus, som redovisas separat i official_plus_minus."
            ),
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/onice")
        return {"status": "error", "error": str(e), "players": []}


# ===========================================================================
# Vyerna fran mockupen. Allt underlag ligger redan i core — ingen ny hamtning
# fran Swehockey behovs.
# ===========================================================================


def _bjk_games(bq, season_ids: str) -> list[dict]:
    """Lagets spelade matcher med resultat och periodresultat, aldst forst."""
    return [
        dict(r.items())
        for r in bq.query(
            f"""
            SELECT game_id, match_date, home_team, away_team, result,
                   period_results, venue, spectators, stage
            FROM `{bq.project}.core.schedule`
            WHERE season_group_id IN ({season_ids})
              AND game_id IS NOT NULL
              AND REGEXP_CONTAINS(IFNULL(result, ''), r'\d+\s*-\s*\d+')
              AND (REGEXP_CONTAINS(home_team, r'(?i)bj[oö]rkl[oö]ven')
                   OR REGEXP_CONTAINS(away_team, r'(?i)bj[oö]rkl[oö]ven'))
            ORDER BY match_date, game_id
            """
        ).result()
    ]


def _score(result):
    """'3 - 1' -> (3, 1). Ingen giltig strang ger (None, None)."""
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", str(result or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _ours_theirs(row):
    """Matchen sedd fran vart hall: (vara mal, deras, motstandare, hemma)."""
    home = bool(BJK_HOME.search(str(row.get("home_team") or "")))
    h, a = _score(row.get("result"))
    if h is None:
        return None
    opponent = row.get("away_team") if home else row.get("home_team")
    return (h, a, opponent, True) if home else (a, h, opponent, False)


@app.get("/api/v1/lines")
@cached_ok(cache=stats_cache)
def get_lines(season: str = None, refresh: bool = False):
    """Femmornas utfall: mal for och emot med enheten pa isen.

    Enheten kommer ur klubbens egen uppstallning pa matchsidan, inte gissad ur
    vilka som gor mal ihop. Malet knyts till den enhet flest av spelarna pa
    isen tillhorde.

    Swehockey skriver "1st Line" over en rad som rymmer hela femman: de tre
    forwardsen och backparet. Raden ar alltsa inte en kedja i ordets vanliga
    mening, och forwards och backar maste delas isar har — annars blandas de
    i listan, och vilka tre som syns avgors av vem som rakat spela flest
    matcher. Positionen kommer ur sasongsstatistiken.

    Lagkoden avgor vem som gjorde malet — inte trojnumren. Motstandarens 19
    kolliderar med var egen 19, och rakas det pa nummer blir varje mal vart.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = ",".join(str(sid) for sid in {active["regular"], active.get("playoff")} if sid)

        lineups = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT game_id, player_number, player_name, block, line_number
                FROM `{bq.project}.core.game_lineups`
                WHERE season_group_id IN ({season_ids})
                  AND REGEXP_CONTAINS(team_name, r'(?i)bj[oö]rkl[oö]ven')
                """
            ).result()
        ]
        goals = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT game_id, team_code, on_ice_for, on_ice_against, score_state
                FROM `{bq.project}.core.game_events`
                WHERE season_group_id IN ({season_ids}) AND event_type = 'goal'
                """
            ).result()
        ]

        # Position per spelare, sa forwards och backar gar att skilja at.
        # Sasongstabellen markerar spelare med asterisk, uppstallningen inte —
        # utan den har normaliseringen traffar uppslaget aldrig.
        def _key(name: str) -> str:
            return re.sub(r"[*\u2020\u2021]+", "", str(name or "")).strip().rstrip(",").strip().lower()

        position: dict[str, str] = {}
        try:
            for r in bq.query(
                f"""
                SELECT a.player_name, a.position
                FROM `{bq.project}.core.player_season_stats` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.player_season_stats`
                    WHERE season_group_id IN ({season_ids})
                ) b ON a.scraped_at = b.max_s
                WHERE a.season_group_id IN ({season_ids})
                  AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
                """
            ).result():
                d = dict(r.items())
                name = _key(clean_person(d.get("player_name")))
                if name and d.get("position"):
                    position.setdefault(name, str(d["position"]).upper())
        except Exception:
            logging.warning("Kunde inte lasa positionerna for kedjorna", exc_info=True)

        def is_back(name: str) -> bool:
            """LD, RD och D ar backar; CE, LW och RW ar forwards."""
            return position.get(_key(name), "").rstrip("0123456789").endswith("D")

        line_of, members = {}, {}
        for l in lineups:
            if l.get("block") != "line" or not l.get("line_number"):
                continue
            num = l.get("player_number")
            if num is None:
                continue
            line_of[(l["game_id"], int(num))] = int(l["line_number"])
            members.setdefault(int(l["line_number"]), Counter())[clean_person(l.get("player_name"))] += 1

        def numbers(text):
            return [int(x) for x in str(text or "").split(",") if x.strip().isdigit()]

        tally = {}
        unattributed = {"for": 0, "against": 0}
        for g in goals:
            ours = _is_ours(g.get("team_code"))
            on = numbers(g.get("on_ice_for") if ours else g.get("on_ice_against"))
            seen = [line_of.get((g["game_id"], n)) for n in on]
            seen = [x for x in seen if x]
            key = "for" if ours else "against"
            if not seen:
                # Tomt mal: malvakten utbytt mot en extra spelare, och de pa
                # isen tillhor ingen kedja. Redovisas, inte tystas.
                unattributed[key] += 1
                continue
            line = Counter(seen).most_common(1)[0][0]
            row = tally.setdefault(line, {"gf": 0, "ga": 0})
            row["gf" if ours else "ga"] += 1

        lines = []
        for n in sorted(tally):
            row = tally[n]
            # Flest matcher forst inom varje position; en femma ar tre
            # forwards och tva backar, resten har hoppat in.
            roster = members.get(n, Counter())
            forwards = [name for name, _ in roster.most_common() if not is_back(name)][:3]
            defence = [name for name, _ in roster.most_common() if is_back(name)][:2]
            lines.append({
                "line": n,
                "goals_for": row["gf"],
                "goals_against": row["ga"],
                "diff": row["gf"] - row["ga"],
                "share_of_team_goals": round(100 * row["gf"] / max(1, sum(v["gf"] for v in tally.values())), 1),
                "forwards": forwards,
                "defence": defence,
                # Hur manga fler som spelat i femman an de som listas ovan.
                # Backparet byts oftare an kedjan, och att tiga om det vore
                # att pasta att fem spelare stod for hela utfallet.
                "rotated": max(0, len(roster) - len(forwards) - len(defence)),
                "players": [name for name, _ in roster.most_common(6)],
            })

        # Hur manga spelare som roterat in utover de tjugo som listas. Raknas
        # over distinkta namn — samma back har ofta hoppat in i flera femmor,
        # och en summa av radernas `rotated` hade raknat honom en gang per rad.
        listed = {name for row in lines for name in row["forwards"] + row["defence"]}
        everyone = {name for n in tally for name in members.get(n, Counter())}

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "lines": lines,
            "totals": {
                "goals_for": sum(v["gf"] for v in tally.values()),
                "goals_against": sum(v["ga"] for v in tally.values()),
                "without_line_for": unattributed["for"],
                "without_line_against": unattributed["against"],
                "rotated_players": len(everyone - listed),
            },
        }
    except Exception as e:
        logging.exception("get_lines misslyckades")
        return {"status": "error", "error": str(e), "lines": []}


@app.get("/api/v1/table-history")
@cached_ok(cache=stats_cache)
def get_table_history(season: str = None, refresh: bool = False):
    """Tabellplacering per omgang, harledd ur matchresultaten.

    Sparade tabellogonblicksbilder gar inte att anvanda bakat: en avslutad
    sasong har skrapats om i efterhand, och varje sadan generation bar
    sluttabellen med ett farskt scraped_at. Resultaten daremot ligger kvar
    som de var, sa serien gar att rakna fram for hela sasongen.

    Poang enligt svensk praxis: 3 for vinst i ordinarie tid, 2 efter
    forlangning, 1 for forlust efter forlangning.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        regular = active["regular"]

        games = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT game_id, match_date, home_team, away_team, result, period_results
                FROM `{bq.project}.core.schedule`
                WHERE season_group_id = {int(regular)}
                  AND game_id IS NOT NULL
                  AND REGEXP_CONTAINS(IFNULL(result, ''), r'\d+\s*-\s*\d+')
                ORDER BY match_date, game_id
                """
            ).result()
        ]

        def award(row, home):
            h, a = _score(row.get("result"))
            if h is None:
                return None
            beyond = len(parse_period_results(row.get("period_results"))) > 3
            won = (h > a) if home else (a > h)
            return (2 if beyond else 3) if won else (1 if beyond else 0)

        points, played = Counter(), Counter()
        rounds, our_round = [], 0
        for row in games:
            for team, home in ((row.get("home_team"), True), (row.get("away_team"), False)):
                pts = award(row, home)
                if pts is None:
                    continue
                points[team] += pts
                played[team] += 1
            ours = next((t for t in (row.get("home_team"), row.get("away_team"))
                         if BJK_HOME.search(str(t or ""))), None)
            if not ours or played[ours] == our_round:
                continue
            our_round = played[ours]
            order = sorted(points.items(), key=lambda kv: (-kv[1], kv[0]))
            rounds.append({
                "round": our_round,
                "date": str(row.get("match_date") or "")[:10],
                "table": [{"team": t, "rank": i, "points": p, "games_played": played[t]}
                          for i, (t, p) in enumerate(order, 1)],
            })

        # Serien mats i VARA omgangar, men sasongen tar inte slut med var sista
        # match: HA 25/26 spelade tre matcher efter Bjorklovens, och Kalmar vann
        # en av dem. Ogonblicksbilden vid var sista omgang gav dem darfor 110
        # poang dar tabellen sager 113. Sluttabellen raknas separat, over alla
        # matcher, och ar den som far ge final_rank.
        order = sorted(points.items(), key=lambda kv: (-kv[1], kv[0]))
        final = [{"team": t, "rank": i, "points": p, "games_played": played[t]}
                 for i, (t, p) in enumerate(order, 1)]

        series = {}
        for entry in final:
            team = entry["team"]
            series[team] = [next((x["rank"] for x in r["table"] if x["team"] == team), None)
                            for r in rounds]

        trailing = sum(1 for t in played if played[t] > (
            next((x["games_played"] for x in (rounds[-1]["table"] if rounds else [])
                  if x["team"] == t), 0)))

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "rounds": [r["round"] for r in rounds],
            "dates": [r["date"] for r in rounds],
            # Sant nar minst ett lag spelade fler matcher efter var sista. Da ar
            # sista punkten i kurvan inte sluttabellen, och frontend ska saga det.
            "table_settled_after_last_round": trailing > 0,
            "teams": [
                {
                    "team": e["team"],
                    "is_bjk": bool(BJK_HOME.search(str(e["team"] or ""))),
                    "final_rank": e["rank"],
                    "points": e["points"],
                    "games_played": e["games_played"],
                    "ranks": series[e["team"]],
                }
                for e in final
            ],
        }
    except Exception as e:
        logging.exception("get_table_history misslyckades")
        return {"status": "error", "error": str(e), "teams": []}


@app.get("/api/v1/opponents")
@cached_ok(cache=stats_cache)
def get_opponents(season: str = None, venue: str = None, last: int = 0, refresh: bool = False):
    """Facit mot varje motstandare. venue=home|away, last=N begransar urvalet."""
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = ",".join(str(sid) for sid in {active["regular"], active.get("playoff")} if sid)

        rows = _bjk_games(bq, season_ids)
        want_home = {"home": True, "hemma": True, "away": False, "borta": False}.get(
            str(venue or "").lower()
        )
        picked = []
        for row in rows:
            view = _ours_theirs(row)
            if view is None:
                continue
            gf, ga, opponent, home = view
            if want_home is not None and home != want_home:
                continue
            picked.append((row, gf, ga, opponent, home))
        if last and last > 0:
            picked = picked[-int(last):]

        by_opponent = {}
        for row, gf, ga, opponent, home in picked:
            d = by_opponent.setdefault(opponent, {
                "opponent": opponent, "games": 0, "wins": 0, "losses": 0,
                "goals_for": 0, "goals_against": 0, "beyond_regulation": 0,
            })
            d["games"] += 1
            d["goals_for"] += gf
            d["goals_against"] += ga
            d["wins" if gf > ga else "losses"] += 1
            if len(parse_period_results(row.get("period_results"))) > 3:
                d["beyond_regulation"] += 1

        table = sorted(
            ({**d, "diff": d["goals_for"] - d["goals_against"]} for d in by_opponent.values()),
            key=lambda d: (-d["wins"], -d["diff"], d["opponent"]),
        )
        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "filter": {"venue": venue or "all", "last": last or 0},
            "games": len(picked),
            "opponents": table,
        }
    except Exception as e:
        logging.exception("get_opponents misslyckades")
        return {"status": "error", "error": str(e), "opponents": []}


@app.get("/api/v1/swings")
@cached_ok(cache=stats_cache)
def get_swings(season: str = None, refresh: bool = False):
    """Vandningar och tapp: stallningen efter tva perioder mot slutresultatet."""
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)
        active = lookup_season(season)
        season_ids = ",".join(str(sid) for sid in {active["regular"], active.get("playoff")} if sid)

        out = []
        for row in _bjk_games(bq, season_ids):
            view = _ours_theirs(row)
            periods = parse_period_results(row.get("period_results"))
            if view is None or len(periods) < 3:
                continue
            gf, ga, opponent, home = view
            after_home = sum(p["home_gf"] for p in periods[:2])
            after_away = sum(p["away_gf"] for p in periods[:2])
            us2, them2 = (after_home, after_away) if home else (after_away, after_home)
            if us2 < them2 and gf > ga:
                kind = "comeback"
            elif us2 > them2 and gf < ga:
                kind = "collapse"
            else:
                continue
            out.append({
                "kind": kind,
                "game_id": row.get("game_id"),
                "date": str(row.get("match_date") or "")[:10],
                "opponent": opponent,
                "is_home": home,
                "after_two": f"{us2}-{them2}",
                "final": f"{gf}-{ga}",
                "beyond_regulation": len(periods) > 3,
            })

        return {
            "status": "ok",
            "season": active["name"],
            "season_key": active["key"],
            "comebacks": sum(1 for x in out if x["kind"] == "comeback"),
            "collapses": sum(1 for x in out if x["kind"] == "collapse"),
            "swings": out,
        }
    except Exception as e:
        logging.exception("get_swings misslyckades")
        return {"status": "error", "error": str(e), "swings": []}


@app.get("/api/v1/match/{game_id}")
@cached_ok(cache=stats_cache)
def get_match(game_id: int):
    """Alla handelser for en enskild match.

    Bygger matchrapporten: malkronologi, utvisningar och momentumkurva.
    Kallan ar core.game_events, som fylls fran Swehockeys
    /Game/Events-sidor. Matchen kopplas till schedule via game_id.
    """
    try:
        bq = bigquery.Client(project=BQ_PROJECT_ID or None)

        events = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.game_events` a
                INNER JOIN (
                    SELECT MAX(scraped_at) AS max_s
                    FROM `{bq.project}.core.game_events`
                    WHERE game_id = {int(game_id)}
                ) b ON a.scraped_at = b.max_s
                WHERE a.game_id = {int(game_id)}
                """
            ).result()
        ]

        # Schemaraden bar datum, arena, publik och periodresultat.
        sched_rows = [
            dict(r.items())
            for r in bq.query(
                f"""
                SELECT a.*
                FROM `{bq.project}.core.schedule` a
                WHERE a.game_id = {int(game_id)}
                ORDER BY a.scraped_at DESC
                LIMIT 1
                """
            ).result()
        ]
        sched = sched_rows[0] if sched_rows else {}

        if not events and not sched:
            return {"status": "not_found", "game_id": game_id, "error": "Matchen finns inte i datalagret."}

        def _minute(t: str) -> float:
            """'58:25' -> 58.42. Klockan ar loptid over hela matchen."""
            try:
                mm, ss = str(t or "").split(":")[:2]
                return int(mm) + int(ss) / 60.0
            except Exception:
                return 0.0

        # Trojnummer -> namn for laget, sa on-ice-listorna gar att lasa.
        # Handelserna bar bara nummer; utan uppslaget star det "6, 19, 28".
        squad: dict[str, dict] = {}
        season_gid = sched.get("season_group_id") or next(
            (e.get("season_group_id") for e in events if e.get("season_group_id")), None
        )
        if season_gid:
            try:
                for r in bq.query(
                    f"""
                    SELECT a.player_name, a.jersey_number, a.position, a.games_played
                    FROM `{bq.project}.core.player_season_stats` a
                    INNER JOIN (
                        SELECT MAX(scraped_at) AS max_s
                        FROM `{bq.project}.core.player_season_stats`
                        WHERE season_group_id = {int(season_gid)}
                    ) b ON a.scraped_at = b.max_s
                    WHERE a.season_group_id = {int(season_gid)}
                      AND (LOWER(a.team_code) LIKE '%ifb%' OR LOWER(a.team_code) LIKE '%rkl%')
                    -- Flest matcher vinner numret nar tva spelare delat det.
                    ORDER BY a.games_played DESC
                    """
                ).result():
                    d = dict(r.items())
                    num = d.get("jersey_number")
                    if num is None:
                        continue
                    squad.setdefault(
                        str(int(num)),
                        {"name": clean_person(d.get("player_name")), "position": d.get("position")},
                    )
            except Exception:
                logging.warning("Kunde inte lasa truppen for match %s", game_id, exc_info=True)

        # Spelare som bytt nummer under sasongen fangas av handelserna sjalva.
        for e in events:
            num, name = e.get("player_number"), clean_person(e.get("player_name"))
            if num is not None and name and _is_ours(e.get("team_code")):
                squad.setdefault(str(int(num)), {"name": name, "position": None})

        home = sched.get("home_team") or next((e.get("home_team") for e in events if e.get("home_team")), "")
        away = sched.get("away_team") or next((e.get("away_team") for e in events if e.get("away_team")), "")

        goals = sorted(
            [e for e in events if (e.get("event_type") or "") == "goal"],
            key=lambda e: _minute(e.get("time")),
        )
        penalties = sorted(
            [e for e in events if (e.get("event_type") or "") == "penalty"],
            key=lambda e: _minute(e.get("time")),
        )

        def _shape_goal(e):
            assists = [
                c for c in (clean_person(e.get("assist1_name")), clean_person(e.get("assist2_name"))) if c
            ]
            return {
                "time": e.get("time"),
                "minute": round(_minute(e.get("time")), 2),
                "period": e.get("period"),
                "team_code": e.get("team_code"),
                "scorer": clean_person(e.get("player_name")),
                "scorer_number": e.get("player_number"),
                "assists": assists,
                "score_state": e.get("score_state"),
                "is_power_play": bool(e.get("is_power_play")),
                "is_short_handed": bool(e.get("is_short_handed")),
                # Trojnumren pa isen: for det gorande laget respektive det
                # slappande. Aldre rader saknar dem tills matchen skorats om.
                "on_ice_for": [int(n) for n in re.findall(r"\d{1,2}", str(e.get("on_ice_for") or ""))],
                "on_ice_against": [int(n) for n in re.findall(r"\d{1,2}", str(e.get("on_ice_against") or ""))],
            }

        def _shape_penalty(e):
            return {
                "time": e.get("time"),
                "minute": round(_minute(e.get("time")), 2),
                "period": e.get("period"),
                "team_code": e.get("team_code"),
                "player": clean_person(e.get("player_name")),
                "player_number": e.get("player_number"),
                "minutes": e.get("penalty_minutes") or 0,
                "type": clean_penalty_type(e.get("detail")),
            }

        # Lagkoder: identifiera vilken kod som ar hemmalaget, sa klienten kan
        # placera handelserna ratt utan att gissa.
        codes = [c for c in {e.get("team_code") for e in events} if c]

        # Skott, raddningar och powerplaytid ligger i matchsummeringen, inte i
        # handelserna. Utan dem kan rapporten bara beratta vad som hande, inte
        # hur matchen sag ut — och delkortet skulle behova hitta pa siffror.
        def _side(row: dict) -> dict:
            return {
                "team_name": row.get("team_name"),
                "is_home": bool(row.get("is_home")),
                "shots": row.get("shots"),
                "saves": row.get("saves"),
                "pim": row.get("pim"),
                "shots_by_period": row.get("shots_by_period"),
                "saves_by_period": row.get("saves_by_period"),
                "pp_pct": _to_float(row.get("pp_pct")),
                "pp_time": row.get("pp_time"),
                "shooting_pct": _to_float(row.get("shooting_pct")),
                "save_pct": _to_float(row.get("save_pct")),
                "pdo": _to_float(row.get("pdo")),
            }

        teams: dict[str, dict] | None = None
        keepers: list[dict] = []
        try:
            summary = [
                dict(r.items())
                for r in bq.query(
                    f"""
                    SELECT team_key AS team_name, is_home, shots, saves, pim,
                           shots_by_period, saves_by_period,
                           pp_pct, pp_time, shooting_pct, save_pct, pdo
                    FROM `{bq.project}.marts.fact_team_game`
                    WHERE game_id = {int(game_id)}
                    """
                ).result()
            ]
            # Var rad ar ett lag. Hemmalaget avgor vilken sida som ar var bara
            # nar vi sjalva spelar hemma.
            ours = next((r for r in summary if BJK_HOME.search(str(r.get("team_name") or ""))), None)
            theirs = next((r for r in summary if r is not ours), None)
            if ours and theirs:
                teams = {"ours": _side(ours), "theirs": _side(theirs)}

            for r in bq.query(
                f"""
                SELECT player_key, team_key, jersey_number,
                       shots_against, saves, goals_against, save_pct, time_on_ice
                FROM `{bq.project}.marts.fact_goalie_game`
                WHERE game_id = {int(game_id)}
                """
            ).result():
                d = dict(r.items())
                keepers.append(
                    {
                        "name": clean_person(d.get("player_key")),
                        "team": d.get("team_key"),
                        "number": d.get("jersey_number"),
                        # Lagnyckeln ar ibland namnet och ibland koden; _is_ours tar bada.
                        "is_ours": _is_ours(d.get("team_key")),
                        "shots_against": d.get("shots_against"),
                        "saves": d.get("saves"),
                        "goals_against": d.get("goals_against"),
                        "save_pct": _to_float(d.get("save_pct")),
                        "time_on_ice": d.get("time_on_ice"),
                    }
                )
            # Den som motte flest skott stod langst, och namns forst.
            keepers.sort(key=lambda k: -(k.get("shots_against") or 0))
        except Exception:
            # Marten byggs om vid varje skorning. Rapporten ska ga att lasa
            # aven under de minuterna, sa summeringen ar frivillig.
            logging.warning("Kunde inte lasa lagsummeringen for match %s", game_id, exc_info=True)

        return {
            "status": "ok",
            "game_id": game_id,
            "date": str(sched.get("match_date") or ""),
            "time": sched.get("match_time"),
            "home_team": home,
            "away_team": away,
            "result": sched.get("result"),
            "period_results": sched.get("period_results"),
            "venue": sched.get("venue"),
            "spectators": sched.get("spectators"),
            "team_codes": codes,
            # Skott, raddningar och specialteam per lag. None nar matchen inte
            # skorats med summeringen an.
            "teams": teams,
            "goalies": keepers,
            # Lagets trojnummer -> namn, sa on-ice-listorna gar att lasa som
            # namn i stallet for siffror.
            "squad": squad,
            "counts": {
                "events": len(events),
                "goals": len(goals),
                "penalties": len(penalties),
            },
            "goals": [_shape_goal(e) for e in goals],
            "penalties": [_shape_penalty(e) for e in penalties],
        }
    except Exception as e:
        logging.exception("Failed to load /api/v1/match/%s", game_id)
        return {"status": "error", "game_id": game_id, "error": str(e)}


@app.get("/api/v1/analytics")
@cached_ok(cache=analytics_cache)
def get_analytics(season: str = None, refresh: bool = False):
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

        schedule = q(f"SELECT a.* FROM `{proj}.core.schedule` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.schedule` WHERE season_group_id = {REGULAR_ID}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {REGULAR_ID} ORDER BY a.match_date")
        players = q(f"SELECT a.* FROM `{proj}.core.player_season_stats` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.player_season_stats` WHERE season_group_id = {REGULAR_ID}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {REGULAR_ID}")
        goalies = q(f"SELECT a.* FROM `{proj}.core.goalie_season_stats` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.goalie_season_stats` WHERE season_group_id = {REGULAR_ID}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {REGULAR_ID}")
        standings = q(f"SELECT a.* FROM `{proj}.core.standings` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.standings` WHERE season_group_id = {REGULAR_ID}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {REGULAR_ID}")

        # Find the SHL season whose start_date is closest to the current HA season,
        # but only among seasons that actually have goalie data loaded.
        # Two-step: (1) get seasons with data, (2) pick closest to REGULAR_ID's start_date.
        shl_with_data = q(f"""
            SELECT s.regular_season_id, s.start_date
            FROM `{proj}.core.season` s
            WHERE LOWER(s.league) = 'shl'
              AND EXISTS (
                SELECT 1 FROM `{proj}.core.goalie_season_stats` g
                WHERE g.season_group_id = s.regular_season_id
              )
            ORDER BY s.start_date DESC
        """)
        ha_start_rows = q(f"""
            SELECT start_date FROM `{proj}.core.season`
            WHERE regular_season_id = {REGULAR_ID}
        """)
        ha_start = ha_start_rows[0]["start_date"] if ha_start_rows else None
        shl_regular_id = None
        if shl_with_data:
            if ha_start:
                def _date_diff(row):
                    import datetime
                    s = row.get("start_date")
                    if s is None:
                        return 99999
                    if hasattr(s, "toordinal"):
                        return abs(s.toordinal() - ha_start.toordinal())
                    return 99999
                best = min(shl_with_data, key=_date_diff)
            else:
                best = shl_with_data[0]
            shl_regular_id = best["regular_season_id"]

        shl_players = []
        shl_goalies = []
        if shl_regular_id:
            shl_players = q(f"SELECT a.* FROM `{proj}.core.player_season_stats` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.player_season_stats` WHERE season_group_id = {shl_regular_id}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {shl_regular_id}")
            shl_goalies = q(f"SELECT a.* FROM `{proj}.core.goalie_season_stats` a INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.goalie_season_stats` WHERE season_group_id = {shl_regular_id}) b ON a.scraped_at = b.max_s WHERE a.season_group_id = {shl_regular_id}")

        
        # Only query events for games in the current regular season schedule to avoid loading other leagues' events
        sched_game_ids = [str(g['game_id']) for g in schedule if g.get("game_id")]
        if sched_game_ids:
            game_ids_str = ", ".join(sched_game_ids)
            # Handelsetabellen ar append-only: varje skorning lagger till en ny
            # uppsattning rader. Utan att forst valja senaste korningen per
            # match summeras alla generationer — utvisningarna tredubblades nar
            # samma sasong hade skorats tre ganger, och samma fel drabbade
            # specialteam och nar malen faller.
            events = q(
                f"""
                SELECT e.*
                FROM `{proj}.core.game_events` e
                INNER JOIN (
                    SELECT game_id, MAX(scraped_at) AS max_s
                    FROM `{proj}.core.game_events`
                    WHERE game_id IN ({game_ids_str})
                    GROUP BY game_id
                ) m ON e.game_id = m.game_id AND e.scraped_at = m.max_s
                WHERE e.game_id IN ({game_ids_str})
                """
            )
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
                if match_player(p.get("player_name")) == name:
                    candidates.append(p)
            if not candidates and shl_players:
                for p in shl_players:
                    if match_player(p.get("player_name")) == name:
                        p["_is_shl_source"] = True
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
                        # Utan namnet avgor radordningen vem som star forst
                        # bland lika, och den ar inte garanterad.
                        str(p.get("player_name") or ""),
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
                "_is_shl_source": bq_p.get("_is_shl_source", False) if bq_p else False
            })
        player_impact.sort(key=lambda x: -x["p_per_gp"])

        # ── Module 8: Goalie Radar ──
        all_goalies_min10 = sorted([g for g in goalies if (g.get("games_played") or 0) >= 10],
                                    key=lambda g: -(g.get("save_pct") or 0))

        def percentile(value, all_vals):
            if not all_vals:
                return 50
            valid_vals = [v for v in all_vals if v is not None]
            if not valid_vals:
                return 50
            below = sum(1 for v in valid_vals if float(v) <= float(value))
            return round((below / len(valid_vals)) * 100)

        sv_vals = [g.get("save_pct") or 0 for g in all_goalies_min10]
        gaa_vals = [g.get("gaa") or 0 for g in all_goalies_min10]
        wp_vals = [g.get("win_pct") or 0 for g in all_goalies_min10]

        goalie_radar = []
        radar_names = []
        for name in roster_goalies:
            candidates = []
            for g in goalies:
                if match_player(g.get("goalie_name", "")) == name:
                    candidates.append(g)
            if not candidates and shl_goalies:
                for g in shl_goalies:
                    if match_player(g.get("goalie_name", "")) == name:
                        g["_is_shl_source"] = True
                        candidates.append(g)
            
            if candidates:
                candidates.sort(
                    key=lambda g: (
                        float(g.get("save_pct") or 0),
                        _to_int(g.get("games_played")),
                        str(g.get("goalie_name") or ""),
                    ),
                    reverse=True,
                )
                g = candidates[0]
                gp = g.get("games_played") or 1
                matched_name = name
                radar_names.append(matched_name)
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
                    "gsaa": round(g.get("saves", 0) - (g.get("saves", 0) / (g.get("save_pct", 0)/100 if g.get("save_pct") else 1)) * 0.90, 1),
                    "percentiles": {
                        "sv_pct": percentile(float(g.get("save_pct") or 0), sv_vals),
                        "gaa": 100 - percentile(float(g.get("gaa") or 0), gaa_vals),
                        "win_pct": percentile(float(g.get("win_pct") or 0), wp_vals)
                    },
                    "_is_shl_source": g.get("_is_shl_source", False)
                })
            else:
                goalie_radar.append({
                    "name": name,
                    "gp": 0,
                    "sv_pct": 0,
                    "gaa": 0,
                    "shutouts": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_pct": 0,
                    "saves_per_gp": 0,
                    "gsaa": 0,
                    "percentiles": {"sv_pct": 50, "gaa": 50, "win_pct": 50},
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
            "by_type": [{"type": k, "count": v} for k, v in sorted(pen_by_type.items(), key=lambda x: (-x[1], x[0]))[:5]],
            "by_period": [{"period": k, "count": v} for k, v in pen_by_period.items()],
            "most_penalized": [{"name": k, "count": v["count"], "minutes": v["minutes"]} for k, v in sorted(pen_by_player.items(), key=lambda x: (-x[1]["minutes"], -x[1]["count"], x[0]))[:5]],
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
                         for p, count in sorted(chemistry.items(), key=lambda x: (-x[1], str(x[0])))[:5]]

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
        for list_name in ["confirmed_signings", "roster"]:
            for signing in SILLY_SEASON_BASELINE.get(list_name, []):
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
                is_shl_exempt = p.get("_is_shl_source") or name in ["Fredrik Forsberg", "Marcus Nilsson"]
                if is_shl_exempt:
                    proj_ppg = round(p["p_per_gp"], 2)
                    ha_ppg = round(p["p_per_gp"], 2)
                    display_name = f"{name} (SHL/Exempt)"
                elif p["p_per_gp"] == 0 and any(s.get("name") == name for s in SILLY_SEASON_BASELINE.get("confirmed_signings", [])):
                    # Utlandsspelare / missing stats heuristic
                    proj_ppg = 0.50 if "D" not in p["position"] else 0.30
                    ha_ppg = proj_ppg / 0.60
                    display_name = f"{name} (Utland)"
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
                
            name = g["name"]
            matched_override = None
            for override_name, override_data in signings_overrides.items():
                if name_match_strict(name, override_name):
                    matched_override = (override_name, override_data)
                    break
                    
            if matched_override:
                override_name, override_data = matched_override
                proj_sv_pct = override_data.get("proj_sv_pct", 91.0)
                proj_gaa = override_data.get("proj_gaa", 2.20)
                ha_sv_pct = override_data.get("ha_sv_pct", 92.0)
                display_name = f"{override_name} 🆕"
            else:
                ha_sv_pct = g["sv_pct"]
                if g.get("_is_shl_source") or ha_sv_pct == 0:
                    # Source is already SHL data — use directly, no HA-to-SHL regression
                    shl_sv = ha_sv_pct
                    proj_sv_pct = round(shl_sv, 1)
                else:
                    # HA data — apply typical HA-to-SHL regression of -1.8%
                    proj_sv_pct = round(ha_sv_pct - 1.8, 1)
                proj_gaa = round(g["gaa"] + 0.60, 2) if not g.get("_is_shl_source") else round(g["gaa"], 2)
                display_name = name

            readiness = "GREEN" if proj_sv_pct >= 91.0 else "AMBER" if proj_sv_pct >= 89.2 else "RED"
            shl_goalies.append({
                "name": display_name,
                "ha_sv_pct": ha_sv_pct,
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

        roster_ages = {}
        for r_p in SILLY_SEASON_BASELINE.get("roster", []) + SILLY_SEASON_BASELINE.get("confirmed_departures", []):
            name = r_p.get("name")
            age = r_p.get("age")
            if name and age:
                roster_ages[name] = int(age)

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

        # — Modul 20: Predicted SHL Table (Preseason) —
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
            shl_standings = []
            if shl_regular_id:
                shl_standings = q(f"""
                    SELECT a.team_name, a.games_played, a.points, a.rank
                    FROM `{proj}.core.standings` a
                    INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `{proj}.core.standings` WHERE season_group_id = {int(shl_regular_id)}) b
                    ON a.scraped_at = b.max_s
                    WHERE a.season_group_id = {int(shl_regular_id)}
                      AND COALESCE(a.games_played, 0) >= 40
                      AND COALESCE(a.points, 0) > 0
                """)

            if not shl_standings:
                shl_projected_table["data_quality"] = "missing_shl_source"
                raise ValueError("No SHL standings data available in core.standings for latest SHL season")

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
            sti = special_teams.get("special_teams_index") or 95.0
            bjk_points_model += (sti - 95.0) * 0.35
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
                    "projected_rank_p50": i,
                    "projected_rank_p10": p10_rank,
                    "projected_rank_p50": i,
                    "projected_rank_p90": p90_rank,
                    "team": r["team"],
                    "projected_points": pts,
                    "projected_points_p50": pts,
                    "projected_points_p10": p10_pts,
                    "projected_points_p50": pts,
                    "projected_points_p90": p90_pts,
                    "tier": tier,
                    "top6_chance_pct": top6_chance,
                    "playout_risk_pct": playout_risk,
                    "is_bjk": is_bjk(r["team"]) or "bjÃ¶rklÃ¶ven" in (r["team"] or "").lower(),
                })

            bjk_row = next((r for r in projected_table_rows if r.get("is_bjk") or r.get("team") == "IF Björklöven"), None)
            shl_projected_table = {
                "season": "SHL 2026/27 (preseason)",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "method": "Team-strength blend (historic SHL baseline + BJK roster projection)",
                "data_quality": "ok",
                "table": projected_table_rows,
                "bjk_summary": {
                    "projected_rank": bjk_row["projected_rank"] if bjk_row else None,
                    "projected_points": bjk_row["projected_points"] if bjk_row else None,
                    "top6_chance_pct": bjk_row["top6_chance_pct"] if bjk_row else None,
                    "playout_risk_pct": bjk_row["playout_risk_pct"] if bjk_row else None,
                    "projected_points_p10": bjk_row["projected_points_p10"] if bjk_row else None,
                    "projected_points_p50": bjk_row["projected_points"] if bjk_row else None,
                    "projected_points_p90": bjk_row["projected_points_p90"] if bjk_row else None,
                    "projected_rank_p10": bjk_row["projected_rank_p10"] if bjk_row else None,
                    "projected_rank_p50": bjk_row["projected_rank"] if bjk_row else None,
                    "projected_rank_p90": bjk_row["projected_rank_p90"] if bjk_row else None,
                } if bjk_row else {}
            }
        except Exception as shl_proj_err:
            logging.warning(f"Failed to compute shl_projected_table: {shl_proj_err}")
            shl_projected_table = {
                "season": "SHL 2026/27 (preseason)",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "method": "Team-strength blend (historic SHL baseline + BJK roster projection)",
                "data_quality": "error",
                "table": [],
                "bjk_summary": {}
            }

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
                "shl_transition": {
                    "skaters": shl_skaters,
                    "goalies": shl_goalies,
                    "benchmarks": shl_benchmarks
                },
                "age_curve": age_curve,
                "shl_projected_table": shl_projected_table,
                "silly_season": {
                    "baseline": SILLY_SEASON_BASELINE,
                    "shl_readiness": {
                        "skaters": shl_skaters,
                        "goalies": shl_goalies,
                        "benchmarks": shl_benchmarks
                    },
                    "shl_projected_table": shl_projected_table
                },
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
            "age": s.get("age"),
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
# Bada silly-endpointsen tar noll argument och delade cache. Med `@cached`
# blir nyckeln hashkey() for bagge, sa de returnerade varandras svar.
# cached_ok tar med funktionsnamnet i nyckeln.
@cached_ok(cache=silly_cache)
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
@cached_ok(cache=silly_cache)
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
@cached(cache=xfeed_cache)
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


