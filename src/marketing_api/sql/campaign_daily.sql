-- One campaign's daily figures with a 7-day moving average of spend.
with daily as (
    select
        date,
        platform,
        campaign_id,
        campaign_name,
        campaign_type,
        impressions,
        clicks,
        spend,
        platform_conversions as conversions,
        platform_conversion_value as conversion_value,
        ga4_sessions as sessions,
        ga4_transactions as transactions,
        ga4_revenue as revenue
    from {{ rpt_paid_media_campaign_daily }}
    where campaign_id = @campaign_id
        -- Read 6 extra days so the moving average is complete on the first day shown.
        and date between date_sub(@start_date, interval 6 day) and @end_date
),

smoothed as (
    select
        *,
        round(avg(spend) over (order by date rows between 6 preceding and current row), 2)
            as spend_7d_avg
    from daily
)

select
    date,
    platform,
    campaign_id,
    campaign_name,
    campaign_type,
    impressions,
    clicks,
    round(spend, 2) as spend,
    spend_7d_avg,
    round(conversions, 2) as conversions,
    round(conversion_value, 2) as conversion_value,
    sessions,
    transactions,
    round(revenue, 2) as revenue,
    {{ ratio_columns }}
from smoothed
where date >= @start_date
order by date
