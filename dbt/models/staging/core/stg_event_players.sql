{{ config(materialized='view') }}

select
  cast(event_id as string) as event_id,
  cast(player_id as string) as player_id,
  cast(team_id as string) as team_id,
  cast(event_player_role as string) as event_player_role,
  cast(source_system as string) as source_system,
  cast(null as string) as source_run_id,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'event_players') }}
