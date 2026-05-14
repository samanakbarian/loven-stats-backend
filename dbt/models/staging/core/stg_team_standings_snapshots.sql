{{ config(materialized='view') }}

select
  cast(snapshot_date as date) as snapshot_date,
  cast(season_id as string) as season_id,
  cast(team_id as string) as team_id,
  cast(games_played as int64) as games_played,
  cast(wins as int64) as wins,
  cast(losses as int64) as losses,
  cast(ot_wins as int64) as ot_wins,
  cast(ot_losses as int64) as ot_losses,
  cast(points as int64) as points,
  cast(rank as int64) as rank,
  cast(goal_diff as int64) as goal_diff,
  cast(form_last_5 as string) as form_last_5,
  cast(points_per_game as numeric) as points_per_game,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'team_standings_snapshots') }}
