from google.cloud import bigquery
client = bigquery.Client()
for row in client.query("SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_seasons`").result():
    print(dict(row))
