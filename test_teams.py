from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT team_code, COUNT(*) as c
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
WHERE season_group_id = 18266
GROUP BY team_code
'''
for row in client.query(q).result():
    print(dict(row.items()))
