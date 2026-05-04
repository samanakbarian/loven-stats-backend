{{ config(materialized='view') }}

select
  cast(article_id as string) as article_id,
  cast(title as string) as title,
  cast(tag as string) as tag,
  cast(source as string) as source,
  cast(url as string) as url,
  cast(date as date) as article_date,
  cast(time as string) as article_time,
  cast(priority as string) as priority,
  cast(scraped_at as timestamp) as scraped_at
from {{ source('raw_content', 'silly_articles') }}
