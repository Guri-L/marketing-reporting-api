"""Runtime settings, read from environment variables (and .env in development)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def fingerprint(api_key: str) -> str:
    """SHA-256 of a key. Keys are only ever compared and logged in this form."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Warehouse
    warehouse: Literal["duckdb", "bigquery"] = "duckdb"
    # Relative paths are resolved against the working directory.
    duckdb_path: Path = Path("data/warehouse.duckdb")
    seed_dir: Path = Path("data/seed")
    gcp_project_id: str = ""
    bq_dataset: str = "marketing_reporting"
    bq_location: str = "US"
    # Hard cap per query. BigQuery rejects a query that would scan more, before it runs.
    bq_max_bytes_billed: int = Field(default=1_000_000_000, gt=0)
    bq_query_timeout_seconds: float = Field(default=30.0, gt=0)

    # Access
    api_keys: SecretStr = SecretStr("")      # comma-separated
    auth_disabled: bool = False              # local development only
    rate_limit_per_minute: int = Field(default=120, ge=1)
    cors_origins: str = ""                   # comma-separated; empty disables CORS

    # Behaviour
    cache_ttl_seconds: float = Field(default=300.0, ge=0)
    cache_max_entries: int = Field(default=500, ge=1)
    default_range_days: int = Field(default=30, ge=1)
    max_range_days: int = Field(default=366, ge=1)
    pacing_over_threshold: float = Field(default=1.10, gt=1)
    pacing_under_threshold: float = Field(default=0.85, gt=0, lt=1)

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.warehouse == "bigquery" and not self.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required when WAREHOUSE=bigquery")
        if not self.auth_disabled and not self.key_fingerprints:
            # Fail closed: an API with no keys configured must not start open.
            raise ValueError("Set API_KEYS, or AUTH_DISABLED=true for local development")
        return self

    @property
    def key_fingerprints(self) -> frozenset[str]:
        keys = [k.strip() for k in self.api_keys.get_secret_value().split(",")]
        return frozenset(fingerprint(k) for k in keys if k)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
