{{ config(
    materialized='incremental',
    unique_key='goalie_game_id',
    on_schema_change='sync_all_columns'
) }}

with base as (
  select * from {{ ref('stg_goalie_game_stats') }}
  union all
  select * from {{ ref('stg_swehockey_goalie_stats') }}
)
select
  goalie_game_id,
  goalie_id,
  match_id,
  team_id,
  shots_against,
  saves,
  goals_against,
  save_pct,
  xga,
  goals_saved_above_expected,
  toi_seconds,
  started,
  pulled,
  empty_net_goals_against,
  source_system,
  source_run_id,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from base

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
