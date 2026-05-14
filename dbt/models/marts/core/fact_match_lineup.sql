{{ config(
    materialized='incremental',
    unique_key='lineup_key',
    on_schema_change='sync_all_columns'
) }}

select
  to_hex(md5(concat(coalesce(match_id, ''), '|', coalesce(player_id, ''), '|', coalesce(position, '')))) as lineup_key,
  match_id,
  player_id,
  team_id,
  position,
  line_number,
  pair_number,
  role,
  is_starting_goalie,
  is_backup_goalie,
  is_scratched,
  is_captain,
  is_alternate_captain,
  source_system,
  source_record_id,
  ingested_at,
  current_timestamp() as dbt_loaded_at
from {{ ref('stg_match_lineups') }}

{% if is_incremental() %}
where ingested_at > (select coalesce(max(ingested_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
