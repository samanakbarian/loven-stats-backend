from google.cloud import bigquery

client = bigquery.Client(project='granskaren-d51a1')

sql_create = """
CREATE TABLE IF NOT EXISTS `granskaren-d51a1.raw_sports.swehockey_seasons` (
  season_key STRING NOT NULL,
  season_name STRING NOT NULL,
  league STRING NOT NULL,
  regular_season_id INT64 NOT NULL,
  playoff_id INT64,
  start_date DATE,
  end_date DATE,
  is_active BOOL DEFAULT TRUE
);
"""

sql_insert = """
MERGE `granskaren-d51a1.raw_sports.swehockey_seasons` T
USING (SELECT 'ha_2526' as season_key, 'HockeyAllsvenskan 2025/26' as season_name, 'HA' as league, 18266 as regular_season_id, 19979 as playoff_id, DATE('2025-09-19') as start_date, DATE('2026-03-15') as end_date, TRUE as is_active) S
ON T.season_key = S.season_key
WHEN NOT MATCHED THEN
  INSERT (season_key, season_name, league, regular_season_id, playoff_id, start_date, end_date, is_active)
  VALUES (S.season_key, S.season_name, S.league, S.regular_season_id, S.playoff_id, S.start_date, S.end_date, S.is_active);
"""

print('Creating table...')
client.query(sql_create).result()
print('Inserting row...')
client.query(sql_insert).result()
print('Done!')
