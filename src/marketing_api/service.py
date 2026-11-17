"""Reporting logic: turns validated request values into queries and results.

This is the only module that chooses SQL fragments. Everything a client sends
reaches SQL either as a bound parameter or as an enum looked up in a fixed
table below.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .cache import TTLCache
from .schemas import CampaignSort, Granularity, SortOrder
from .settings import Settings
from .warehouse import Params, Row, Warehouse

RATIO_COLUMNS = """\
round(safe_divide(clicks, impressions), 4) as ctr,
    round(safe_divide(spend, clicks), 2) as cpc,
    round(safe_divide(spend, impressions) * 1000, 2) as cpm,
    round(safe_divide(spend, conversions), 2) as cpa,
    round(safe_divide(conversion_value, spend), 2) as platform_roas,
    round(safe_divide(revenue, spend), 2) as ga4_roas"""

PERIOD_EXPRESSIONS = {
    Granularity.day: "date",
    Granularity.week: "date_trunc(date, week(monday))",
    Granularity.month: "date_trunc(date, month)",
}

CHANGE_METRICS = (
    "impressions", "clicks", "spend", "conversions", "conversion_value", "sessions",
    "transactions", "revenue", "ctr", "cpc", "cpm", "cpa", "platform_roas", "ga4_roas",
)


class NotFound(Exception):
    """The requested resource has no data."""


def order_by_clause(sort: CampaignSort, order: SortOrder) -> str:
    """ORDER BY text built only from enum members, with a stable tie-break."""
    # NULL ratios (e.g. CPA with no conversions) always sort last.
    return f"{sort.value} is null, {sort.value} {order.value}, platform, campaign_id"


def relative_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous, 4)


class ReportingService:
    def __init__(self, warehouse: Warehouse, cache: TTLCache, settings: Settings) -> None:
        self.warehouse = warehouse
        self.cache = cache
        self.settings = settings

    def _run(self, name: str, params: Params | None = None,
             fragments: dict[str, str] | None = None) -> tuple[list[Row], bool]:
        frozen = tuple(sorted((fragments or {}).items()))
        key = (name, frozen, tuple(sorted((params or {}).items(), key=lambda kv: kv[0])))
        return self.cache.get_or_compute(
            key, lambda: self.warehouse.query(name, params, frozen))

    # -- metadata -----------------------------------------------------------

    def data_range(self) -> tuple[Row, bool]:
        rows, cached = self._run("data_range")
        row = rows[0]
        if row["first_date"] is None:
            raise NotFound("The reporting table is empty")
        row["platforms"] = [p for p in (row["platforms"] or "").split(",") if p]
        return row, cached

    def default_period(self) -> tuple[date, date]:
        """The most recent ``default_range_days`` that actually have data."""
        last = self.data_range()[0]["last_date"]
        return last - timedelta(days=self.settings.default_range_days - 1), last

    # -- KPIs ---------------------------------------------------------------

    def kpi_summary(self, start: date, end: date, platform: str | None) -> tuple[Row, bool]:
        length = (end - start).days + 1
        previous_start, previous_end = start - timedelta(days=length), start - timedelta(days=1)
        rows, cached = self._run(
            "kpi_summary",
            {"start_date": start, "end_date": end, "previous_start": previous_start,
             "platform": platform},
            {"ratio_columns": RATIO_COLUMNS},
        )
        by_period = {row.pop("period"): row for row in rows}
        current = by_period.get("current")
        if current is None:
            raise NotFound("No data in the requested period")
        previous = by_period.get("previous")
        change = {
            metric: relative_change(current[metric], previous[metric] if previous else None)
            for metric in CHANGE_METRICS
        }
        return {
            "period": {"start_date": start, "end_date": end},
            "current": current,
            "previous_period": {"start_date": previous_start, "end_date": previous_end},
            "previous": previous,
            "change": change,
        }, cached

    def kpi_timeseries(self, start: date, end: date, platform: str | None,
                       granularity: Granularity) -> tuple[list[Row], bool]:
        return self._run(
            "kpi_timeseries",
            {"start_date": start, "end_date": end, "platform": platform},
            {"ratio_columns": RATIO_COLUMNS,
             "period_expression": PERIOD_EXPRESSIONS[granularity]},
        )

    # -- campaigns ----------------------------------------------------------

    def campaigns(self, start: date, end: date, platform: str | None, search: str | None,
                  sort: CampaignSort, order: SortOrder, limit: int,
                  offset: int) -> tuple[list[Row], int, bool]:
        rows, cached = self._run(
            "campaigns",
            {"start_date": start, "end_date": end, "platform": platform,
             "search": search or None, "limit": limit, "offset": offset},
            {"ratio_columns": RATIO_COLUMNS, "order_by": order_by_clause(sort, order)},
        )
        if rows:
            total = rows[0]["total_rows"]
        elif offset == 0:
            total = 0
        else:
            # Past the last page: the window count is gone with the rows, so ask again.
            first_page, _ = self._run(
                "campaigns",
                {"start_date": start, "end_date": end, "platform": platform,
                 "search": search or None, "limit": 1, "offset": 0},
                {"ratio_columns": RATIO_COLUMNS, "order_by": order_by_clause(sort, order)},
            )
            total = first_page[0]["total_rows"] if first_page else 0
        return rows, total, cached

    def campaign_detail(self, campaign_id: str, start: date, end: date) -> tuple[Row, bool]:
        daily, cached = self._run(
            "campaign_daily",
            {"campaign_id": campaign_id, "start_date": start, "end_date": end},
            {"ratio_columns": RATIO_COLUMNS},
        )
        if not daily:
            raise NotFound(f"No data for campaign {campaign_id!r} in the requested period")
        latest = daily[-1]
        return {
            "platform": latest["platform"],
            "campaign_id": campaign_id,
            "campaign_name": latest["campaign_name"],
            "campaign_type": latest["campaign_type"],
            "period": {"start_date": start, "end_date": end},
            "totals": summarise(daily),
            "daily": daily,
        }, cached

    # -- channels, platforms, pacing ---------------------------------------

    def channels(self, start: date, end: date) -> tuple[list[Row], bool]:
        return self._run("channels", {"start_date": start, "end_date": end})

    def platforms(self, start: date, end: date) -> tuple[list[Row], bool]:
        return self._run("platforms", {"start_date": start, "end_date": end},
                         {"ratio_columns": RATIO_COLUMNS})

    def pacing(self, as_of: date) -> tuple[Row, bool]:
        month_start = as_of.replace(day=1)
        days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
        days_remaining = days_in_month - as_of.day
        rows, cached = self._run("pacing", {
            "as_of": as_of,
            "month_start": month_start,
            "days_remaining": days_remaining,
            "over_threshold": self.settings.pacing_over_threshold,
            "under_threshold": self.settings.pacing_under_threshold,
        })
        return {
            "as_of": as_of,
            "month": f"{as_of:%Y-%m}",
            "days_remaining": days_remaining,
            "campaigns": rows,
        }, cached


def _ratio(numerator: float, denominator: float, digits: int, scale: float = 1.0) -> float | None:
    return round(numerator / denominator * scale, digits) if denominator else None


def summarise(rows: list[Row]) -> Row:
    """Totals and ratios over daily rows, using the same definitions as RATIO_COLUMNS."""
    total = {
        key: sum(row[key] or 0 for row in rows)
        for key in ("impressions", "clicks", "spend", "conversions", "conversion_value",
                    "sessions", "transactions", "revenue")
    }
    for key in ("spend", "conversions", "conversion_value", "revenue"):
        total[key] = round(total[key], 2)
    total.update(
        ctr=_ratio(total["clicks"], total["impressions"], 4),
        cpc=_ratio(total["spend"], total["clicks"], 2),
        cpm=_ratio(total["spend"], total["impressions"], 2, 1000),
        cpa=_ratio(total["spend"], total["conversions"], 2),
        platform_roas=_ratio(total["conversion_value"], total["spend"], 2),
        ga4_roas=_ratio(total["revenue"], total["spend"], 2),
    )
    return total
