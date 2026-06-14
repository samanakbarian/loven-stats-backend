import json
from google.cloud import bigquery
client = bigquery.Client()
q = '''SELECT DISTINCT home_team FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id = 20961'''
rows = list(client.query(q).result())
teams = [r.home_team for r in rows]
with open('teams.json', 'w', encoding='utf-8') as f:
    json.dump(teams, f, ensure_ascii=False)
