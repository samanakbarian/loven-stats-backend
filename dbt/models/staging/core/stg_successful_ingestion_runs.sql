{{ config(materialized='view') }}

with latest_event as (
  select *
  from {{ source('raw_ops', 'ingestion_runs') }}
  qualify row_number() over (
    partition by run_id
    order by finished_at is null, finished_at desc, started_at desc, event_id desc
  ) = 1
)
select
  cast(run_id as string) as run_id,
  cast(pipeline_name as string) as pipeline_name,
  cast(source as string) as source_system,
  cast(started_at as timestamp) as started_at,
  cast(finished_at as timestamp) as finished_at,
  cast(fetched_rows as int64) as fetched_rows,
  cast(loaded_rows as int64) as loaded_rows,
  cast(metadata_json as string) as metadata_json
from latest_event
where status = 'SUCCESS'
