from google.cloud import bigquery
client = bigquery.Client()
q = "INSERT INTO `granskaren-d51a1.raw_sports.swehockey_seasons` (season_key, season_name, league, regular_season_id, is_active) VALUES ('shl_2425', 'SHL 2024/25', 'SHL', 16147, TRUE)"
client.query(q).result()
print('Inserted 16147')
