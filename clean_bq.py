from google.cloud import bigquery
client = bigquery.Client()

# 1. Delete shl_2526 from swehockey_seasons
q1 = "DELETE FROM `granskaren-d51a1.raw_sports.swehockey_seasons` WHERE season_key = 'shl_2526'"
client.query(q1).result()

# 2. Delete corrupted rows from swehockey_schedule
q2 = "DELETE FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id = 20961 AND home_team LIKE '%Bjrklven%'"
client.query(q2).result()

# 3. Delete corrupted rows from player stats
q3 = "DELETE FROM `granskaren-d51a1.raw_sports.swehockey_player_stats` WHERE season_group_id = 20961"
client.query(q3).result()

# 4. Delete corrupted rows from goalie stats
q4 = "DELETE FROM `granskaren-d51a1.raw_sports.swehockey_goalie_stats` WHERE season_group_id = 20961"
client.query(q4).result()

# 5. Delete corrupted rows from standings
q5 = "DELETE FROM `granskaren-d51a1.raw_sports.swehockey_standings` WHERE season_group_id = 20961 AND team_name LIKE '%Bjrklven%'"
client.query(q5).result()

print("Cleaned BQ")
