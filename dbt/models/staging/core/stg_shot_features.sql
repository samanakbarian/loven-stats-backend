{{ config(materialized='view') }}

select
  cast(event_id as string) as event_id,
  cast(match_id as string) as match_id,
  cast(player_id as string) as player_id,
  cast(team_id as string) as team_id,
  cast(shot_type as string) as shot_type,
  cast(shot_distance_m as numeric) as shot_distance_m,
  cast(shot_angle as numeric) as shot_angle,
  cast(x_coordinate as numeric) as x_coordinate,
  cast(y_coordinate as numeric) as y_coordinate,
  cast(is_rebound as bool) as is_rebound,
  cast(is_rush as bool) as is_rush,
  cast(is_power_play as bool) as is_power_play,
  cast(is_empty_net as bool) as is_empty_net,
  cast(previous_event_type as string) as previous_event_type,
  cast(seconds_since_previous_event as int64) as seconds_since_previous_event,
  cast(goalie_id as string) as goalie_id,
  cast(model_version as string) as model_version,
  cast(xg as numeric) as xg,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'shot_features') }}
