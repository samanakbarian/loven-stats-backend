from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT DISTINCT home_team FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id = 20961 AND home_team LIKE '%Bj%ven%'"
rows = list(client.query(q).result())
for r in rows:
    print(r.home_team.encode('utf-8'))
