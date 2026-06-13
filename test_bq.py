from google.cloud import bigquery

client = bigquery.Client(project='granskaren-d51a1')
query = """
SELECT season_group_id, count(*) as c 
FROM `granskaren-d51a1.raw_sports.swehockey_schedule` 
WHERE LOWER(home_team) LIKE '%björk%' OR LOWER(away_team) LIKE '%björk%'
GROUP BY 1 ORDER BY 1
"""
rows = client.query(query).result()
for r in rows:
    print(r)
