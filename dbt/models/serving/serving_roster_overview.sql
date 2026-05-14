{{ config(materialized='view') }}

with latest_status as (
  select
    *,
    row_number() over (
      partition by player_id, season_id
      order by status_date desc, updated_at desc
    ) as rn
  from {{ ref('fact_roster_status') }}
)

select
  player_id,
  team_id,
  season_id,
  status_date as latest_status_date,
  status as latest_status,
  status_reason,
  is_available,
  is_loaned,
  is_injured,
  is_junior,
  is_import,
  depth_role,
  line_role
from latest_status
where rn = 1
