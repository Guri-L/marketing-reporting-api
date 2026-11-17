-- Campaign leaderboard for a period: totals, share of spend, rank, and the
-- total row count so the API can paginate without a second query.
with totals as (
    select
        platform,
        campaign_id,
        -- A campaign can be renamed; report the most recent name.
        max_by(campaign_name, date) as campaign_name,
        max_by(campaign_type, date) as campaign_type,
        min(date) as first_date,
        max(date) as last_date,
        count(distinct date) as active_days,
        sum(impressions) as impressions,
        sum(clicks) as clicks,
        sum(spend) as spend,
        sum(platform_conversions) as conversions,
        sum(platform_conversion_value) as conversion_value,
        sum(ga4_sessions) as sessions,
        sum(ga4_transactions) as transactions,
        sum(ga4_revenue) as revenue
    from {{ rpt_paid_media_campaign_daily }}
    where date between @start_date and @end_date
        and (@platform is null or platform = @platform)
    group by platform, campaign_id
),

ranked as (
    select
        platform,
        campaign_id,
        campaign_name,
        campaign_type,
        first_date,
        last_date,
        active_days,
        impressions,
        clicks,
        round(spend, 2) as spend,
        round(conversions, 2) as conversions,
        round(conversion_value, 2) as conversion_value,
        sessions,
        transactions,
        round(revenue, 2) as revenue,
        {{ ratio_columns }},
        round(safe_divide(spend, sum(spend) over ()), 4) as spend_share,
        rank() over (order by spend desc) as spend_rank
    from totals
)

select
    *,
    count(*) over () as total_rows
from ranked
-- The search filter is applied after ranking so rank and share stay relative
-- to the whole period, not to the filtered list.
where @search is null or strpos(lower(campaign_name), lower(@search)) > 0
order by {{ order_by }}
limit @limit offset @offset
