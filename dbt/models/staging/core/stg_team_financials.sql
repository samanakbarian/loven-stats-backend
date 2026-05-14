{{ config(materialized='view') }}

select
  cast(team_id as string) as team_id,
  cast(season_id as string) as season_id,
  cast(reporting_period as string) as reporting_period,
  cast(revenue_total as numeric) as revenue_total,
  cast(ticket_revenue as numeric) as ticket_revenue,
  cast(sponsorship_revenue as numeric) as sponsorship_revenue,
  cast(broadcast_revenue as numeric) as broadcast_revenue,
  cast(merchandise_revenue as numeric) as merchandise_revenue,
  cast(player_salary_cost as numeric) as player_salary_cost,
  cast(staff_cost as numeric) as staff_cost,
  cast(arena_cost as numeric) as arena_cost,
  cast(travel_cost as numeric) as travel_cost,
  cast(operating_result as numeric) as operating_result,
  cast(equity as numeric) as equity,
  cast(cash as numeric) as cash,
  cast(debt as numeric) as debt,
  cast(source_document_url as string) as source_document_url,
  cast(confidence_level as string) as confidence_level,
  cast(source_system as string) as source_system,
  cast(source_record_id as string) as source_record_id,
  cast(ingested_at as timestamp) as ingested_at,
  cast(updated_at as timestamp) as updated_at
from {{ source('raw_financials', 'team_financials') }}
