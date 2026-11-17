"""Local DuckDB backend: the API runs with no cloud account, seeded from CSV files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb

from .base import TABLES, Params, Row, Warehouse, WarehouseError

log = logging.getLogger(__name__)
PARAM = re.compile(r"\$([a-z_]+)")

SEED_SCHEMAS = {
    "rpt_paid_media_campaign_daily": """
        date DATE, platform VARCHAR, account_id VARCHAR, campaign_id VARCHAR,
        campaign_name VARCHAR, campaign_type VARCHAR, impressions BIGINT, clicks BIGINT,
        spend DOUBLE, platform_conversions DOUBLE, platform_conversion_value DOUBLE,
        ga4_sessions BIGINT, ga4_engaged_sessions BIGINT, ga4_transactions BIGINT,
        ga4_revenue DOUBLE""",
    "rpt_channel_performance_daily": """
        date DATE, channel_group VARCHAR, sessions BIGINT, engaged_sessions BIGINT,
        transactions BIGINT, revenue DOUBLE, spend DOUBLE""",
    "campaign_budgets": "campaign_id VARCHAR, month DATE, budget DOUBLE",
}


def seed_database(path: Path, seed_dir: Path) -> dict[str, int]:
    """(Re)build the local database from the CSV files in ``seed_dir``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    con = duckdb.connect(str(path))
    try:
        for table in TABLES:
            source = seed_dir / f"{table}.csv"
            if not source.is_file():
                raise WarehouseError(f"Seed file missing: {source}")
            con.execute(f"CREATE OR REPLACE TABLE {table} ({SEED_SCHEMAS[table]})")
            # COPY into a table with declared types: ids stay text, nothing is guessed.
            quoted = str(source).replace("'", "''")
            con.execute(f"COPY {table} FROM '{quoted}' (HEADER true)")
            counts[table] = len(con.execute(f"SELECT 1 FROM {table}").fetchall())  # noqa: S608
    finally:
        con.close()
    return counts


class DuckDBWarehouse(Warehouse):
    dialect = "duckdb"

    def __init__(self, path: Path, seed_dir: Path | None = None) -> None:
        if not path.exists():
            if seed_dir is None:
                raise WarehouseError(f"Database not found: {path}")
            log.info("Seeded local database %s: %s", path, seed_database(path, seed_dir))
        # Read-only: the API never writes, and a query cannot change the data.
        self._con = duckdb.connect(str(path), read_only=True)

    def table_ref(self, table: str) -> str:
        return table

    def _execute(self, sql: str, params: Params) -> list[Row]:
        used = {name: params[name] for name in set(PARAM.findall(sql))}
        try:
            cursor = self._con.cursor()  # one cursor per call keeps this thread-safe
            cursor.execute(sql, used)
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except (duckdb.Error, KeyError) as exc:
            log.error("Local query failed: %s", exc)
            raise WarehouseError("Query failed") from exc

    def close(self) -> None:
        self._con.close()
