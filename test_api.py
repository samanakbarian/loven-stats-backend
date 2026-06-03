import sys
import os
# Add the api folder to path
sys.path.append(os.path.abspath('c:\\Users\\saman\\loven-stats-backend\\api'))
import main
print('--- Seasons ---')
print(main.get_seasons())
print('--- Statistics (ha_2324) ---')
stats = main.get_statistics_snapshot(season='ha_2324')
print(f"Games played: {stats.get('team_games')}")
if stats.get('skaters_regular'):
    top_scorer = stats['skaters_regular'][0]
    print(f"Top scorer: {top_scorer.get('player_name')} with {top_scorer.get('points')} points")
else:
    print("Top scorer: Not found")
print(f"Team standing rank: {stats.get('team_standing').get('rank') if stats.get('team_standing') else None}")
