{{ config(materialized='view') }}

select
  cast(goalie_game_id as string) as goalie_game_id,
  cast(goalie_id as string) as goalie_id,
  cast(match_id as string) as match_id,
  cast(team_id as string) as team_id,
  cast(shots_against as int64) as shots_against,
  cast(saves as int64) as saves,
  cast(goals_against as int64) as goals_against,
  cast(save_pct as numeric) as save_pct,
  cast(xga as numeric) as xga,
  cast(goals_saved_above_expected as numeric) as goals_saved_above_expected,
  cast(toi_seconds as int64) as toi_seconds,
  cast(started as bool) as started,
  cast(pulled as bool) as pulled,
  cast(empty_net_goals_against as int64) as empty_net_goals_against,
  cast(source_system as string) as source_system,
  cast(null as string) as source_run_id,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'goalie_game_stats') }}
