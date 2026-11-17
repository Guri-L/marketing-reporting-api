"""Build the local DuckDB database from the CSV files in data/seed.

Usage: python -m marketing_api.seed
"""

from __future__ import annotations

import os

from .settings import Settings
from .warehouse.duckdb_warehouse import seed_database


def main() -> int:
    # Seeding needs paths only, so it must not require API keys to be configured.
    os.environ.setdefault("AUTH_DISABLED", "true")
    settings = Settings()
    counts = seed_database(settings.duckdb_path, settings.seed_dir)
    for table, rows in counts.items():
        print(f"{table}: {rows} rows")
    print(f"Database written to {settings.duckdb_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
