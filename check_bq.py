from google.cloud import bigquery
client = bigquery.Client()
rows = list(client.query("SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id=20961").result())
print('Schedule rows:', len(rows))
