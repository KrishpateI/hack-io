"""Property 27: expiry transitions are timely and exclusive.

For any set of reports and sweep time ``t``, every non-Resolved report with ``expires_at < t``
becomes Expired (and its closure overlay is removed), no Resolved report becomes Expired, and no
report with ``expires_at >= t`` changes. Re-running the sweep with the same ``t`` changes nothing.

The exact instant ``expires_at == t`` is excluded: Req 24.5 ("passes its expiration timestamp")
does not pin the boundary and the service treats it as expired.

**Validates: Requirements 24.5, 44.10**
"""
# Feature: safepath-evacuation-navigation, Property 27: Expiry transitions are timely and exclusive
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from hypothesis import given, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.adapters.ports import Principal, Role
from app.auth.accounts import AccountService
from app.communication.events import EventBus
from app.models.domain import VerificationStatus as VS
from app.services.overlays import OVERLAYS_TABLE, OverlayService, RepositoryContextProvider
from app.services.reports import REPORTS_TABLE, ReportService
from app.services.sweeper import DEFAULT_INTERVAL_SECONDS, run_sweep, sweeper_interval
from app.services.verification import VerificationService
from strategies import NOW

INC = "inc_p27"
MOD = Principal("mod", Role.MODERATOR)
SWEEP_AT = NOW + timedelta(days=1)
# How each report is prepared before the sweep.
KINDS = ("unverified", "verified_closure", "resolved", "resolved_after_closure")
_offset = st.integers(-20 * 3600, 20 * 3600).filter(lambda s: s != 0)  # expires_at - t, seconds
_specs = st.lists(st.tuples(st.sampled_from(KINDS), _offset), min_size=1, max_size=6)


def _world():
    repo = MemoryRepository()
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC, "simulation_flag": True})
    clock = lambda: NOW  # noqa: E731  (setup happens at NOW; the sweep is given its own time)
    publisher = InProcessPublisher()
    bus = EventBus(repo, publisher, clock=clock)
    overlays = OverlayService(repo, bus, clock=clock)
    reports = ReportService(repo, bus, clock=clock, alias_secret="p27-secret")
    app = SimpleNamespace(state=SimpleNamespace(
        repository=repo, publisher=publisher, event_bus=bus, overlay_service=overlays,
        report_service=reports, accounts=AccountService(repo),
        routing_context_provider=RepositoryContextProvider(repo)))
    return repo, app, reports, VerificationService(reports, bus, overlays, clock=clock)


def _overlay_rows(repo) -> dict[str, dict]:
    return {r["sk"]: r for r in repo.query(OVERLAYS_TABLE, {"pk": INC}, limit=1000)}


def _report_rows(repo) -> dict[str, dict]:
    return {r["sk"]: r for r in repo.query(REPORTS_TABLE, {"pk": INC}, limit=1000)}


@given(specs=_specs)
def test_expiry_transitions_are_timely_and_exclusive(specs):
    repo, app, reports, svc = _world()
    offsets: dict[str, int] = {}
    for i, (kind, offset) in enumerate(specs):
        rid = reports.create(Principal(f"u{i}", Role.USER), {
            "incident_id": INC, "category": "blocked road",
            "latitude": 41.86 + i * 0.002, "longitude": -87.65}).report.report_id
        key = {"pk": INC, "sk": rid}
        offsets[rid] = offset
        if kind in ("verified_closure", "resolved_after_closure"):
            repo.put(REPORTS_TABLE, {**repo.get(REPORTS_TABLE, key), "affected_edge_ids": [f"e{i}"]})
            svc.moderate(MOD, rid, "verify")
        if kind in ("resolved", "resolved_after_closure"):
            svc.moderate(MOD, rid, "resolve")
        repo.put(REPORTS_TABLE, {**repo.get(REPORTS_TABLE, key),
                                 "expires_at": (SWEEP_AT + timedelta(seconds=offset)).isoformat()})

    before, overlays_before = _report_rows(repo), _overlay_rows(repo)
    version_before = app.state.overlay_service.overlay_version(INC)
    due = {sk for sk, r in before.items()
           if r["verification_status"] != VS.RESOLVED.value and offsets[sk] < 0}

    summary = run_sweep(SWEEP_AT, app=app)
    after, overlays_after = _report_rows(repo), _overlay_rows(repo)

    assert summary["errors"] == []
    assert summary["reports_expired"] == len(due)
    closures_removed = 0
    for sk, old in before.items():
        new = after[sk]
        if sk in due:
            # Overdue and not Resolved: Expired exactly once, with history.
            assert new["verification_status"] == VS.EXPIRED.value
            entry = new["status_history"][-1]
            assert entry["status"] == VS.EXPIRED.value
            assert entry["previous_status"] == old["verification_status"]
            assert [h["status"] for h in new["status_history"]].count(VS.EXPIRED.value) == 1
            if old.get("restriction_id"):
                ov = overlays_after[old["restriction_id"]]
                assert ov["status"] == "removed" and ov["active"] is False
                closures_removed += 1
        else:
            # Resolved or not yet due: untouched (Resolved never becomes Expired).
            assert new == old
            if old.get("restriction_id"):
                assert overlays_after[old["restriction_id"]] == overlays_before[old["restriction_id"]]
    # Each removed closure bumps overlay_version once; nothing else moves it.
    assert app.state.overlay_service.overlay_version(INC) == version_before + closures_removed
    for rid, ov in overlays_before.items():
        if not any(before[sk].get("restriction_id") == rid for sk in due):
            assert overlays_after[rid] == ov

    # Re-running at the same t is a no-op.
    n_events = len(repo.query("events", {"pk": INC}, limit=10_000))
    again = run_sweep(SWEEP_AT, app=app)
    assert again["errors"] == [] and again["reports_expired"] == 0
    assert _report_rows(repo) == after and _overlay_rows(repo) == overlays_after
    assert app.state.overlay_service.overlay_version(INC) == version_before + closures_removed
    assert len(repo.query("events", {"pk": INC}, limit=10_000)) == n_events


def test_default_sweep_interval_meets_60_second_bound():
    """Timeliness (Req 24.5): the scheduled sweep runs at least every 60 seconds by default."""
    assert DEFAULT_INTERVAL_SECONDS <= 60
    assert sweeper_interval({}) == DEFAULT_INTERVAL_SECONDS
