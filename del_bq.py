from google.cloud import bigquery
client = bigquery.Client()
client.query("DELETE FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id=20961").result()
print('Deleted schedule 20961')
