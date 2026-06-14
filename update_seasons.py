from google.cloud import bigquery
client = bigquery.Client()
client.query("UPDATE `granskaren-d51a1.raw_sports.swehockey_seasons` SET is_active = FALSE WHERE is_active = TRUE").result()
client.query("DELETE FROM `granskaren-d51a1.raw_sports.swehockey_seasons` WHERE season_key = 'shl_2526'").result()
client.query("INSERT INTO `granskaren-d51a1.raw_sports.swehockey_seasons` (season_key, season_name, league, regular_season_id, playoff_id, start_date, is_active) VALUES ('shl_2526', 'SHL 2025/26', 'SHL', 20961, NULL, '2025-09-01', TRUE)").result()
print('Added shl_2526 with league SHL')
