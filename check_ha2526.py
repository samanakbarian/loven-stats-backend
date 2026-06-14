from google.cloud import bigquery
client = bigquery.Client()
rows = list(client.query("SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_seasons` WHERE season_key = 'ha_2526'").result())
print([dict(r) for r in rows])
