{{ config(
    materialized='incremental',
    unique_key='event_id',
    on_schema_change='sync_all_columns'
) }}

select
  event_id,
  match_id,
  player_id,
  team_id,
  shot_type,
  shot_distance_m,
  shot_angle,
  x_coordinate,
  y_coordinate,
  is_rebound,
  is_rush,
  is_power_play,
  is_empty_net,
  previous_event_type,
  seconds_since_previous_event,
  goalie_id,
  model_version,
  xg,
  source_system,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from {{ ref('stg_shot_features') }}

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
