from google.cloud import bigquery
client = bigquery.Client()
q = 'UPDATE `granskaren-d51a1.raw_sports.swehockey_seasons` SET regular_season_id = 20962 WHERE season_key = "ha_2526"'
job = client.query(q)
job.result()
print('Updated BQ')
