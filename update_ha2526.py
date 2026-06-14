from google.cloud import bigquery
client = bigquery.Client()
client.query("UPDATE `granskaren-d51a1.raw_sports.swehockey_seasons` SET season_name = 'SHL 2025/26', league = 'SHL', regular_season_id = 20961, is_active = TRUE WHERE season_key = 'ha_2526'").result()
print('Updated ha_2526 to SHL')
