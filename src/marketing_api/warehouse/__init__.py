"""Warehouse backends."""

from __future__ import annotations

from ..settings import Settings
from .base import Params, Row, Warehouse, WarehouseError, WarehouseTimeout

__all__ = ["Params", "Row", "Warehouse", "WarehouseError", "WarehouseTimeout", "build_warehouse"]


def build_warehouse(settings: Settings) -> Warehouse:
    if settings.warehouse == "bigquery":
        from .bigquery_warehouse import BigQueryWarehouse

        return BigQueryWarehouse(settings)
    from .duckdb_warehouse import DuckDBWarehouse

    return DuckDBWarehouse(settings.duckdb_path, settings.seed_dir)
