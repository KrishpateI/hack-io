"""Property 32: Rate limiting admits at most the limit per window.

**Validates: Requirements 28.1, 28.2**
"""
# Feature: safepath-evacuation-navigation, Property 32: Rate limiting admits at most the limit per window
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st

from app.adapters.repository import MemoryRepository
from app.api.ratelimit import (
    DEFAULT_LIMITS,
    InMemoryRateLimiter,
    RateLimitExceeded,
    RepositoryRateLimiter,
    rate_limit,
)
from app.api.setup import install_api_infrastructure
from app.config import Settings

# Limits required by Req 28.1 (action -> (max, window seconds)).
REQUIRED = {"report": (5, 600), "message": (20, 60), "vote": (30, 600), "checkin": (10, 600)}
ACTIONS = sorted(REQUIRED)
START = 1_700_000_000.0


class Clock:
    """Controllable clock: tests advance time explicitly instead of sleeping."""

    def __init__(self, t: float = START):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_limiter(kind: str, clock: Clock):
    if kind == "memory":
        return InMemoryRateLimiter(clock=clock)
    return RepositoryRateLimiter(MemoryRepository(), clock=clock)


def _gap(window: int):
    # Mix bursts (zero/small gaps), gaps near the window boundary, and long idle periods.
    return st.one_of(
        st.just(0.0),
        st.floats(min_value=0.0, max_value=window / 10, allow_nan=False),
        st.sampled_from([window - 1.0, float(window), window + 1.0, window - 0.5]),
        st.integers(min_value=0, max_value=2 * window).map(float),
    )


@st.composite
def request_sequences(draw):
    action = draw(st.sampled_from(ACTIONS))
    limit, window = REQUIRED[action]
    steps = draw(st.lists(
        st.tuples(_gap(window), st.sampled_from(["u1", "u2"])),
        min_size=1, max_size=3 * limit + 10,
    ))
    return action, steps


def test_default_limits_match_requirements() -> None:
    for action, cfg in REQUIRED.items():
        assert DEFAULT_LIMITS[action] == cfg


@settings(max_examples=150, deadline=None)
@given(kind=st.sampled_from(["memory", "repository"]), seq=request_sequences())
def test_at_most_limit_per_window_and_never_rejects_below_limit(kind, seq) -> None:
    action, steps = seq
    limit, window = REQUIRED[action]
    clock = Clock()
    lim = make_limiter(kind, clock)
    accepted: dict[str, list[float]] = {"u1": [], "u2": []}

    for gap, subject in steps:
        clock.t += gap
        now = clock.t
        in_window = [a for a in accepted[subject] if a > now - window]
        try:
            lim.check(subject, action)
        except RateLimitExceeded as exc:
            # Rejected only when the subject is already at the limit (never below it).
            assert len(in_window) >= limit, (subject, now, in_window)
            ra = exc.retry_after_seconds
            assert isinstance(ra, int) and ra >= 1
            # Retry-After points at when the oldest in-window hit expires.
            assert ra == max(1, math.ceil(min(in_window) + window - now))
            continue
        assert len(in_window) < limit, (subject, now, in_window)
        accepted[subject].append(now)

    # Global invariant: no window of the configured length holds more than `limit` accepted hits.
    for times in accepted.values():
        for i, t in enumerate(times):
            assert sum(1 for u in times[: i + 1] if u > t - window) <= limit


@settings(max_examples=50, deadline=None)
@given(kind=st.sampled_from(["memory", "repository"]), action=st.sampled_from(ACTIONS),
       extra=st.integers(min_value=1, max_value=5))
def test_retry_after_is_honoured(kind, action, extra) -> None:
    limit, window = REQUIRED[action]
    clock = Clock()
    lim = make_limiter(kind, clock)
    for _ in range(limit):
        lim.check("u", action)
    ra = None
    for _ in range(extra):  # rejected attempts are not recorded
        try:
            lim.check("u", action)
            raise AssertionError("request above limit was admitted")
        except RateLimitExceeded as exc:
            ra = exc.retry_after_seconds
    assert ra == window
    clock.t += ra
    lim.check("u", action)  # admitted once Retry-After has elapsed


# --- HTTP layer: 429 with a positive integer Retry-After header ---

_CLOCK = Clock()


def _build_app() -> FastAPI:
    app = FastAPI()
    install_api_infrastructure(app, Settings())
    r = APIRouter()
    for action in ACTIONS:
        def handler(request: Request):
            return {"ok": True}
        r.add_api_route(f"/{action}", handler, methods=["POST"],
                        dependencies=[Depends(rate_limit(action))])
    app.include_router(r)
    return app


_APP = _build_app()
_CLIENT = TestClient(_APP, raise_server_exceptions=False)


@settings(max_examples=30, deadline=None)
@given(kind=st.sampled_from(["memory", "repository"]), action=st.sampled_from(ACTIONS),
       gaps=st.lists(st.sampled_from([0.0, 1.0, 30.0]), min_size=1, max_size=40))
def test_http_429_with_retry_after(kind, action, gaps) -> None:
    limit, window = REQUIRED[action]
    _CLOCK.t = START
    _APP.state.rate_limiter = make_limiter(kind, _CLOCK)
    accepted: list[float] = []
    for gap in gaps:
        _CLOCK.t += gap
        now = _CLOCK.t
        in_window = [a for a in accepted if a > now - window]
        resp = _CLIENT.post(f"/{action}")
        if len(in_window) < limit:
            assert resp.status_code == 200
            accepted.append(now)
        else:
            assert resp.status_code == 429
            ra = resp.headers["Retry-After"]
            assert ra.isdigit() and int(ra) >= 1
            assert resp.json()["error"]["retry_after_seconds"] == int(ra)
