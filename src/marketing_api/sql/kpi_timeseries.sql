-- KPIs per day, week (starting Monday) or month. The grain expression comes
-- from a fixed whitelist in code, never from the request.
with totals as (
    select
        cast({{ period_expression }} as date) as period_start,
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
    group by period_start
)

select
    period_start,
    impressions,
    clicks,
    round(spend, 2) as spend,
    round(conversions, 2) as conversions,
    round(conversion_value, 2) as conversion_value,
    sessions,
    transactions,
    round(revenue, 2) as revenue,
    {{ ratio_columns }}
from totals
order by period_start
