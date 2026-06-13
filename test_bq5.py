from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT player_name, team_code, games_played, goals, assists, points
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
WHERE season_group_id = 19979 AND team_code = 'IFB'
ORDER BY points DESC
'''
for row in client.query(q).result():
    print(dict(row.items()))
