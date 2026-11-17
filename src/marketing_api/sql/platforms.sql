-- Ad platforms side by side, with the gap between the revenue each platform
-- claims and the revenue GA4 attributes to it.
with totals as (
    select
        platform,
        count(distinct campaign_id) as active_campaigns,
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
    group by platform
)

select
    platform,
    active_campaigns,
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
    -- Share of platform-claimed revenue that GA4 does not see
    round(1 - safe_divide(revenue, conversion_value), 4) as attribution_gap
from totals
order by spend desc, platform
