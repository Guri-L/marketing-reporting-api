"""SQL rendering for both backends and the BigQuery client wiring (with a fake client)."""

from datetime import date
from pathlib import Path

import pytest
import sqlglot

from marketing_api.schemas import CampaignSort, Granularity, SortOrder
from marketing_api.service import PERIOD_EXPRESSIONS, RATIO_COLUMNS, order_by_clause
from marketing_api.settings import Settings
from marketing_api.warehouse.base import SQL_DIR, WarehouseError
from marketing_api.warehouse.duckdb_warehouse import DuckDBWarehouse, seed_database

bigquery = pytest.importorskip("google.cloud.bigquery")
from marketing_api.warehouse.bigquery_warehouse import (  # noqa: E402
    BigQueryWarehouse,
    bigquery_type,
)

FRAGMENTS = {
    "kpi_summary": {"ratio_columns": RATIO_COLUMNS},
    "kpi_timeseries": {"ratio_columns": RATIO_COLUMNS,
                       "period_expression": PERIOD_EXPRESSIONS[Granularity.week]},
    "campaigns": {"ratio_columns": RATIO_COLUMNS,
                  "order_by": order_by_clause(CampaignSort.ga4_roas, SortOrder.desc)},
    "campaign_daily": {"ratio_columns": RATIO_COLUMNS},
    "platforms": {"ratio_columns": RATIO_COLUMNS},
}
QUERIES = sorted(path.stem for path in SQL_DIR.glob("*.sql"))


class FakeJob:
    def __init__(self, rows):
        self.rows = rows

    def result(self, timeout=None):
        return self.rows


class FakeClient:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = list(rows), error, []

    def query(self, sql, job_config=None, timeout=None):
        self.calls.append((sql, job_config, timeout))
        if self.error:
            raise self.error
        return FakeJob(self.rows)


@pytest.fixture
def bq_settings():
    return Settings(_env_file=None, warehouse="bigquery", gcp_project_id="demo-project",
                    bq_dataset="marketing_reporting", auth_disabled=True,
                    bq_max_bytes_billed=123_456, bq_query_timeout_seconds=7)


def test_every_query_file_is_covered():
    assert QUERIES == ["campaign_daily", "campaigns", "channels", "data_range", "kpi_summary",
                       "kpi_timeseries", "pacing", "platforms"]


@pytest.mark.parametrize("name", QUERIES)
def test_queries_render_to_parseable_bigquery_sql(bq_settings, name):
    warehouse = BigQueryWarehouse(bq_settings, client=FakeClient())
    sql = warehouse.render(name, tuple(FRAGMENTS.get(name, {}).items()))
    assert "{{" not in sql
    assert "`demo-project.marketing_reporting." in sql
    sqlglot.parse_one(sql, read="bigquery")


@pytest.mark.parametrize("name", QUERIES)
def test_queries_transpile_for_the_local_backend(database, name):
    warehouse = DuckDBWarehouse(database)
    sql = warehouse.render(name, tuple(FRAGMENTS.get(name, {}).items()))
    assert "@" not in sql and "`" not in sql
    warehouse.close()


def test_unknown_placeholder_or_query_is_an_error(database):
    warehouse = DuckDBWarehouse(database)
    with pytest.raises(KeyError, match="unresolved placeholder"):
        warehouse.render("campaigns")          # fragments missing
    with pytest.raises(KeyError, match="Unknown query"):
        warehouse.render("not_a_query")
    warehouse.close()


def test_order_by_is_built_only_from_enum_members():
    clauses = {order_by_clause(sort, order) for sort in CampaignSort for order in SortOrder}
    assert len(clauses) == len(CampaignSort) * 2
    for clause in clauses:
        assert all(ch.isalnum() or ch in "_ ," for ch in clause), clause


def test_bigquery_parameters_are_bound_and_typed(bq_settings):
    client = FakeClient(rows=[{"platform": "google_ads"}])
    warehouse = BigQueryWarehouse(bq_settings, client=client)
    rows = warehouse.query("platforms", {"start_date": date(2026, 10, 1),
                                         "end_date": date(2026, 10, 31)},
                           tuple(FRAGMENTS["platforms"].items()))
    assert rows == [{"platform": "google_ads"}]
    sql, config, timeout = client.calls[0]
    assert "@start_date" in sql and "2026-10-01" not in sql      # value never in the SQL text
    assert {(p.name, p.type_, p.value) for p in config.query_parameters} == {
        ("start_date", "DATE", date(2026, 10, 1)), ("end_date", "DATE", date(2026, 10, 31))}
    assert config.maximum_bytes_billed == 123_456                # cost guard applied
    assert config.labels == {"service": "marketing-api"}
    assert timeout == 7


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "STRING"), ("meta_ads", "STRING"), (5, "INT64"), (1.1, "FLOAT64"),
     (True, "BOOL"), (date(2026, 1, 1), "DATE")],
)
def test_bigquery_type_mapping(value, expected):
    assert bigquery_type(value) == expected


def test_bigquery_errors_are_translated_without_leaking_detail(bq_settings):
    from google.api_core import exceptions as gexc

    from marketing_api.warehouse import WarehouseTimeout

    denied = BigQueryWarehouse(bq_settings, client=FakeClient(
        error=gexc.Forbidden("Access Denied: demo-project:marketing_reporting.secret")))
    with pytest.raises(WarehouseError) as failure:
        denied.query("data_range")
    assert "secret" not in str(failure.value)

    slow = BigQueryWarehouse(bq_settings, client=FakeClient(error=TimeoutError()))
    with pytest.raises(WarehouseTimeout):
        slow.query("data_range")


def test_local_database_is_read_only_and_seeds_itself(tmp_path, database):
    warehouse = DuckDBWarehouse(database)
    with pytest.raises(WarehouseError):
        warehouse._execute("DELETE FROM campaign_budgets", {})
    warehouse.close()

    fresh = tmp_path / "new.duckdb"
    seeded = DuckDBWarehouse(fresh, Path(__file__).resolve().parents[1] / "data" / "seed")
    assert seeded.query("data_range")[0]["row_count"] == 909
    seeded.close()
    with pytest.raises(WarehouseError, match="not found"):
        DuckDBWarehouse(tmp_path / "missing.duckdb")
    with pytest.raises(WarehouseError, match="Seed file missing"):
        seed_database(tmp_path / "x.duckdb", tmp_path)


def test_budget_without_spend_is_reported_as_under(tmp_path, database):
    import shutil

    import duckdb

    copy = tmp_path / "copy.duckdb"
    shutil.copy(database, copy)
    con = duckdb.connect(str(copy))
    con.execute("INSERT INTO campaign_budgets VALUES ('not-started', DATE '2026-10-01', 500)")
    con.close()

    warehouse = DuckDBWarehouse(copy)
    rows = warehouse.query("pacing", {
        "as_of": date(2026, 10, 20), "month_start": date(2026, 10, 1), "days_remaining": 11,
        "over_threshold": 1.10, "under_threshold": 0.85})
    warehouse.close()
    row = next(r for r in rows if r["campaign_id"] == "not-started")
    assert (row["campaign_name"], row["spend_to_date"], row["projected_spend"]) == (None, 0, 0)
    assert (row["pace"], row["status"]) == (0, "under")
    assert len(rows) == 11
