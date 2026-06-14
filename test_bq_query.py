from google.cloud import bigquery
client = bigquery.Client()
q = '''
SELECT a.* FROM `granskaren-d51a1.raw_sports.swehockey_schedule` a 
INNER JOIN (SELECT MAX(scraped_at) as max_s FROM `granskaren-d51a1.raw_sports.swehockey_schedule` WHERE season_group_id = 20961) b 
ON a.scraped_at = b.max_s 
WHERE a.season_group_id = 20961 ORDER BY a.match_date
'''
rows = list(client.query(q).result())
print('Games:', len(rows))
