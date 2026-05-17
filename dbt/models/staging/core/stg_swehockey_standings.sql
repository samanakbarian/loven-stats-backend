{{ config(materialized='view') }}

with src as (
  select
    coalesce(cast(season_group_id as string), '') as season_id,
    coalesce(cast(team_name as string), '') as team_name,
    coalesce(cast(rank as int64), 0) as rank,
    coalesce(cast(games_played as int64), 0) as games_played,
    coalesce(cast(wins as int64), 0) as wins,
    coalesce(cast(losses as int64), 0) as losses,
    coalesce(cast(ot_wins as int64), 0) as ot_wins,
    coalesce(cast(ot_losses as int64), 0) as ot_losses,
    coalesce(cast(points as int64), 0) as points,
    coalesce(cast(goal_diff as int64), 0) as goal_diff,
    coalesce(cast(source as string), 'swehockey') as source_system,
    coalesce(cast(scraped_at as timestamp), current_timestamp()) as scraped_at
  from {{ source('raw_sports', 'swehockey_standings') }}
),
dedup as (
  select
    *,
    {{ dbt_utils.generate_surrogate_key(['season_id', 'team_name']) }} as sk_team_row
  from src
  qualify row_number() over (
    partition by season_id, team_name
    order by scraped_at desc
  ) = 1
)
select
  cast(scraped_at as date) as snapshot_date,
  season_id,
  sk_team_row as team_id,
  games_played,
  wins,
  losses,
  ot_wins,
  ot_losses,
  points,
  rank,
  goal_diff,
  '' as form_last_5,
  case when games_played = 0 then 0 else cast(points as numeric) / games_played end as points_per_game,
  source_system,
  sk_team_row as source_record_id,
  scraped_at as ingested_at
from dedup

