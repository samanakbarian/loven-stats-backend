from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT team_code FROM `granskaren-d51a1.raw_sports.swehockey_goalie_stats` WHERE season_group_id = 18266 LIMIT 5"
for r in client.query(q).result():
    print(dict(r.items()))
