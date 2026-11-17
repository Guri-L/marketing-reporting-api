"""FastAPI dependencies: authentication, shared services and common query parameters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, Query, Request, Response, Security
from fastapi.security import APIKeyHeader

from .schemas import Platform
from .security import authenticate
from .service import ReportingService
from .settings import Settings

api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False,
    description="API key issued by the service owner")


class InvalidRange(ValueError):
    """The requested dates are not usable."""


def get_service(request: Request) -> ReportingService:
    return request.app.state.service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_client(
    request: Request,
    response: Response,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> str:
    """Authenticate, then rate-limit. Returns the client id used in logs."""
    settings: Settings = request.app.state.settings
    client = "anonymous" if settings.auth_disabled \
        else authenticate(api_key, settings.key_fingerprints)
    request.state.client = client
    remaining = request.app.state.rate_limiter.check(client)
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return client


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


def date_range(
    service: Annotated[ReportingService, Depends(get_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    start_date: Annotated[date | None, Query(
        description="First day, YYYY-MM-DD. Defaults to 30 days ending at end_date.")] = None,
    end_date: Annotated[date | None, Query(
        description="Last day, YYYY-MM-DD. Defaults to the latest day with data.")] = None,
) -> DateRange:
    if end_date is None:
        end_date = service.default_period()[1]
    if start_date is None:
        start_date = end_date - timedelta(days=settings.default_range_days - 1)
    if start_date > end_date:
        raise InvalidRange("start_date must not be after end_date")
    if (end_date - start_date).days + 1 > settings.max_range_days:
        raise InvalidRange(f"Date range must not exceed {settings.max_range_days} days")
    return DateRange(start_date, end_date)


def platform_filter(
    platform: Annotated[Platform | None, Query(description="Limit to one ad platform")] = None,
) -> str | None:
    return platform.value if platform else None


Client = Annotated[str, Depends(require_client)]
Service = Annotated[ReportingService, Depends(get_service)]
Range = Annotated[DateRange, Depends(date_range)]
PlatformFilter = Annotated[str | None, Depends(platform_filter)]


def mark_cache(response: Response, cached: bool, ttl: float) -> None:
    response.headers["X-Cache"] = "HIT" if cached else "MISS"
    # private: results depend on the caller's key, so shared proxies must not store them.
    response.headers["Cache-Control"] = f"private, max-age={int(ttl)}"
