"""Warehouse interface and SQL rendering shared by both backends.

Queries live in marketing_api/sql/*.sql, written in BigQuery Standard SQL. They use
three kinds of placeholder:

* ``@name`` - a bound query parameter. Every value that comes from a request
  goes through one of these, so request data is never spliced into SQL text.
* ``{{ table_name }}`` - a table reference, resolved per backend.
* ``{{ fragment_name }}`` - a SQL fragment chosen in code from a fixed
  whitelist (sort order, time grain). Never built from request text.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlglot

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"   # shipped inside the package
TABLES = ("rpt_paid_media_campaign_daily", "rpt_channel_performance_daily", "campaign_budgets")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_0-9]+)\s*\}\}")

ParamValue = str | int | float | bool | date | None
Params = dict[str, ParamValue]
Row = dict[str, Any]


class WarehouseError(Exception):
    """The warehouse could not answer. Carries no detail that is safe to show clients."""


class WarehouseTimeout(WarehouseError):
    """The query did not finish in time."""


@lru_cache
def load_sql(name: str) -> str:
    path = SQL_DIR / f"{name}.sql"
    if not path.is_file():
        raise KeyError(f"Unknown query: {name}")
    return path.read_text(encoding="utf-8").strip().rstrip(";")


class Warehouse(ABC):
    dialect: str

    @abstractmethod
    def table_ref(self, table: str) -> str:
        """Fully qualified, quoted name of a reporting table."""

    @abstractmethod
    def _execute(self, sql: str, params: Params) -> list[Row]:
        """Run rendered SQL with bound parameters."""

    def close(self) -> None:  # noqa: B027 - optional hook
        pass

    def render(self, name: str, fragments: tuple[tuple[str, str], ...] = ()) -> str:
        """SQL text for a named query in this backend's dialect (cached by caller)."""
        values = dict(fragments)

        def resolve(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in TABLES:
                return self.table_ref(key)
            if key in values:
                return values[key]
            raise KeyError(f"Query {name!r}: unresolved placeholder {key!r}")

        rendered = PLACEHOLDER.sub(resolve, load_sql(name))
        if self.dialect == "bigquery":
            return rendered
        statements = sqlglot.transpile(rendered, read="bigquery", write=self.dialect)
        if len(statements) != 1:
            raise ValueError(f"Query {name!r} must contain exactly one statement")
        return statements[0]

    def query(self, name: str, params: Params | None = None,
              fragments: tuple[tuple[str, str], ...] = ()) -> list[Row]:
        return self._execute(self._rendered(name, fragments), params or {})

    @lru_cache(maxsize=256)  # noqa: B019 - one long-lived warehouse per process
    def _rendered(self, name: str, fragments: tuple[tuple[str, str], ...]) -> str:
        return self.render(name, fragments)
