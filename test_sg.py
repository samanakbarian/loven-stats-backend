from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT season_group_id, COUNT(*) as c
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
GROUP BY season_group_id
ORDER BY c DESC
'''
for row in client.query(q).result():
    print(dict(row.items()))
