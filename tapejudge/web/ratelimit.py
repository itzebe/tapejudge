"""A tiny, dependency-free fixed-window rate limiter.

TapeJudge is a single-process app (SQLite, one instance), so an in-process
limiter is sufficient and adds no infrastructure. It exists to blunt obvious
abuse — scripted entry spam, evaluation flooding, hammering the write API — not
to be a production-grade WAF. Keys are ``(client-ip, bucket)``; a bucket is a
coarse class of request, so a burst on one endpoint cannot lock a user out of the
others.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, *, default_limit: int = 60, window_seconds: int = 60):
        self.default_limit = default_limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._rules: list[tuple[str, int]] = []  # (prefix, limit)

    def rule(self, prefix: str, limit: int) -> None:
        """Set the per-window limit for any key whose bucket starts with prefix."""
        self._rules.append((prefix, limit))

    def _limit_for(self, bucket: str) -> int:
        for prefix, limit in self._rules:
            if bucket.startswith(prefix):
                return limit
        return self.default_limit

    def allow(self, ip: str, bucket: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        key = (ip, bucket)
        limit = self._limit_for(bucket)
        with self._lock:
            stamps = [t for t in self._hits.get(key, []) if t > now - self.window]
            if len(stamps) >= limit:
                self._hits[key] = stamps
                return False
            stamps.append(now)
            self._hits[key] = stamps
            # Opportunistic cleanup so the dict cannot grow without bound.
            if len(self._hits) > 10_000:
                for k in [k for k, v in self._hits.items()
                          if not v or v[-1] <= now - self.window]:
                    self._hits.pop(k, None)
            return True
