from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT COUNT(*) as c, MAX(games_played) as mg FROM `granskaren-d51a1.raw_sports.swehockey_standings` WHERE season_group_id = 20822"
for r in client.query(q).result():
    print(dict(r.items()))
