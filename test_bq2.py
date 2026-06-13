from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT *
FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
WHERE season_group_id = 459 
  AND player_name LIKE '%Forsberg%'
LIMIT 1
'''
for row in client.query(q).result():
    print(dict(row.items()))
