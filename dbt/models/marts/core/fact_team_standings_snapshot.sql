{{ config(
    materialized='incremental',
    unique_key='standings_key',
    on_schema_change='sync_all_columns'
) }}

with base as (
  select * from {{ ref('stg_team_standings_snapshots') }}
  union all
  select * from {{ ref('stg_swehockey_standings') }}
)
select
  to_hex(md5(concat(coalesce(cast(snapshot_date as string), ''), '|', coalesce(season_id, ''), '|', coalesce(team_id, '')))) as standings_key,
  snapshot_date,
  season_id,
  team_id,
  games_played,
  wins,
  losses,
  ot_wins,
  ot_losses,
  points,
  rank,
  goal_diff,
  form_last_5,
  points_per_game,
  source_system,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from base

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
