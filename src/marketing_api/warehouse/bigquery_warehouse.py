"""BigQuery backend.

Auth is Application Default Credentials only. The identity needs
roles/bigquery.dataViewer on the reporting dataset and roles/bigquery.jobUser
on the project. Nothing more: the API only reads.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..settings import Settings
from .base import Params, ParamValue, Row, Warehouse, WarehouseError, WarehouseTimeout

log = logging.getLogger(__name__)


def bigquery_type(value: ParamValue) -> str:
    """BigQuery type for a bound parameter. ``None`` is always a nullable STRING filter."""
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    if isinstance(value, date):
        return "DATE"
    return "STRING"


class BigQueryWarehouse(Warehouse):
    dialect = "bigquery"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self._settings = settings
        self._client = client  # injectable for tests

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import bigquery
            except ImportError as exc:
                raise WarehouseError("Install the 'bigquery' extra") from exc
            self._client = bigquery.Client(project=self._settings.gcp_project_id,
                                           location=self._settings.bq_location)
        return self._client

    def table_ref(self, table: str) -> str:
        return f"`{self._settings.gcp_project_id}.{self._settings.bq_dataset}.{table}`"

    def _execute(self, sql: str, params: Params) -> list[Row]:
        from google.api_core import exceptions as gexc
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, bigquery_type(value), value)
                for name, value in params.items()
            ],
            # Cost guard: BigQuery refuses the query up front if it would scan more.
            maximum_bytes_billed=self._settings.bq_max_bytes_billed,
            use_query_cache=True,
            labels={"service": "marketing-api"},
        )
        timeout = self._settings.bq_query_timeout_seconds
        try:
            job = self.client.query(sql, job_config=job_config, timeout=timeout)
            return [dict(row.items()) for row in job.result(timeout=timeout)]
        except (TimeoutError, gexc.DeadlineExceeded, gexc.RetryError) as exc:
            log.error("BigQuery query timed out after %ss", timeout)
            raise WarehouseTimeout("Query timed out") from exc
        except gexc.GoogleAPICallError as exc:
            # Full detail goes to the log, never to the client.
            log.error("BigQuery query failed: %s", exc)
            raise WarehouseError("Query failed") from exc
