from google.cloud import bigquery
client = bigquery.Client()
q = 'SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_seasons` WHERE season_key = "ha_2526"'
for row in client.query(q).result():
    print(dict(row.items()))
