from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from marketing_api.main import create_app
from marketing_api.settings import Settings
from marketing_api.warehouse.duckdb_warehouse import seed_database

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "seed"
API_KEY = "test-key-alpha"
OTHER_KEY = "test-key-beta"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture(scope="session")
def database(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "warehouse.duckdb"
    seed_database(path, SEED_DIR)
    return path


@pytest.fixture
def make_settings(database):
    def build(**overrides) -> Settings:
        values = {
            "warehouse": "duckdb", "duckdb_path": database, "seed_dir": SEED_DIR,
            "api_keys": SecretStr(f"{API_KEY},{OTHER_KEY}"), "log_level": "WARNING",
            "log_format": "text", "rate_limit_per_minute": 10_000,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)
    return build


@pytest.fixture
def make_client(make_settings):
    clients = []

    def build(warehouse=None, **overrides) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides), warehouse))
        client.__enter__()  # runs startup, which builds the service
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def _read(name: str) -> list[dict]:
    with (SEED_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="session")
def paid_rows() -> list[dict]:
    """The seed data, typed, for recomputing expected results independently of SQL."""
    rows = _read("rpt_paid_media_campaign_daily.csv")
    for row in rows:
        row["date"] = date.fromisoformat(row["date"])
        for key in ("impressions", "clicks", "ga4_sessions", "ga4_transactions"):
            row[key] = int(row[key])
        for key in ("spend", "platform_conversions", "platform_conversion_value", "ga4_revenue"):
            row[key] = float(row[key])
    return rows


@pytest.fixture(scope="session")
def budget_rows() -> list[dict]:
    return _read("campaign_budgets.csv")


def between(rows: list[dict], start: str, end: str, platform: str | None = None) -> list[dict]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [r for r in rows if first <= r["date"] <= last
            and (platform is None or r["platform"] == platform)]
