{{ config(materialized='view') }}

select
  cast(roster_status_id as string) as roster_status_id,
  cast(player_id as string) as player_id,
  cast(team_id as string) as team_id,
  cast(season_id as string) as season_id,
  cast(status_date as date) as status_date,
  cast(status as string) as status,
  cast(status_reason as string) as status_reason,
  cast(source as string) as source,
  cast(is_available as bool) as is_available,
  cast(is_loaned as bool) as is_loaned,
  cast(is_injured as bool) as is_injured,
  cast(is_junior as bool) as is_junior,
  cast(is_import as bool) as is_import,
  cast(depth_role as string) as depth_role,
  cast(line_role as string) as line_role,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at,
  cast(updated_at as timestamp) as updated_at
from {{ source('raw_roster', 'roster_status_events') }}
