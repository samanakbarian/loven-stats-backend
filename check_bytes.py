from google.cloud import bigquery
client = bigquery.Client()
rows = list(client.query("SELECT DISTINCT home_team FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id=20961 AND home_team LIKE '%Bj%'").result())
if rows:
    t = rows[0]['home_team']
    print("repr:", repr(t))
    print("bytes:", t.encode('utf-8'))
else:
    print("No teams found")
