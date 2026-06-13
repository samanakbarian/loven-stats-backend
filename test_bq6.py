import sys
sys.path.append('api')
from main import q, shl_regular_id

res = q(f"""
    SELECT team_name, games_played, points, rank
    FROM `granskaren-d51a1.raw_sports.swehockey_standings`
    WHERE season_group_id = {int(shl_regular_id)}
      AND COALESCE(games_played, 0) >= 40
      AND COALESCE(points, 0) > 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY team_name, season_group_id
        ORDER BY scraped_at DESC
    ) = 1
""")

for r in res:
    gp = max(1, int(r.get('games_played') or 52))
    pts = float(r.get('points') or 0)
    ppg = pts / gp
    rank = int(r.get('rank') or 14)
    ppg_seed = ppg * 52.0
    rank_seed = max(42.0, 100.0 - ((rank - 1) * 4.0))
    base_points = round((ppg_seed * 0.75) + (rank_seed * 0.25))
    print(f"{rank}: {r.get('team_name')} - {base_points}")
