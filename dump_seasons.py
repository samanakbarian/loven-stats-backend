from google.cloud import bigquery
bq = bigquery.Client(project="granskaren-d51a1")
for r in bq.query("SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_seasons`").result():
    print(dict(r.items()))
