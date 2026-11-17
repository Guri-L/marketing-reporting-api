"""Versioned reporting endpoints. All require an API key."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from ..dependencies import Client, PlatformFilter, Range, Service, mark_cache
from ..schemas import (
    Campaign,
    CampaignDetail,
    CampaignSort,
    Channel,
    DataRange,
    Envelope,
    Granularity,
    KpiSummary,
    PacingReport,
    Page,
    Pagination,
    PlatformComparison,
    Problem,
    SortOrder,
    TimeseriesPoint,
)

ERRORS: dict[int | str, dict] = {
    401: {"model": Problem, "description": "Missing or invalid API key"},
    404: {"model": Problem, "description": "No data for the request"},
    422: {"model": Problem, "description": "Invalid parameters"},
    429: {"model": Problem, "description": "Rate limit exceeded"},
    503: {"model": Problem, "description": "Warehouse unavailable"},
}
router = APIRouter(prefix="/v1", responses=ERRORS)


@router.get("/meta", response_model=Envelope[DataRange], tags=["Metadata"],
            summary="Date range and size of the available data")
def meta(client: Client, service: Service, response: Response) -> dict:
    data, cached = service.data_range()
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/kpis/summary", response_model=Envelope[KpiSummary], tags=["KPIs"],
            summary="Totals for a period, compared with the period before")
def kpi_summary(client: Client, service: Service, period: Range, platform: PlatformFilter,
                response: Response) -> dict:
    data, cached = service.kpi_summary(period.start, period.end, platform)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/kpis/timeseries", response_model=Envelope[list[TimeseriesPoint]], tags=["KPIs"],
            summary="KPIs per day, week or month")
def kpi_timeseries(
    client: Client, service: Service, period: Range, platform: PlatformFilter,
    response: Response,
    granularity: Annotated[Granularity, Query(description="Weeks start on Monday")]
        = Granularity.day,
) -> dict:
    data, cached = service.kpi_timeseries(period.start, period.end, platform, granularity)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/campaigns", response_model=Page[Campaign], tags=["Campaigns"],
            summary="Campaign leaderboard with sorting, search and pagination")
def campaigns(
    client: Client, service: Service, period: Range, platform: PlatformFilter,
    response: Response,
    search: Annotated[str | None, Query(
        max_length=100, description="Case-insensitive match on campaign name")] = None,
    sort: Annotated[CampaignSort, Query()] = CampaignSort.spend,
    order: Annotated[SortOrder, Query()] = SortOrder.desc,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    rows, total, cached = service.campaigns(
        period.start, period.end, platform, search, sort, order, limit, offset)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    next_offset = offset + limit if offset + limit < total else None
    return {"data": rows,
            "pagination": Pagination(limit=limit, offset=offset, total=total,
                                     next_offset=next_offset)}


@router.get("/campaigns/{campaign_id}", response_model=Envelope[CampaignDetail],
            tags=["Campaigns"], summary="One campaign: totals and daily figures")
def campaign_detail(
    client: Client, service: Service, period: Range, response: Response,
    campaign_id: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")],
) -> dict:
    data, cached = service.campaign_detail(campaign_id, period.start, period.end)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/channels", response_model=Envelope[list[Channel]], tags=["Channels"],
            summary="Site performance by channel, paid and unpaid")
def channels(client: Client, service: Service, period: Range, response: Response) -> dict:
    data, cached = service.channels(period.start, period.end)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/platforms", response_model=Envelope[list[PlatformComparison]], tags=["Platforms"],
            summary="Ad platforms compared, including the attribution gap against GA4")
def platforms(client: Client, service: Service, period: Range, response: Response) -> dict:
    data, cached = service.platforms(period.start, period.end)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}


@router.get("/pacing", response_model=Envelope[PacingReport], tags=["Budgets"],
            summary="Projected month-end spend against each campaign's budget")
def pacing(
    client: Client, service: Service, response: Response,
    as_of: Annotated[date | None, Query(
        description="Report date, YYYY-MM-DD. Defaults to the latest day with data.")] = None,
) -> dict:
    report_date = as_of or service.data_range()[0]["last_date"]
    data, cached = service.pacing(report_date)
    mark_cache(response, cached, service.settings.cache_ttl_seconds)
    return {"data": data}
