-- Budget pacing for the month containing the report date.
-- projected spend = spend so far this month + recent daily run rate x days left.
with month_to_date as (
    select
        campaign_id,
        max_by(campaign_name, date) as campaign_name,
        max_by(platform, date) as platform,
        sum(spend) as spend_to_date
    from {{ rpt_paid_media_campaign_daily }}
    where date between @month_start and @as_of
    group by campaign_id
),

run_rate as (
    -- Last 7 days up to the report date, which may reach into the previous month early
    -- in a month. Dividing by 7 (not by days with spend) makes a paused
    -- campaign's run rate fall towards zero.
    select
        campaign_id,
        sum(spend) / 7 as daily_run_rate
    from {{ rpt_paid_media_campaign_daily }}
    where date between date_sub(@as_of, interval 6 day) and @as_of
    group by campaign_id
),

projected as (
    select
        budgets.campaign_id,
        month_to_date.campaign_name,
        month_to_date.platform,
        budgets.budget,
        coalesce(month_to_date.spend_to_date, 0) as spend_to_date,
        coalesce(run_rate.daily_run_rate, 0) as daily_run_rate,
        coalesce(month_to_date.spend_to_date, 0)
            + coalesce(run_rate.daily_run_rate, 0) * @days_remaining as projected_spend
    from {{ campaign_budgets }} as budgets
    left join month_to_date
        on budgets.campaign_id = month_to_date.campaign_id
    left join run_rate
        on budgets.campaign_id = run_rate.campaign_id
    where budgets.month = @month_start
)

select
    campaign_id,
    campaign_name,
    platform,
    round(budget, 2) as budget,
    round(spend_to_date, 2) as spend_to_date,
    round(daily_run_rate, 2) as daily_run_rate,
    round(projected_spend, 2) as projected_spend,
    round(projected_spend - budget, 2) as projected_variance,
    round(safe_divide(projected_spend, budget), 4) as pace,
    case
        when safe_divide(projected_spend, budget) > @over_threshold then 'over'
        when safe_divide(projected_spend, budget) < @under_threshold then 'under'
        else 'on_track'
    end as status
from projected
order by abs(safe_divide(projected_spend, budget) - 1) desc, campaign_id
