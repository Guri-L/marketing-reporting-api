"""A small thread-safe TTL cache for query results.

Reporting tables change once a day, so identical requests within a few minutes
can share one warehouse query. The cache is per process: with several API
instances each keeps its own, which costs some duplicate queries but can never
serve wrong data. Entries are immutable to callers (results are copied out).
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """Return (value, was_cached). ``compute`` runs outside the lock."""
        if self.ttl <= 0:
            return compute(), False
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                self._entries.move_to_end(key)
                self.hits += 1
                return copy.deepcopy(entry[1]), True
            self.misses += 1
        # Two threads missing the same key both compute. That wastes one query
        # at worst and avoids holding a lock across a slow warehouse call.
        value = compute()
        with self._lock:
            self._entries[key] = (self._clock() + self.ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return copy.deepcopy(value), False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
