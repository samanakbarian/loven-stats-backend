{{ config(
    materialized='incremental',
    unique_key='roster_status_id',
    on_schema_change='sync_all_columns'
) }}

select
  roster_status_id,
  player_id,
  team_id,
  season_id,
  status_date,
  status,
  status_reason,
  source,
  is_available,
  is_loaned,
  is_injured,
  is_junior,
  is_import,
  depth_role,
  line_role,
  source_system,
  source_record_id,
  ingested_at,
  updated_at,
  current_timestamp() as dbt_loaded_at,
  cast(null as string) as data_quality_status,
  cast(null as numeric) as confidence_score
from {{ ref('stg_roster_status_events') }}

{% if is_incremental() %}
where updated_at > (select coalesce(max(updated_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
