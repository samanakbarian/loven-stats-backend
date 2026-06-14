from google.cloud import bigquery
client = bigquery.Client()
rows = list(client.query("SELECT DISTINCT home_team, away_team FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id=20961").result())
teams = set()
for r in rows:
    teams.add(r['home_team'])
    teams.add(r['away_team'])
print([t for t in teams if 'Bj' in t])
