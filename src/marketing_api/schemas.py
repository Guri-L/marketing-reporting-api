"""Request enums and response models. These define the public contract and the OpenAPI docs."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Platform(str, Enum):
    google_ads = "google_ads"
    meta_ads = "meta_ads"


class Granularity(str, Enum):
    day = "day"
    week = "week"
    month = "month"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class CampaignSort(str, Enum):
    spend = "spend"
    impressions = "impressions"
    clicks = "clicks"
    conversions = "conversions"
    conversion_value = "conversion_value"
    revenue = "revenue"
    ctr = "ctr"
    cpc = "cpc"
    cpa = "cpa"
    platform_roas = "platform_roas"
    ga4_roas = "ga4_roas"
    campaign_name = "campaign_name"


class PacingStatus(str, Enum):
    over = "over"
    under = "under"
    on_track = "on_track"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Metrics(ApiModel):
    impressions: int
    clicks: int
    spend: float = Field(description="Ad spend in the account currency")
    conversions: float = Field(description="Conversions as reported by the ad platform")
    conversion_value: float = Field(description="Conversion value as reported by the ad platform")
    sessions: int = Field(description="GA4 sessions attributed to the campaigns")
    transactions: int = Field(description="GA4 purchases attributed to the campaigns")
    revenue: float = Field(description="GA4 purchase revenue attributed to the campaigns")
    ctr: float | None = Field(description="Clicks / impressions")
    cpc: float | None = Field(description="Spend / clicks")
    cpm: float | None = Field(description="Spend per 1,000 impressions")
    cpa: float | None = Field(description="Spend / platform conversions")
    platform_roas: float | None = Field(description="Platform conversion value / spend")
    ga4_roas: float | None = Field(description="GA4 revenue / spend")


class PeriodMetrics(Metrics):
    active_campaigns: int


class Period(ApiModel):
    start_date: date
    end_date: date


class KpiSummary(ApiModel):
    period: Period
    current: PeriodMetrics
    previous_period: Period
    previous: PeriodMetrics | None = Field(
        description="Same-length period immediately before; null if it has no data")
    change: dict[str, float | None] = Field(
        description="Relative change of each metric against the previous period "
                    "(0.25 = +25%); null where the previous value is missing or zero")


class TimeseriesPoint(Metrics):
    period_start: date = Field(description="First day of the day, week (Monday) or month")


class Campaign(Metrics):
    platform: Platform
    campaign_id: str
    campaign_name: str
    campaign_type: str | None
    first_date: date
    last_date: date
    active_days: int
    spend_share: float | None = Field(description="Share of all spend in the period")
    spend_rank: int = Field(description="Rank by spend among all campaigns in the period")


class CampaignDay(Metrics):
    date: date
    spend_7d_avg: float | None = Field(description="Average spend over this and the 6 days before")


class CampaignDetail(ApiModel):
    platform: Platform
    campaign_id: str
    campaign_name: str
    campaign_type: str | None
    period: Period
    totals: Metrics
    daily: list[CampaignDay]


class Channel(ApiModel):
    channel_group: str
    sessions: int
    engaged_sessions: int
    transactions: int
    revenue: float
    spend: float
    session_share: float | None
    revenue_share: float | None
    engagement_rate: float | None
    conversion_rate: float | None
    average_order_value: float | None
    roas: float | None = Field(description="Revenue / spend; null for channels without ad spend")


class PlatformComparison(Metrics):
    platform: Platform
    active_campaigns: int
    spend_share: float | None
    attribution_gap: float | None = Field(
        description="Share of platform-claimed revenue that GA4 does not attribute to it")


class PacingRow(ApiModel):
    campaign_id: str
    campaign_name: str | None = Field(description="Null if the campaign has not spent this month")
    platform: Platform | None
    status: PacingStatus
    budget: float
    spend_to_date: float
    daily_run_rate: float = Field(description="Average daily spend over the last 7 days")
    projected_spend: float = Field(description="Spend to date + run rate x days remaining")
    projected_variance: float = Field(description="Projected spend minus budget")
    pace: float | None = Field(description="Projected spend / budget (1.0 = exactly on budget)")


class PacingReport(ApiModel):
    as_of: date
    month: str = Field(examples=["2026-10"])
    days_remaining: int
    campaigns: list[PacingRow]


class DataRange(ApiModel):
    first_date: date
    last_date: date
    row_count: int
    campaign_count: int
    platforms: list[str]


class Pagination(ApiModel):
    limit: int
    offset: int
    total: int
    next_offset: int | None = Field(description="Offset of the next page; null on the last page")


class Envelope(ApiModel, Generic[T]):
    data: T


class Page(ApiModel, Generic[T]):
    data: list[T]
    pagination: Pagination


class Problem(ApiModel):
    """Error body, following RFC 9457 (problem details)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = Field(default=None, description="Request id, for support")
    errors: list[dict] | None = None


class Health(ApiModel):
    status: str
    version: str
