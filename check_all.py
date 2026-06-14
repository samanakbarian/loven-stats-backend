from google.cloud import bigquery
client = bigquery.Client()
rows = list(client.query('SELECT season_key, season_name, league, regular_season_id, playoff_id, is_active FROM `granskaren-d51a1.raw_sports.swehockey_seasons`').result())
for r in rows:
    print(dict(r))
