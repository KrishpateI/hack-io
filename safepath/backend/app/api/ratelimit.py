"""Per-user rate limiting (Req 28.1, 28.2).

``RateLimiter`` is a port with an in-memory and a Repository-backed implementation.
Both use a sliding window: at most ``limit`` admitted hits in any ``window`` seconds.
Rejected attempts are not recorded.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Mapping, Protocol, runtime_checkable

from fastapi import Request

from app.adapters.ports import Conflict, Repository

# action -> (max hits, window seconds)
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "report": (5, 600),
    "message": (20, 60),
    "vote": (30, 600),
    "checkin": (10, 600),
}

RATE_LIMIT_TABLE = "ratelimits"


class RateLimitExceeded(Exception):
    def __init__(self, action: str, retry_after_seconds: int):
        super().__init__(f"rate limit exceeded for {action}")
        self.action = action
        self.retry_after_seconds = retry_after_seconds


@runtime_checkable
class RateLimiter(Protocol):
    def check(self, subject: str, action: str) -> None:
        """Record a hit or raise ``RateLimitExceeded`` (never records a rejected hit)."""


def _retry_after(oldest: float, window: int, now: float) -> int:
    return max(1, math.ceil(oldest + window - now))


class InMemoryRateLimiter:
    def __init__(self, limits: Mapping[str, tuple[int, int]] | None = None,
                 clock: Callable[[], float] = time.time):
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, subject: str, action: str) -> None:
        if action not in self.limits:
            raise KeyError(f"unknown rate-limited action {action!r}")
        limit, window = self.limits[action]
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault((subject, action), deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                raise RateLimitExceeded(action, _retry_after(hits[0], window, now))
            hits.append(now)


class RepositoryRateLimiter:
    """Shares state across processes through the Repository (optimistic CAS, retried)."""

    MAX_RETRIES = 10

    def __init__(self, repo: Repository, limits: Mapping[str, tuple[int, int]] | None = None,
                 clock: Callable[[], float] = time.time):
        self.repo = repo
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self._clock = clock

    def check(self, subject: str, action: str) -> None:
        if action not in self.limits:
            raise KeyError(f"unknown rate-limited action {action!r}")
        limit, window = self.limits[action]
        key = {"pk": f"{subject}#{action}", "sk": ""}
        for _ in range(self.MAX_RETRIES):
            now = self._clock()
            row = self.repo.get(RATE_LIMIT_TABLE, key)
            hits = [t for t in (row or {}).get("hits", []) if t > now - window]
            if len(hits) >= limit:
                raise RateLimitExceeded(action, _retry_after(min(hits), window, now))
            hits.append(now)
            patch = {"hits": hits, "ttl": int(now) + window}
            try:
                if row is None:
                    self.repo.put(RATE_LIMIT_TABLE, {**key, **patch, "version": 1}, if_absent=True)
                else:
                    self.repo.update_cas(RATE_LIMIT_TABLE, key, int(row.get("version", 0)), patch)
                return
            except Conflict:
                continue
        # Persistent contention: fail closed for this request.
        raise RateLimitExceeded(action, 1)


def rate_limit(action: str) -> Callable[[Request], None]:
    """FastAPI dependency factory: ``Depends(rate_limit("report"))``.

    Subject is ``request.state.subject`` when auth has set it, else the client host.
    """

    def dependency(request: Request) -> None:
        subject = getattr(request.state, "subject", None) or (
            request.client.host if request.client else "anonymous"
        )
        request.app.state.rate_limiter.check(subject, action)

    return dependency
