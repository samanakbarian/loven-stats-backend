"""Skrapa HA 25/26 lokalt och spara som JSON, sa marten kan provas mot riktigt data."""
import sys, os, json, pathlib
sys.path.insert(0,'/home/user/loven-stats-backend/functions')
os.environ.setdefault("GCP_PROJECT","x"); os.environ.setdefault("GCS_BUCKET","x")
import swehockey_stats_scraper as m
from game_events_parser import parse_events, parse_game_summary, parse_lineups

SID = "18266"
out = pathlib.Path("core_data"); out.mkdir(exist_ok=True)

games = m._team_games(SID, None)
print(f"{len(games)} matcher for laget")
events, summary, goalies, lineups = [], [], [], []
for i, g in enumerate(games, 1):
    gid = int(g["game_id"])
    ev_html = m._game_html(gid)
    lu_html = m._lineup_html(gid)
    if not ev_html:
        print(f"  {gid}: ingen handelsesida"); continue
    ev = parse_events(ev_html, gid)
    su = parse_game_summary(ev_html, gid)
    lu = parse_lineups(lu_html, gid) if lu_html else []
    for r in ev: r["season_group_id"] = int(SID); r["match_date"] = g.get("match_date")
    for r in su["teams"]: r["season_group_id"] = int(SID); r["match_date"] = g.get("match_date"); r["game_id"] = gid
    for r in su["goalies"]:
        r["season_group_id"] = int(SID); r["match_date"] = g.get("match_date")
        r["home_team"] = su["teams"][0].get("team_name"); r["away_team"] = su["teams"][1].get("team_name")
    for r in lu: r["season_group_id"] = int(SID); r["match_date"] = g.get("match_date")
    events += ev; summary += su["teams"]; goalies += su["goalies"]; lineups += lu
    if i % 10 == 0: print(f"  {i}/{len(games)}")

ps,_ = m._fetch_player_stats(SID)
ro,_ = m._fetch_roster(SID)
sc,_ = m._fetch_schedule(SID)
st,_ = m._fetch_standings(SID)
for r in ps: r["season_group_id"] = int(SID)

for name, rows in (("game_events",events),("game_team_summary",summary),("game_goalies",goalies),
                   ("game_lineups",lineups),("player_season_stats",ps),("roster",ro),
                   ("schedule",sc),("standings",st)):
    (out/f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, default=str))
    print(f"  {name:<22}{len(rows):>6} rader")
