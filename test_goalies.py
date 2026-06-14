from google.cloud import bigquery
client = bigquery.Client()
q = "SELECT goalie_name, team_name FROM `granskaren-d51a1.raw_sports.swehockey_goalie_stats` WHERE season_group_id = 20961"
goalies = []
for r in client.query(q).result():
    goalies.append(r["goalie_name"])
print(f"Total goalies: {len(goalies)}")
if len(goalies) > 0:
    print(goalies[:10])
