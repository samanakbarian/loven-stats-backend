{{ config(materialized='view') }}

with src as (
  select
    coalesce(cast(season_group_id as string), '') as season_group_id,
    coalesce(cast(team_id as string), '') as team_id,
    cast(match_date as date) as match_date,
    coalesce(cast(home_team as string), '') as home_team,
    coalesce(cast(away_team as string), '') as away_team,
    coalesce(cast(result as string), '') as result,
    coalesce(cast(status as string), '') as status,
    coalesce(cast(source as string), 'swehockey') as source_system,
    cast(run_id as string) as run_id,
    coalesce(cast(scraped_at as timestamp), current_timestamp()) as scraped_at
  from {{ source('raw_sports', 'swehockey_schedule') }}
),
eligible as (
  select src.*
  from src
  left join {{ ref('stg_successful_ingestion_runs') }} successful
    on src.run_id = successful.run_id
  where src.run_id is null or successful.run_id is not null
)
select
  {{ dbt_utils.generate_surrogate_key([
    'season_group_id',
    'match_date',
    'home_team',
    'away_team'
  ]) }} as match_id,
  season_group_id,
  team_id,
  match_date,
  home_team,
  away_team,
  result,
  status,
  source_system,
  run_id as source_run_id,
  scraped_at as ingested_at
from eligible
qualify row_number() over (
  partition by season_group_id, match_date, home_team, away_team
  order by scraped_at desc
) = 1
