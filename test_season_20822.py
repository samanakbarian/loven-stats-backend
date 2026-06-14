from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT * FROM `granskaren-d51a1.raw_sports.swehockey_seasons` WHERE regular_season_id = 20822 OR playoff_id = 20822"
for r in client.query(q).result():
    print(dict(r.items()))
