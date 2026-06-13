import re

with open('api/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_query_season = """        def _query_season(table_name: str, season_ids: list[int]):
            \"\"\"Return rows from the table filtered by season_group_id.\"\"\"
            if not season_ids:
                return []
            ids_str = ",".join(str(sid) for sid in season_ids if sid)
            q = f\"\"\"
            SELECT * FROM `{bq_client.project}.raw_sports.{table_name}`
            WHERE season_group_id IN ({ids_str}) -- cache bust 1
            \"\"\"
            return [dict(row.items()) for row in bq_client.query(q).result()]"""

new_query_season = """        def _query_season(table_name: str, season_ids: list[int]):
            \"\"\"Return rows from the table filtered by season_group_id.\"\"\"
            if not season_ids:
                return []
            ids_str = ",".join(str(sid) for sid in season_ids if sid)
            partition_key = "player_name" if "player" in table_name else "goalie_name" if "goalie" in table_name else "team_name" if "standings" in table_name else "game_id" if "events" in table_name else "match_date" if "schedule" in table_name else "id"
            q = f\"\"\"
            SELECT * FROM `{bq_client.project}.raw_sports.{table_name}`
            WHERE season_group_id IN ({ids_str})
            QUALIFY ROW_NUMBER() OVER(PARTITION BY season_group_id, {partition_key} ORDER BY scraped_at DESC) = 1
            \"\"\"
            return [dict(row.items()) for row in bq_client.query(q).result()]"""

content = content.replace(old_query_season, new_query_season)

old_q_schedule = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_schedule` WHERE season_group_id = {REGULAR_ID} ORDER BY match_date")'
new_q_schedule = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_schedule` WHERE season_group_id = {REGULAR_ID} QUALIFY ROW_NUMBER() OVER(PARTITION BY match_date ORDER BY scraped_at DESC) = 1 ORDER BY match_date")'
content = content.replace(old_q_schedule, new_q_schedule)

old_q_players = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = {REGULAR_ID}")'
new_q_players = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = {REGULAR_ID} QUALIFY ROW_NUMBER() OVER(PARTITION BY player_name ORDER BY scraped_at DESC) = 1")'
content = content.replace(old_q_players, new_q_players)

old_q_goalies = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = {REGULAR_ID}")'
new_q_goalies = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = {REGULAR_ID} QUALIFY ROW_NUMBER() OVER(PARTITION BY goalie_name ORDER BY scraped_at DESC) = 1")'
content = content.replace(old_q_goalies, new_q_goalies)

old_q_standings = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_standings` WHERE season_group_id = {REGULAR_ID}")'
new_q_standings = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_standings` WHERE season_group_id = {REGULAR_ID} QUALIFY ROW_NUMBER() OVER(PARTITION BY team_name ORDER BY scraped_at DESC) = 1")'
content = content.replace(old_q_standings, new_q_standings)

old_shl_players = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = {shl_regular_id}")'
new_shl_players = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_player_stats` WHERE season_group_id = {shl_regular_id} QUALIFY ROW_NUMBER() OVER(PARTITION BY player_name ORDER BY scraped_at DESC) = 1")'
content = content.replace(old_shl_players, new_shl_players)

old_shl_goalies = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = {shl_regular_id}")'
new_shl_goalies = 'q(f"SELECT * FROM `{proj}.raw_sports.swehockey_goalie_stats` WHERE season_group_id = {shl_regular_id} QUALIFY ROW_NUMBER() OVER(PARTITION BY goalie_name ORDER BY scraped_at DESC) = 1")'
content = content.replace(old_shl_goalies, new_shl_goalies)


with open('api/main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched main.py successfully")
