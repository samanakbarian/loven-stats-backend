{{ config(
    materialized='incremental',
    unique_key='content_id',
    on_schema_change='sync_all_columns'
) }}

with base as (
    select
      cast(article_id as string) as source_record_id,
      cast(source as string) as source,
      cast(url as string) as source_url,
      cast(article_date as timestamp) as published_at,
      cast(scraped_at as timestamp) as scraped_at,
      cast(null as string) as author,
      cast(title as string) as title,
      cast(null as string) as body_text,
      cast('news' as string) as content_type,
      cast('sv' as string) as language,
      cast('if_bjorkloven' as string) as team_id,
      cast(null as array<string>) as player_ids,
      to_hex(md5(concat(coalesce(title, ''), '|', coalesce(url, '')))) as hash,
      to_hex(md5(concat(coalesce(source, ''), '|', coalesce(url, '')))) as deduplication_key
    from {{ ref('stg_silly_articles') }}
)

select
  to_hex(md5(concat(coalesce(source, ''), '|', coalesce(source_url, ''), '|', coalesce(cast(scraped_at as string), '')))) as content_id,
  source,
  source_url,
  published_at,
  scraped_at,
  author,
  title,
  body_text,
  content_type,
  language,
  team_id,
  player_ids,
  hash,
  deduplication_key,
  source_record_id,
  current_timestamp() as dbt_loaded_at
from base

{% if is_incremental() %}
where scraped_at > (select coalesce(max(scraped_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
