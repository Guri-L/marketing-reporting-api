-- Whole-site performance by channel (paid and unpaid) for a period.
with totals as (
    select
        channel_group,
        sum(sessions) as sessions,
        sum(engaged_sessions) as engaged_sessions,
        sum(transactions) as transactions,
        sum(revenue) as revenue,
        sum(spend) as spend
    from {{ rpt_channel_performance_daily }}
    where date between @start_date and @end_date
    group by channel_group
)

select
    channel_group,
    sessions,
    engaged_sessions,
    transactions,
    round(revenue, 2) as revenue,
    round(spend, 2) as spend,
    round(safe_divide(sessions, sum(sessions) over ()), 4) as session_share,
    round(safe_divide(revenue, sum(revenue) over ()), 4) as revenue_share,
    round(safe_divide(engaged_sessions, sessions), 4) as engagement_rate,
    round(safe_divide(transactions, sessions), 4) as conversion_rate,
    round(safe_divide(revenue, transactions), 2) as average_order_value,
    -- NULL for channels with no ad spend
    round(safe_divide(revenue, nullif(spend, 0)), 2) as roas
from totals
order by revenue desc, channel_group
