{{ config(
    materialized='incremental',
    unique_key='financial_key',
    on_schema_change='sync_all_columns'
) }}

select
  to_hex(md5(concat(coalesce(team_id, ''), '|', coalesce(season_id, ''), '|', coalesce(reporting_period, '')))) as financial_key,
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
  source_document_url,
  confidence_level,
  source_system,
  source_record_id,
  ingested_at,
  updated_at,
  current_timestamp() as dbt_loaded_at
from {{ ref('stg_team_financials') }}

{% if is_incremental() %}
where updated_at > (select coalesce(max(updated_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
