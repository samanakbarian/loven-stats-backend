{{ config(materialized='view') }}

select
  s.snapshot_date,
  s.season_id,
  s.team_id,
  s.games_played,
  s.points,
  s.rank,
  s.goal_diff,
  s.form_last_5,
  s.points_per_game
from {{ ref('fact_team_standings_snapshot') }} s
