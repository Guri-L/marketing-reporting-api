"""API-key authentication and per-key rate limiting."""

from __future__ import annotations

import hmac
import math
import threading
import time
from collections.abc import Callable

from .settings import fingerprint


class AuthError(Exception):
    """Missing or invalid API key."""


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after


def authenticate(api_key: str | None, valid_fingerprints: frozenset[str]) -> str:
    """Return a short client id for a valid key, or raise AuthError.

    The presented key is hashed and compared in constant time against every
    configured fingerprint, so response time does not reveal how close a guess
    was. The client id (first 8 hex characters of the hash) is safe to log.
    """
    if not api_key:
        raise AuthError("Missing API key. Send it in the X-API-Key header.")
    presented = fingerprint(api_key)
    matched = False
    for valid in valid_fingerprints:
        matched |= hmac.compare_digest(presented, valid)
    if not matched:
        raise AuthError("Invalid API key.")
    return presented[:8]


class RateLimiter:
    """Token bucket per client: ``per_minute`` requests, refilled continuously.

    State is in memory, so the limit applies per API process. With several
    instances the effective limit is the sum; a shared store (Redis) would be
    needed for a strict global limit.
    """

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # client -> (tokens, updated at)

    def check(self, client: str) -> int:
        """Spend one token. Returns tokens remaining, or raises RateLimited."""
        now = self._clock()
        with self._lock:
            tokens, updated = self._buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens < 1:
                self._buckets[client] = (tokens, now)
                raise RateLimited(max(1, math.ceil((1 - tokens) / self.refill_per_second)))
            self._buckets[client] = (tokens - 1, now)
            return int(tokens - 1)
