{{ config(materialized='view') }}

with src as (
  select
    coalesce(cast(season_group_id as string), '') as season_group_id,
    coalesce(cast(team_id as string), '') as team_id,
    coalesce(cast(player_name as string), '') as player_name,
    coalesce(cast(position as string), '') as position,
    coalesce(cast(jersey_number as int64), 0) as jersey_number,
    coalesce(cast(games_played as int64), 0) as games_played,
    coalesce(cast(goals as int64), 0) as goals,
    coalesce(cast(assists as int64), 0) as assists,
    coalesce(cast(points as int64), 0) as points,
    coalesce(cast(plus_minus as int64), 0) as plus_minus,
    coalesce(cast(pim as int64), 0) as pim,
    coalesce(cast(source as string), 'swehockey') as source_system,
    cast(run_id as string) as run_id,
    coalesce(cast(scraped_at as timestamp), current_timestamp()) as scraped_at
  from {{ source('raw_sports', 'swehockey_player_stats') }}
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
    {{ dbt_utils.generate_surrogate_key(['season_group_id', 'team_id', 'player_name']) }} as sk_player_row
  from eligible
  qualify row_number() over (
    partition by season_group_id, team_id, player_name
    order by scraped_at desc
  ) = 1
)
select
  concat('swehockey_player_stats_', season_group_id, '_', team_id) as event_id,
  {{ dbt_utils.generate_surrogate_key(['team_id', 'player_name']) }} as player_id,
  team_id,
  'SEASON_STAT' as event_player_role,
  source_system,
  run_id as source_run_id,
  sk_player_row as source_record_id,
  scraped_at as ingested_at
from dedup
