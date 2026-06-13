from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT season_group_id, team_code, player_name, games_played, goals, assists, points
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
WHERE season_group_id = 18266 AND player_name LIKE '%Forsberg%'
'''
for row in client.query(q).result():
    print(dict(row.items()))
