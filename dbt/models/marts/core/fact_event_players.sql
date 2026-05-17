{{ config(
    materialized='incremental',
    unique_key='event_player_key',
    on_schema_change='sync_all_columns'
) }}

with base as (
  select * from {{ ref('stg_event_players') }}
  union all
  select * from {{ ref('stg_swehockey_player_stats') }}
)
select
  to_hex(md5(concat(coalesce(event_id, ''), '|', coalesce(player_id, ''), '|', coalesce(event_player_role, '')))) as event_player_key,
  event_id,
  player_id,
  team_id,
  event_player_role,
  source_system,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from base

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
