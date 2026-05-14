{{ config(materialized='view') }}

select
  cast(match_id as string) as match_id,
  cast(player_id as string) as player_id,
  cast(team_id as string) as team_id,
  cast(position as string) as position,
  cast(line_number as int64) as line_number,
  cast(pair_number as int64) as pair_number,
  cast(role as string) as role,
  cast(is_starting_goalie as bool) as is_starting_goalie,
  cast(is_backup_goalie as bool) as is_backup_goalie,
  cast(is_scratched as bool) as is_scratched,
  cast(is_captain as bool) as is_captain,
  cast(is_alternate_captain as bool) as is_alternate_captain,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'match_lineups') }}
