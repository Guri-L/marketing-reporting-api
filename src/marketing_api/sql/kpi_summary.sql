-- Totals for the requested period and for the period of equal length before it,
-- in one scan. Returns one row per period that has data.
with totals as (
    select
        case when date >= @start_date then 'current' else 'previous' end as period,
        sum(impressions) as impressions,
        sum(clicks) as clicks,
        sum(spend) as spend,
        sum(platform_conversions) as conversions,
        sum(platform_conversion_value) as conversion_value,
        sum(ga4_sessions) as sessions,
        sum(ga4_transactions) as transactions,
        sum(ga4_revenue) as revenue,
        count(distinct campaign_id) as active_campaigns
    from {{ rpt_paid_media_campaign_daily }}
    where date between @previous_start and @end_date
        and (@platform is null or platform = @platform)
    group by period
)

select
    period,
    impressions,
    clicks,
    round(spend, 2) as spend,
    round(conversions, 2) as conversions,
    round(conversion_value, 2) as conversion_value,
    sessions,
    transactions,
    round(revenue, 2) as revenue,
    active_campaigns,
    {{ ratio_columns }}
from totals
