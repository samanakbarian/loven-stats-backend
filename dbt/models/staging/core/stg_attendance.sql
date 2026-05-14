{{ config(materialized='view') }}

select
  cast(match_id as string) as match_id,
  cast(team_id as string) as team_id,
  cast(attendance as int64) as attendance,
  cast(arena_capacity as int64) as arena_capacity,
  cast(attendance_pct as numeric) as attendance_pct,
  cast(ticket_revenue_estimate as numeric) as ticket_revenue_estimate,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at
from {{ source('raw_sports', 'attendance') }}
