from google.cloud import bigquery
client = bigquery.Client()
q = 'UPDATE `granskaren-d51a1.raw_sports.swehockey_seasons` SET playoff_id = NULL WHERE season_key = "ha_2526"'
job = client.query(q)
job.result()
print('Updated BQ playoff_id')
