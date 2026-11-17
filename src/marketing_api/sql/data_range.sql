-- What the reporting table currently holds. Used for default date ranges,
-- the /v1/meta endpoint and the readiness check.
select
    min(date) as first_date,
    max(date) as last_date,
    count(*) as row_count,
    count(distinct campaign_id) as campaign_count,
    string_agg(distinct platform, ',' order by platform) as platforms
from {{ rpt_paid_media_campaign_daily }}
