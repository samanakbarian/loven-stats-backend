"""Kor marts-SQL:en mot en riktig sasong i DuckDB och stam av mot Swehockeys facit.

Korning:
    python3 tests/marts_extract.py     # skrapar HA 25/26 till core_data/
    python3 tests/marts_validate.py    # bygger vyerna och jamfor

BigQuery gar inte att na fran utvecklingsmiljon, sa vyerna provas i DuckDB
med samma SQL och nagra dialektoversattningar. Det fangar inte allt, men
det fangade tre riktiga fel: tomma-mal-mal som regexen tappade,
straffavgoranden som saknades helt, och assistgivare som blev utan lag.
"""

import duckdb, json, re, pathlib, sys

con = duckdb.connect()
for name in ("game_events","game_team_summary","game_goalies","game_lineups",
             "player_season_stats","roster","schedule","standings"):
    rows = json.load(open(f"core_data/{name}.json"))
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_json_auto(?)", [f"core_data/{name}.json"])
    print(f"  {name:<22}{len(rows):>6} rader")

# core.season och core.standings_history finns inte i extraktet — stubbar.
con.execute("""CREATE TABLE season AS SELECT 'ha_2526' season_key,
  'HockeyAllsvenskan 2025/26' season_name, 'HA' league, 18266 regular_season_id,
  19979 playoff_id, DATE '2025-09-19' start_date, DATE '2026-03-15' end_date, TRUE is_active""")
con.execute("CREATE TABLE standings_history AS SELECT *, DATE '2026-03-15' AS snapshot_date FROM standings")

sql = pathlib.Path("/home/user/loven-stats-backend/sql/marts.sql").read_text()
# BigQuery -> DuckDB
sql = re.sub(r'`@PROJECT@\.(core|marts)\.(\w+)`', r'\2', sql)
sql = sql.replace("CREATE SCHEMA IF NOT EXISTS marts OPTIONS(location = 'europe-west1');", "")
sql = re.sub(r"CREATE SCHEMA[^;]+;", "", sql)
sql = sql.replace("SAFE_CAST", "TRY_CAST").replace("SPLIT(", "STRING_SPLIT(")
sql = sql.replace("IFNULL(", "COALESCE(").replace("LOGICAL_OR(", "BOOL_OR(")
sql = sql.replace("REGEXP_CONTAINS(", "REGEXP_MATCHES(")
# BigQuery REGEXP_EXTRACT ger fangstgruppen, DuckDB hela traffen
sql = re.sub(r"REGEXP_EXTRACT\(([^,]+), ('[^']*')\)", r"REGEXP_EXTRACT(\1, \2, 1)", sql)
sql = sql.replace("UNION DISTINCT", "UNION")
# DuckDB namnger unnest-kolumnen med tabellalias
sql = sql.replace(") AS num", ") AS t(num)")
sql = re.sub(r"ANY_VALUE\(([^()]*(?:\([^()]*\))?[^()]*)\)", r"MIN(\1)", sql)

made = 0
for stmt in sql.split(";"):
    if not stmt.strip() or all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
        continue
    try:
        con.execute(stmt); made += 1
    except Exception as e:
        print(f"\nFEL i sats {made+1}: {str(e)[:300]}")
        print("  ", stmt.strip()[:200].replace("\n"," "))
        sys.exit(1)
print(f"\n{made} vyer skapade\n")

print("="*76)
print("AVSTÄMNING: fact_player_game summerad mot Swehockeys säsongstotaler")
print("="*76)
q = con.execute("""
WITH derived AS (
  SELECT player_key, SUM(goals) g, SUM(assists) a, SUM(points) p, SUM(pim) pim,
         COUNT(*) FILTER (WHERE in_lineup) gp
  FROM fact_player_game WHERE team_key = 'IF Björklöven' GROUP BY player_key
),
official AS (
  SELECT player_key, goals g, assists a, points p, pim, games_played gp
  FROM fact_player_season WHERE team_key = 'IF Björklöven'
)
SELECT o.player_key, o.g og, d.g dg, o.a oa, d.a da, o.p op, d.p dp,
       o.pim opim, d.pim dpim, o.gp ogp, d.gp dgp
FROM official o LEFT JOIN derived d USING (player_key)
ORDER BY o.p DESC
""").fetchall()
hdr = f"{'spelare':<24}{'mål':>9}{'assist':>10}{'poäng':>10}{'pim':>9}{'matcher':>11}"
print(hdr); print("-"*len(hdr))
bad = 0
for r in q:
    name,og,dg,oa,da,op,dp,opim,dpim,ogp,dgp = r
    dg,da,dp,dpim,dgp = [x if x is not None else 0 for x in (dg,da,dp,dpim,dgp)]
    f = lambda o,d: f"{o}/{d}" + ("" if o==d else " ✗")
    line = f"{name:<24}{f(og,dg):>9}{f(oa,da):>10}{f(op,dp):>10}{f(opim,dpim):>9}{f(ogp,dgp):>11}"
    if (og,oa,op)!=(dg,da,dp): bad += 1
    print(line)
print("-"*len(hdr))
print(f"{len(q)} spelare, {bad} med avvikande mål/assist/poäng   (officiellt/härlett)")
