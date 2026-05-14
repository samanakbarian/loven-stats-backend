{{ config(
    materialized='incremental',
    unique_key='attendance_key',
    on_schema_change='sync_all_columns'
) }}

select
  to_hex(md5(concat(coalesce(match_id, ''), '|', coalesce(team_id, '')))) as attendance_key,
  match_id,
  team_id,
  attendance,
  arena_capacity,
  attendance_pct,
  ticket_revenue_estimate,
  source_system,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from {{ ref('stg_attendance') }}

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
