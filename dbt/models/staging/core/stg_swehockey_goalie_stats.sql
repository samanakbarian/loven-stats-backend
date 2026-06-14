{{ config(materialized='view') }}

with src as (
  select
    coalesce(cast(season_group_id as string), '') as season_group_id,
    coalesce(cast(team_id as string), '') as team_id,
    coalesce(cast(goalie_name as string), '') as goalie_name,
    coalesce(cast(games_played as int64), 0) as games_played,
    coalesce(cast(shots_against as int64), 0) as shots_against,
    coalesce(cast(saves as int64), 0) as saves,
    coalesce(cast(goals_against as int64), 0) as goals_against,
    coalesce(cast(save_pct as numeric), 0) as save_pct,
    coalesce(cast(gaa as numeric), 0) as gaa,
    coalesce(cast(toi_minutes as int64), 0) as toi_minutes,
    coalesce(cast(source as string), 'swehockey') as source_system,
    cast(run_id as string) as run_id,
    coalesce(cast(scraped_at as timestamp), current_timestamp()) as scraped_at
  from {{ source('raw_sports', 'swehockey_goalie_stats') }}
),
eligible as (
  select src.*
  from src
  left join {{ ref('stg_successful_ingestion_runs') }} successful
    on src.run_id = successful.run_id
  where src.run_id is null or successful.run_id is not null
),
dedup as (
  select
    *,
    {{ dbt_utils.generate_surrogate_key(['season_group_id', 'team_id', 'goalie_name']) }} as sk_goalie_row
  from eligible
  qualify row_number() over (
    partition by season_group_id, team_id, goalie_name
    order by scraped_at desc
  ) = 1
)
select
  sk_goalie_row as goalie_game_id,
  {{ dbt_utils.generate_surrogate_key(['team_id', 'goalie_name']) }} as goalie_id,
  concat('swehockey_season_', season_group_id) as match_id,
  team_id,
  shots_against,
  saves,
  goals_against,
  save_pct,
  cast(null as numeric) as xga,
  cast(null as numeric) as goals_saved_above_expected,
  toi_minutes * 60 as toi_seconds,
  true as started,
  false as pulled,
  0 as empty_net_goals_against,
  source_system,
  run_id as source_run_id,
  sk_goalie_row as source_record_id,
  scraped_at as ingested_at
from dedup
