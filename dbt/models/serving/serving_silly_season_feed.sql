{{ config(materialized='view') }}

select
  content_id,
  source,
  source_url as url,
  published_at,
  scraped_at,
  title,
  content_type,
  language,
  team_id,
  deduplication_key
from {{ ref('fact_content_items') }}
order by scraped_at desc
