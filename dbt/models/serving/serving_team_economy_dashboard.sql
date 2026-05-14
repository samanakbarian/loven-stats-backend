{{ config(materialized='view') }}

with latest_period as (
  select
    *,
    row_number() over (
      partition by team_id, season_id
      order by updated_at desc, reporting_period desc
    ) as rn
  from {{ ref('fact_team_financials') }}
)

select
  team_id,
  season_id,
  reporting_period,
  revenue_total,
  ticket_revenue,
  sponsorship_revenue,
  broadcast_revenue,
  merchandise_revenue,
  player_salary_cost,
  staff_cost,
  arena_cost,
  travel_cost,
  operating_result,
  equity,
  cash,
  debt,
  confidence_level,
  source_document_url
from latest_period
where rn = 1
