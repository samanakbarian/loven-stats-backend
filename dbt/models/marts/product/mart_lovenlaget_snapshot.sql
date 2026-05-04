{{ config(
    materialized='incremental',
    unique_key='snapshot_id',
    on_schema_change='sync_all_columns',
    partition_by={"field": "snapshot_at", "data_type": "timestamp"},
    cluster_by=["season_id", "freshness_status"]
) }}

{% set use_real_source = var('use_real_silly_source', false) %}

{% if use_real_source %}

with source_stats as (
    select
        max(scraped_at) as source_updated_at,
        count(*) as scraped_articles,
        countif(date(scraped_at) = current_date('Europe/Stockholm')) as new_signals
    from {{ ref('stg_silly_articles') }}
),
latest_signal as (
    select
        title,
        priority
    from {{ ref('stg_silly_articles') }}
    order by scraped_at desc
    limit 1
),
snapshot as (
    select
        concat(
            'sr_season_2026_2027_shl',
            '_',
            format_timestamp('%Y%m%d%H%M%S', current_timestamp())
        ) as snapshot_id,
        current_timestamp() as snapshot_at,
        'sr_season_2026_2027_shl' as season_id,
        'SHL' as league,
        68 as readiness_score,
        'Nära, men två luckor kan sänka bygget.' as readiness_summary,
        'Toppback saknas' as critical_1,
        'Centerdjup osäkert' as critical_2,
        'Ekonomiskt tryck måste bevakas' as critical_3,
        coalesce((select title from latest_signal), 'Inga nya signaler ännu') as latest_impact_title,
        case
            when (select priority from latest_signal) = 'breaking' then 'high'
            when (select priority from latest_signal) = 'high' then 'high'
            when (select priority from latest_signal) = 'normal' then 'medium'
            else 'medium'
        end as latest_impact_level,
        'Det här flyttar nålen direkt och påverkar lagbalansen.' as latest_impact_meaning,
        'stabilt' as goalies_status,
        'kritisk lucka' as defense_status,
        'bevaka' as centers_status,
        'stabilt' as forwards_status,
        'medel' as economy_risk_level,
        'högt' as economy_budget_pressure,
        'Har klubben råd med två spetsnamn?' as economy_next_question,
        (select source_updated_at from source_stats) as source_updated_at,
        case
            when (select source_updated_at from source_stats) is null then 'unknown'
            when timestamp_diff(current_timestamp(), (select source_updated_at from source_stats), hour) <= 6 then 'fresh'
            when timestamp_diff(current_timestamp(), (select source_updated_at from source_stats), hour) <= 24 then 'stale'
            else 'critical'
        end as freshness_status,
        coalesce((select new_signals from source_stats), 0) as new_signals,
        coalesce((select scraped_articles from source_stats), 0) as scraped_articles,
        0 as expiring_contracts,
        'v1' as schema_version
)

select * from snapshot

{% else %}

with snapshot as (
    select
        concat(
            'sr_season_2026_2027_shl',
            '_',
            format_timestamp('%Y%m%d%H%M%S', current_timestamp())
        ) as snapshot_id,
        current_timestamp() as snapshot_at,
        'sr_season_2026_2027_shl' as season_id,
        'SHL' as league,
        68 as readiness_score,
        'Nära, men två luckor kan sänka bygget.' as readiness_summary,
        'Toppback saknas' as critical_1,
        'Centerdjup osäkert' as critical_2,
        'Ekonomiskt tryck måste bevakas' as critical_3,
        'Rykte: rutinerad center' as latest_impact_title,
        'medium' as latest_impact_level,
        'Det här flyttar nålen direkt och påverkar lagbalansen.' as latest_impact_meaning,
        'stabilt' as goalies_status,
        'kritisk lucka' as defense_status,
        'bevaka' as centers_status,
        'stabilt' as forwards_status,
        'medel' as economy_risk_level,
        'högt' as economy_budget_pressure,
        'Har klubben råd med två spetsnamn?' as economy_next_question,
        current_timestamp() as source_updated_at,
        'fresh' as freshness_status,
        0 as new_signals,
        0 as scraped_articles,
        0 as expiring_contracts,
        'v1' as schema_version
)

select * from snapshot

{% endif %}

{% if is_incremental() %}
where snapshot_at > (select coalesce(max(snapshot_at), timestamp('1970-01-01')) from {{ this }})
{% endif %}
