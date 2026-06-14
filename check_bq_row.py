from google.cloud import bigquery
import json
client = bigquery.Client()
rows = list(client.query("SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id=20961 LIMIT 2").result())
if rows:
    print(dict(rows[0]))
else:
    print('No rows found')
