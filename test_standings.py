from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT season_group_id, COUNT(*) as c FROM `granskaren-d51a1.raw_sports.swehockey_standings` GROUP BY season_group_id"
for r in client.query(q).result():
    print(dict(r.items()))
