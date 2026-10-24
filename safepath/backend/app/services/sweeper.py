"""Sweeper: time-driven expiry and retention (Req 24.5, 27.5, 28).

``run_sweep(now, app=...)`` is the single entry point shared by the Lambda EventBridge handler,
the local background task and ``POST /api/admin/sweep``. It is idempotent and safe to run
concurrently with itself or with request handlers: every write is a CAS ``Repository.transact``
(report update + closure removal + ``overlay_version`` bump + events), a lost race is re-read and
found already done, and deletes of already-deleted rows are no-ops.

Steps:
  1. every non-terminal report past ``expires_at`` becomes Expired (status history entry, closure
     overlay removed, ``restriction_removed`` and ``report_status_changed`` events published
     after commit);
  2. check-in locations past the retention period are removed (the check-in record remains);
  3. rate-limit rows and event rows past their ``ttl`` are deleted when the repository does not
     expire them itself (DynamoDB does, via native TTL).

Reads also render an overdue report as Expired before the sweeper has written it (lazy expiry).
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from app.adapters.ports import Repository, TxOp
from app.services.overlays import INCIDENTS_TABLE, INCIDENTS_PK

log = logging.getLogger(__name__)

RATE_LIMIT_TABLE = "ratelimits"
EVENTS_TABLE = "events"
SCAN_LIMIT = 5000
DELETE_BATCH = 100


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _purge_expired_rows(repo: Repository, table: str, now_epoch: float) -> int:
    """Delete rows whose epoch-seconds ``ttl`` has passed. No-op for stores with native TTL."""
    if getattr(repo, "native_ttl", False):
        return 0
    rows = repo.query(table, {}, limit=SCAN_LIMIT)
    doomed = [r for r in rows if isinstance(r.get("ttl"), (int, float)) and r["ttl"] < now_epoch]
    for i in range(0, len(doomed), DELETE_BATCH):
        repo.transact([TxOp(kind="delete", table=table, key={"pk": r["pk"], "sk": r.get("sk", "")})
                       for r in doomed[i:i + DELETE_BATCH]])
    return len(doomed)


DEFAULT_INTERVAL_SECONDS = 60.0


def sweeper_interval(env: dict[str, str] | None = None) -> float | None:
    """Local background interval in seconds, or None when disabled.

    ``SAFEPATH_SWEEPER_INTERVAL_SECONDS`` > 0 enables it; 0 disables it. When unset the default
    is 60 s, except under pytest where it stays off unless the variable is set explicitly."""
    e = os.environ if env is None else env
    raw = e.get("SAFEPATH_SWEEPER_INTERVAL_SECONDS")
    if raw is None or not raw.strip():
        return None if "PYTEST_CURRENT_TEST" in e else DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning("invalid SAFEPATH_SWEEPER_INTERVAL_SECONDS=%r; sweeper disabled", raw)
        return None
    return value if value > 0 else None


async def _sweep_loop(app: Any, interval: float) -> None:
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(run_sweep, None, app=app)
        except asyncio.CancelledError:
            raise
        except Exception:  # never let one failed sweep stop the schedule
            log.exception("background sweep failed")


@asynccontextmanager
async def sweeper_lifespan(app: Any):
    """FastAPI lifespan: runs the local background sweeper (local profile only)."""
    settings = getattr(app.state, "settings", None)
    interval = sweeper_interval() if getattr(settings, "profile", "local") == "local" else None
    task = asyncio.create_task(_sweep_loop(app, interval)) if interval else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def run_sweep(now: datetime | None = None, *, app: Any) -> dict[str, Any]:
    """Run one sweep against the services held by ``app.state``; returns a summary of counts."""
    # Imported lazily: the routers own the lazy service accessors.
    from app.api.routers.checkins import get_checkin_service
    from app.api.routers.events import get_event_bus
    from app.api.routers.reports import get_report_service
    from app.services.verification import VerificationService
    from app.services.wiring import get_overlay_service

    now = _utc(now)
    repo: Repository | None = getattr(app.state, "repository", None)
    bus = get_event_bus(app)
    if repo is None or bus is None:
        raise RuntimeError("storage unavailable")

    verification = VerificationService(get_report_service(app), bus, get_overlay_service(app),
                                       clock=lambda: now)
    summary: dict[str, Any] = {
        "swept_at": now.isoformat(), "incidents_checked": 0, "reports_expired": 0,
        "locations_scrubbed": 0, "rate_limit_rows_deleted": 0, "event_rows_deleted": 0,
        "errors": [],
    }

    def step(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # one failing step must not block the others
            log.exception("sweeper step %s failed", name)
            summary["errors"].append(f"{name}: {type(exc).__name__}")

    def expire_reports() -> None:
        incidents = repo.query(INCIDENTS_TABLE, {"pk": INCIDENTS_PK}, limit=SCAN_LIMIT)
        for row in incidents:
            summary["incidents_checked"] += 1
            incident_id = row.get("sk") or row.get("incident_id")
            try:
                summary["reports_expired"] += verification.expire_due(incident_id)
            except Exception as exc:
                log.exception("expiry failed for incident %s", incident_id)
                summary["errors"].append(f"expire_reports[{incident_id}]: {type(exc).__name__}")

    def scrub() -> None:
        summary["locations_scrubbed"] = get_checkin_service(app).scrub_expired_locations(now)

    step("expire_reports", expire_reports)
    step("scrub_locations", scrub)
    step("rate_limits", lambda: summary.__setitem__(
        "rate_limit_rows_deleted", _purge_expired_rows(repo, RATE_LIMIT_TABLE, now.timestamp())))
    step("events", lambda: summary.__setitem__(
        "event_rows_deleted", _purge_expired_rows(repo, EVENTS_TABLE, now.timestamp())))
    return summary
