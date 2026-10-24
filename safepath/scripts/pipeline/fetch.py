"""Injectable fetch layer with bounded retries (Req 7.4).

A ``Fetcher`` is any callable ``(url, timeout_seconds) -> bytes`` that raises on failure.
Tests inject fakes; production uses :func:`urllib_fetcher`.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Callable

FETCH_ATTEMPTS = 3
FETCH_TIMEOUT_SECONDS = 60

Fetcher = Callable[[str, float], bytes]


def urllib_fetcher(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SafePath-DataPipeline/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https sources)
        return response.read()


@dataclass
class FetchOutcome:
    data: bytes | None
    attempts: int
    errors: list[str]

    @property
    def ok(self) -> bool:
        return self.data is not None


def fetch_with_retries(
    fetcher: Fetcher,
    url: str,
    *,
    attempts: int = FETCH_ATTEMPTS,
    timeout: float = FETCH_TIMEOUT_SECONDS,
) -> FetchOutcome:
    """Try ``fetcher`` up to ``attempts`` times; never raises."""
    errors: list[str] = []
    for n in range(1, attempts + 1):
        try:
            return FetchOutcome(data=fetcher(url, timeout), attempts=n, errors=errors)
        except Exception as exc:  # noqa: BLE001 - any fetch failure counts as an attempt
            errors.append(f"attempt {n}: {type(exc).__name__}: {exc}")
    return FetchOutcome(data=None, attempts=attempts, errors=errors)
