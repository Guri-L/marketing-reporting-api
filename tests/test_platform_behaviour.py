"""Cross-cutting behaviour: auth, rate limits, validation, caching, errors, operations."""

import logging

import pytest
from conftest import API_KEY, AUTH, OTHER_KEY

from marketing_api.cache import TTLCache
from marketing_api.security import AuthError, RateLimited, RateLimiter, authenticate
from marketing_api.settings import Settings, fingerprint
from marketing_api.warehouse import WarehouseError, WarehouseTimeout
from marketing_api.warehouse.base import Warehouse


class BrokenWarehouse(Warehouse):
    dialect = "duckdb"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def table_ref(self, table: str) -> str:
        return table

    def _execute(self, sql, params):
        raise self.error


# -- authentication ----------------------------------------------------------

def test_requests_without_a_key_are_rejected(client):
    response = client.get("/v1/meta")
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "ApiKey"
    assert "X-API-Key" in response.json()["detail"]


def test_wrong_key_is_rejected_and_every_configured_key_works(client):
    assert client.get("/v1/meta", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/meta", headers={"X-API-Key": API_KEY}).status_code == 200
    assert client.get("/v1/meta", headers={"X-API-Key": OTHER_KEY}).status_code == 200


def test_every_reporting_route_requires_a_key(client):
    paths = [path for path in client.get("/openapi.json").json()["paths"]
             if path.startswith("/v1")]
    assert len(paths) == 8
    for path in paths:
        assert client.get(path.replace("{campaign_id}", "x")).status_code == 401, path


def test_operations_endpoints_are_open(client):
    assert client.get("/health").json() == {"status": "ok", "version": "0.1.0"}
    assert client.get("/ready").json()["status"] == "ready"


def test_service_refuses_to_start_without_keys(make_settings):
    with pytest.raises(ValueError, match="API_KEYS"):
        make_settings(api_keys="")
    assert make_settings(api_keys="", auth_disabled=True).auth_disabled


def test_auth_can_be_disabled_for_local_development(make_client):
    local = make_client(api_keys="", auth_disabled=True)
    assert local.get("/v1/meta").status_code == 200


def test_keys_are_never_stored_or_shown_in_plain_text(make_settings):
    settings = make_settings()
    assert API_KEY not in repr(settings) and API_KEY not in str(settings.model_dump())
    assert settings.key_fingerprints == {fingerprint(API_KEY), fingerprint(OTHER_KEY)}
    client_id = authenticate(API_KEY, settings.key_fingerprints)
    assert len(client_id) == 8 and client_id not in API_KEY
    with pytest.raises(AuthError):
        authenticate(None, settings.key_fingerprints)


def test_bigquery_requires_a_project():
    with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
        Settings(_env_file=None, warehouse="bigquery", auth_disabled=True)


# -- rate limiting -----------------------------------------------------------

def test_rate_limit_returns_429_with_retry_after(make_client):
    limited = make_client(rate_limit_per_minute=3)
    statuses = [limited.get("/v1/meta", headers=AUTH).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    blocked = limited.get("/v1/meta", headers=AUTH)
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.json()["title"] == "Too Many Requests"


def test_rate_limit_is_per_key_and_reported_in_headers(make_client):
    limited = make_client(rate_limit_per_minute=2)
    first = limited.get("/v1/meta", headers=AUTH)
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    limited.get("/v1/meta", headers=AUTH)
    assert limited.get("/v1/meta", headers=AUTH).status_code == 429
    assert limited.get("/v1/meta", headers={"X-API-Key": OTHER_KEY}).status_code == 200


def test_rate_limiter_refills_over_time():
    now = [0.0]
    limiter = RateLimiter(per_minute=60, clock=lambda: now[0])
    for _ in range(60):
        limiter.check("a")
    with pytest.raises(RateLimited) as blocked:
        limiter.check("a")
    assert blocked.value.retry_after == 1
    now[0] += 2.0   # two seconds refill two tokens
    assert limiter.check("a") == 1
    limiter.check("a")
    with pytest.raises(RateLimited):
        limiter.check("a")


# -- validation --------------------------------------------------------------

@pytest.mark.parametrize(
    ("path", "params", "field"),
    [
        ("/v1/kpis/summary", {"start_date": "yesterday"}, "start_date"),
        ("/v1/kpis/summary", {"platform": "tiktok"}, "platform"),
        ("/v1/kpis/timeseries", {"granularity": "hour"}, "granularity"),
        ("/v1/campaigns", {"sort": "spend; drop table x"}, "sort"),
        ("/v1/campaigns", {"order": "sideways"}, "order"),
        ("/v1/campaigns", {"limit": "0"}, "limit"),
        ("/v1/campaigns", {"limit": "5000"}, "limit"),
        ("/v1/campaigns", {"offset": "-1"}, "offset"),
        ("/v1/campaigns", {"search": "x" * 101}, "search"),
        ("/v1/pacing", {"as_of": "2026-13-01"}, "as_of"),
    ],
)
def test_invalid_parameters_are_named_in_a_422(client, path, params, field):
    response = client.get(path, params=params, headers=AUTH)
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Invalid Parameters"
    assert field in {error["field"] for error in body["errors"]}


def test_date_range_rules(client):
    backwards = client.get("/v1/kpis/summary", headers=AUTH,
                           params={"start_date": "2026-10-31", "end_date": "2026-10-01"})
    assert backwards.status_code == 422 and "after end_date" in backwards.json()["detail"]
    too_long = client.get("/v1/kpis/summary", headers=AUTH,
                          params={"start_date": "2025-01-01", "end_date": "2026-10-31"})
    assert too_long.status_code == 422 and "366 days" in too_long.json()["detail"]


def test_campaign_id_with_unsafe_characters_is_rejected(client):
    assert client.get("/v1/campaigns/1%27%20OR%20%271", headers=AUTH).status_code == 422


def test_unknown_route_uses_the_same_error_format(client):
    response = client.get("/v2/nothing", headers=AUTH)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


# -- caching -----------------------------------------------------------------

def test_repeated_request_is_served_from_cache(client):
    params = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    first = client.get("/v1/kpis/summary", params=params, headers=AUTH)
    second = client.get("/v1/kpis/summary", params=params, headers=AUTH)
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()
    assert second.headers["cache-control"] == "private, max-age=300"
    other = client.get("/v1/kpis/summary", params={**params, "platform": "meta_ads"},
                       headers=AUTH)
    assert other.headers["x-cache"] == "MISS"


def test_cache_can_be_turned_off(make_client):
    uncached = make_client(cache_ttl_seconds=0)
    for _ in range(2):
        assert uncached.get("/v1/meta", headers=AUTH).headers["x-cache"] == "MISS"


def test_ttl_cache_expires_evicts_and_protects_stored_values():
    now = [0.0]
    cache = TTLCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    calls = []

    def compute():
        calls.append(1)
        return {"rows": [1]}

    value, hit = cache.get_or_compute("a", compute)
    assert not hit
    value["rows"].append(99)                                   # caller mutates its copy
    assert cache.get_or_compute("a", compute) == ({"rows": [1]}, True)
    now[0] = 11
    assert cache.get_or_compute("a", compute)[1] is False      # expired
    assert len(calls) == 2
    cache.get_or_compute("b", compute)
    cache.get_or_compute("c", compute)                         # evicts the oldest ("a")
    assert len(cache) == 2
    assert cache.get_or_compute("a", compute)[1] is False


# -- errors and operations ---------------------------------------------------

def test_warehouse_failure_is_503_without_internal_detail(make_client, caplog):
    broken = make_client(warehouse=BrokenWarehouse(
        WarehouseError("Access Denied: table secret-project.dataset.table")))
    response = broken.get("/v1/kpis/summary", headers=AUTH,
                          params={"start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "secret-project" not in response.text
    assert broken.get("/ready").status_code == 503
    assert broken.get("/health").status_code == 200   # liveness does not depend on it


def test_warehouse_timeout_is_504(make_client):
    slow = make_client(warehouse=BrokenWarehouse(WarehouseTimeout("slow")))
    assert slow.get("/v1/channels", headers=AUTH,
                    params={"start_date": "2026-10-01", "end_date": "2026-10-31"}
                    ).status_code == 504


def test_unexpected_error_is_500_with_request_id_and_no_traceback(make_client, caplog):
    crashing = make_client(warehouse=BrokenWarehouse(RuntimeError("boom: internal detail")))
    with caplog.at_level(logging.ERROR):
        response = crashing.get("/v1/channels", headers=AUTH,
                                params={"start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert response.status_code == 500
    body = response.json()
    assert "boom" not in response.text and "Traceback" not in response.text
    assert body["instance"] == response.headers["x-request-id"]


def test_request_id_is_echoed_or_generated(client):
    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32
    unsafe = client.get("/health", headers={"X-Request-ID": "x\" injected=1"})
    assert unsafe.headers["x-request-id"] != "x\" injected=1"


def test_access_log_has_client_id_but_never_the_key(make_client, capsys):
    logged = make_client(log_level="INFO", log_format="json")
    logged.get("/v1/meta", headers=AUTH)
    err = capsys.readouterr().err
    assert '"path": "/v1/meta"' in err and '"status": 200' in err
    assert fingerprint(API_KEY)[:8] in err
    assert API_KEY not in err


def test_openapi_documents_every_route_and_the_key_header(client):
    spec = client.get("/openapi.json").json()
    assert set(spec["paths"]) == {
        "/health", "/ready", "/v1/meta", "/v1/kpis/summary", "/v1/kpis/timeseries",
        "/v1/campaigns", "/v1/campaigns/{campaign_id}", "/v1/channels", "/v1/platforms",
        "/v1/pacing"}
    scheme = spec["components"]["securitySchemes"]["APIKeyHeader"]
    assert (scheme["in"], scheme["name"]) == ("header", "X-API-Key")
    assert client.get("/docs").status_code == 200


def test_cors_is_off_by_default_and_limited_when_configured(client, make_client):
    origin = {"Origin": "https://dash.example.com", **AUTH}
    assert "access-control-allow-origin" not in client.get("/v1/meta", headers=origin).headers
    allowed = make_client(cors_origins="https://dash.example.com")
    assert allowed.get("/v1/meta", headers=origin).headers["access-control-allow-origin"] \
        == "https://dash.example.com"
    other = {"Origin": "https://evil.example.com", **AUTH}
    assert "access-control-allow-origin" not in allowed.get("/v1/meta", headers=other).headers
