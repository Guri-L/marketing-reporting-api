"""Endpoint results, checked against figures recomputed in Python from the seed files."""

from datetime import date, timedelta

import pytest
from conftest import AUTH, between

OCTOBER = {"start_date": "2026-10-01", "end_date": "2026-10-31"}


def get(client, path, **params):
    response = client.get(path, params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def test_meta_describes_available_data(client, paid_rows):
    data = get(client, "/v1/meta")["data"]
    assert data == {
        "first_date": "2026-08-01", "last_date": "2026-10-31", "row_count": len(paid_rows),
        "campaign_count": len({r["campaign_id"] for r in paid_rows}),
        "platforms": ["google_ads", "meta_ads"],
    }


def test_summary_totals_match_the_source_data(client, paid_rows):
    current = get(client, "/v1/kpis/summary", **OCTOBER)["data"]["current"]
    rows = between(paid_rows, "2026-10-01", "2026-10-31")
    spend = sum(r["spend"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    impressions = sum(r["impressions"] for r in rows)
    revenue = sum(r["ga4_revenue"] for r in rows)
    value = sum(r["platform_conversion_value"] for r in rows)
    conversions = sum(r["platform_conversions"] for r in rows)

    assert current["spend"] == pytest.approx(spend, abs=0.01)
    assert current["clicks"] == clicks
    assert current["impressions"] == impressions
    assert current["revenue"] == pytest.approx(revenue, abs=0.01)
    assert current["active_campaigns"] == len({r["campaign_id"] for r in rows})
    assert current["ctr"] == pytest.approx(clicks / impressions, abs=5e-5)
    assert current["cpc"] == pytest.approx(spend / clicks, abs=0.005)
    assert current["cpm"] == pytest.approx(spend / impressions * 1000, abs=0.005)
    assert current["cpa"] == pytest.approx(spend / conversions, abs=0.005)
    assert current["platform_roas"] == pytest.approx(value / spend, abs=0.005)
    assert current["ga4_roas"] == pytest.approx(revenue / spend, abs=0.005)


def test_summary_compares_with_the_equal_length_period_before(client, paid_rows):
    data = get(client, "/v1/kpis/summary", **OCTOBER)["data"]
    assert data["previous_period"] == {"start_date": "2026-08-31", "end_date": "2026-09-30"}
    previous_spend = sum(r["spend"] for r in between(paid_rows, "2026-08-31", "2026-09-30"))
    assert data["previous"]["spend"] == pytest.approx(previous_spend, abs=0.01)
    expected = (data["current"]["spend"] - data["previous"]["spend"]) / data["previous"]["spend"]
    assert data["change"]["spend"] == pytest.approx(expected, abs=1e-4)


def test_summary_without_earlier_data_has_no_comparison(client):
    data = get(client, "/v1/kpis/summary", start_date="2026-08-01", end_date="2026-08-31")["data"]
    assert data["previous"] is None
    assert set(data["change"].values()) == {None}


def test_summary_platform_filter(client, paid_rows):
    data = get(client, "/v1/kpis/summary", platform="meta_ads", **OCTOBER)["data"]
    expected = sum(r["spend"] for r in between(paid_rows, "2026-10-01", "2026-10-31", "meta_ads"))
    assert data["current"]["spend"] == pytest.approx(expected, abs=0.01)


def test_default_period_is_the_latest_30_days_of_data(client):
    data = get(client, "/v1/kpis/summary")["data"]
    assert data["period"] == {"start_date": "2026-10-02", "end_date": "2026-10-31"}


def test_end_date_alone_gets_a_30_day_period(client):
    data = get(client, "/v1/kpis/summary", end_date="2026-09-30")["data"]
    assert data["period"] == {"start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.mark.parametrize(("granularity", "points"), [("day", 31), ("week", 5), ("month", 1)])
def test_timeseries_grain_and_totals(client, granularity, points):
    series = get(client, "/v1/kpis/timeseries", granularity=granularity, **OCTOBER)["data"]
    summary = get(client, "/v1/kpis/summary", **OCTOBER)["data"]["current"]
    assert len(series) == points
    assert [p["period_start"] for p in series] == sorted(p["period_start"] for p in series)
    assert sum(p["spend"] for p in series) == pytest.approx(summary["spend"], abs=0.02 * points)
    assert sum(p["clicks"] for p in series) == summary["clicks"]


def test_weeks_start_on_monday(client):
    series = get(client, "/v1/kpis/timeseries", granularity="week", **OCTOBER)["data"]
    assert all(date.fromisoformat(p["period_start"]).weekday() == 0 for p in series)
    assert series[0]["period_start"] == "2026-09-28"  # the week containing 1 October


def test_month_timeseries_over_the_whole_dataset(client):
    series = get(client, "/v1/kpis/timeseries", granularity="month",
                 start_date="2026-08-01", end_date="2026-10-31")["data"]
    assert [p["period_start"] for p in series] == ["2026-08-01", "2026-09-01", "2026-10-01"]


def test_campaigns_default_sort_is_spend_descending(client, paid_rows):
    body = get(client, "/v1/campaigns", **OCTOBER)
    spends = [c["spend"] for c in body["data"]]
    assert spends == sorted(spends, reverse=True)
    assert [c["spend_rank"] for c in body["data"]] == list(range(1, len(spends) + 1))
    assert sum(c["spend_share"] for c in body["data"]) == pytest.approx(1.0, abs=0.001)

    by_campaign = {}
    for row in between(paid_rows, "2026-10-01", "2026-10-31"):
        by_campaign[row["campaign_id"]] = by_campaign.get(row["campaign_id"], 0) + row["spend"]
    top = body["data"][0]
    assert top["campaign_id"] == max(by_campaign, key=by_campaign.get)
    assert top["spend"] == pytest.approx(by_campaign[top["campaign_id"]], abs=0.01)


def test_campaign_ids_are_strings_and_keep_full_precision(client):
    ids = {c["campaign_id"] for c in get(client, "/v1/campaigns", **OCTOBER)["data"]}
    assert "120214400518830101" in ids


def test_campaigns_pagination_walks_every_row_once(client):
    seen, offset = [], 0
    while offset is not None:
        body = get(client, "/v1/campaigns", limit=4, offset=offset, **OCTOBER)
        seen += [c["campaign_id"] for c in body["data"]]
        assert body["pagination"]["total"] == 10
        offset = body["pagination"]["next_offset"]
    assert len(seen) == len(set(seen)) == 10


def test_page_past_the_end_is_empty_but_reports_the_total(client):
    body = get(client, "/v1/campaigns", limit=5, offset=50, **OCTOBER)
    assert body["data"] == []
    assert body["pagination"] == {"limit": 5, "offset": 50, "total": 10, "next_offset": None}


@pytest.mark.parametrize("sort", ["ga4_roas", "cpa", "ctr", "campaign_name", "conversions"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_campaigns_sorting(client, sort, order):
    values = [c[sort] for c in get(client, "/v1/campaigns", sort=sort, order=order,
                                   **OCTOBER)["data"]]
    assert values == sorted(values, reverse=(order == "desc"))


def test_campaign_search_is_case_insensitive_and_keeps_overall_rank(client):
    body = get(client, "/v1/campaigns", search="RETARGETING", **OCTOBER)
    names = [c["campaign_name"] for c in body["data"]]
    assert names and all("retargeting" in name for name in names)
    assert body["pagination"]["total"] == len(names) == 2
    assert all(c["spend_rank"] > 1 for c in body["data"])  # rank among all campaigns


def test_search_text_is_data_not_sql(client):
    for text in ("' OR 1=1 --", "%", "'; DROP TABLE campaign_budgets; --"):
        body = get(client, "/v1/campaigns", search=text, **OCTOBER)
        assert body["data"] == [] and body["pagination"]["total"] == 0
    # No wildcard meaning either: "_" matches only names that contain an underscore.
    assert get(client, "/v1/campaigns", search="_", **OCTOBER)["pagination"]["total"] == 10
    assert get(client, "/v1/campaigns", search="b_and", **OCTOBER)["pagination"]["total"] == 0
    assert get(client, "/v1/meta")["data"]["row_count"] > 0


def test_campaign_detail_totals_and_moving_average(client, paid_rows):
    data = get(client, "/v1/campaigns/20418830011",
               start_date="2026-10-25", end_date="2026-10-31")["data"]
    rows = [r for r in between(paid_rows, "2026-10-25", "2026-10-31")
            if r["campaign_id"] == "20418830011"]
    assert data["campaign_name"] == "brand_search_eu"
    assert len(data["daily"]) == 7
    assert data["totals"]["spend"] == pytest.approx(sum(r["spend"] for r in rows), abs=0.01)
    assert data["totals"]["clicks"] == sum(r["clicks"] for r in rows)

    # The first day's 7-day average must include the six days before the period.
    window = [r["spend"] for r in between(paid_rows, "2026-10-19", "2026-10-25")
              if r["campaign_id"] == "20418830011"]
    assert data["daily"][0]["spend_7d_avg"] == pytest.approx(sum(window) / 7, abs=0.01)


def test_unknown_campaign_is_404(client):
    response = client.get("/v1/campaigns/does-not-exist", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["title"] == "Not Found"


def test_channels_cover_paid_and_unpaid_traffic(client):
    data = get(client, "/v1/channels", **OCTOBER)["data"]
    names = {c["channel_group"] for c in data}
    assert {"Paid Search", "Paid Social", "Organic Search", "Direct", "Email"} <= names
    assert sum(c["session_share"] for c in data) == pytest.approx(1.0, abs=0.001)
    assert sum(c["revenue_share"] for c in data) == pytest.approx(1.0, abs=0.001)
    by_name = {c["channel_group"]: c for c in data}
    assert by_name["Direct"]["roas"] is None and by_name["Direct"]["spend"] == 0
    assert by_name["Paid Search"]["roas"] > 0


def test_platforms_reconcile_with_summary_and_show_attribution_gap(client):
    platforms = get(client, "/v1/platforms", **OCTOBER)["data"]
    summary = get(client, "/v1/kpis/summary", **OCTOBER)["data"]["current"]
    assert {p["platform"] for p in platforms} == {"google_ads", "meta_ads"}
    assert sum(p["spend"] for p in platforms) == pytest.approx(summary["spend"], abs=0.02)
    for platform in platforms:
        expected = 1 - platform["revenue"] / platform["conversion_value"]
        assert platform["attribution_gap"] == pytest.approx(expected, abs=1e-4)
        assert 0 < platform["attribution_gap"] < 1


def test_pacing_projection_matches_the_definition(client, paid_rows, budget_rows):
    as_of = date(2026, 10, 20)
    data = get(client, "/v1/pacing", as_of=as_of.isoformat())["data"]
    assert (data["month"], data["days_remaining"]) == ("2026-10", 11)
    budgets = {b["campaign_id"]: float(b["budget"]) for b in budget_rows
               if b["month"] == "2026-10-01"}
    assert {c["campaign_id"] for c in data["campaigns"]} == set(budgets)

    for campaign in data["campaigns"]:
        rows = [r for r in paid_rows if r["campaign_id"] == campaign["campaign_id"]]
        to_date = sum(r["spend"] for r in rows if date(2026, 10, 1) <= r["date"] <= as_of)
        last_week = sum(r["spend"] for r in rows
                        if as_of - timedelta(days=6) <= r["date"] <= as_of)
        projected = to_date + last_week / 7 * 11
        pace = projected / budgets[campaign["campaign_id"]]
        assert campaign["spend_to_date"] == pytest.approx(to_date, abs=0.01)
        assert campaign["projected_spend"] == pytest.approx(projected, abs=0.01)
        assert campaign["pace"] == pytest.approx(pace, abs=1e-4)
        assert campaign["status"] == ("over" if pace > 1.10 else "under" if pace < 0.85
                                      else "on_track")
    paces = [abs(c["pace"] - 1) for c in data["campaigns"]]
    assert paces == sorted(paces, reverse=True)  # furthest off budget first


def test_pacing_defaults_to_the_latest_day_and_respects_thresholds(client, make_client):
    data = get(client, "/v1/pacing")["data"]
    assert (data["as_of"], data["days_remaining"]) == ("2026-10-31", 0)
    strict = make_client(pacing_over_threshold=1.0001, pacing_under_threshold=0.9999)
    report = get(strict, "/v1/pacing", as_of="2026-10-20")["data"]
    statuses = {c["status"] for c in report["campaigns"]}
    assert statuses == {"over", "under"}


def test_pacing_for_a_month_without_budgets_is_empty(client):
    assert get(client, "/v1/pacing", as_of="2025-01-15")["data"]["campaigns"] == []


def test_period_without_data_is_404_for_summary_and_empty_for_lists(client):
    empty = {"start_date": "2025-01-01", "end_date": "2025-01-31"}
    assert client.get("/v1/kpis/summary", params=empty, headers=AUTH).status_code == 404
    assert get(client, "/v1/kpis/timeseries", **empty)["data"] == []
    assert get(client, "/v1/campaigns", **empty) == {
        "data": [], "pagination": {"limit": 25, "offset": 0, "total": 0, "next_offset": None}}
