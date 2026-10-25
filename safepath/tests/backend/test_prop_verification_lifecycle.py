"""Property 25: verification lifecycle rules.

For any sequence of actions (create, confirm, dispute, moderate verify/resolve, time passing,
expiry sweep) by any actors on a report, the service matches the design state machine:
starts Unverified; Corroborated only after >= corroboration_threshold distinct non-reporter
confirmations; Disputed only after >= dispute_threshold distinct disputes; Officially_Verified
only via an Authorized_Official or a demo Moderator (never via confirmations); duplicate votes
are 409 and own-report votes 403 with no state change; Guests and Users cannot verify or resolve.

**Validates: Requirements 23.3, 24.1, 24.2, 24.3, 24.4, 24.9, 28.6, 44.10**
"""
# Feature: safepath-evacuation-navigation, Property 25: Verification lifecycle rules
from __future__ import annotations

from datetime import timedelta

from hypothesis import assume, given, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.adapters.ports import Principal, Role
from app.communication.events import EventBus
from app.models.domain import VerificationStatus as VS
from app.services.overlays import OverlayService
from app.services.reports import DEFAULT_EXPIRY, REPORTS_TABLE, ReportService
from app.services.verification import (
    OFFICIAL_SOURCE, SIMULATED_MODERATOR_SOURCE, DuplicateVote, InvalidTransition, NotPermitted,
    OwnReportVote, VerificationService,
)
from strategies import NOW

INC = "inc_p25"
REPORTER = "rep"
SUBJECTS = (REPORTER, "u1", "u2", "u3", "u4", "u5")
ROLES = (Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL)
ALL_STATUSES = {s.value for s in VS}
_TERMINAL = {VS.RESOLVED, VS.EXPIRED}

_vote = st.tuples(st.just("vote"), st.sampled_from(SUBJECTS), st.sampled_from(ROLES),
                  st.sampled_from(("confirm", "dispute")))
_moderate = st.tuples(st.just("moderate"), st.sampled_from(SUBJECTS), st.sampled_from(ROLES),
                      st.sampled_from(("verify", "resolve")))
# Seconds the clock advances; mostly small, sometimes enough to cross the 4 h expiry.
_wait = st.tuples(st.just("wait"), st.one_of(st.integers(0, 3600), st.integers(3600, 6 * 3600)))
_sweep = st.tuples(st.just("sweep"))
_steps = st.lists(st.one_of(_vote, _vote, _vote, _moderate, _wait, _sweep), max_size=25)


def _next_status(old: VS, conf: int, disp: int, ct: int, dt: int) -> VS:
    """Voting transitions from the design state diagram."""
    if old == VS.UNVERIFIED:
        if disp >= dt:
            return VS.DISPUTED
        return VS.CORROBORATED if conf >= ct else old
    if old == VS.CORROBORATED:
        return VS.DISPUTED if disp >= dt else old
    if old == VS.DISPUTED:
        return VS.CORROBORATED if conf >= ct and disp < dt else old
    return old  # Officially_Verified never changes by voting


@given(simulated=st.booleans(), ct=st.integers(1, 3), dt=st.integers(1, 3), steps=_steps)
def test_verification_lifecycle_rules(simulated, ct, dt, steps):
    repo = MemoryRepository()
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC,
                           "simulation_flag": simulated})
    clock = [NOW]
    now = lambda: clock[0]  # noqa: E731
    bus = EventBus(repo, InProcessPublisher(), clock=now)
    reports = ReportService(repo, bus, clock=now, alias_secret="p25-secret")
    svc = VerificationService(reports, bus, OverlayService(repo, bus, clock=now), clock=now,
                              corroboration_threshold=ct, dispute_threshold=dt)

    created = reports.create(Principal(REPORTER, Role.USER), {
        "incident_id": INC, "category": "blocked road", "latitude": 41.8708,
        "longitude": -87.6505})
    rid = created.report.report_id
    # 23.3: a new report starts Unverified.
    assert created.report.verification_status == VS.UNVERIFIED
    expires_at = NOW + DEFAULT_EXPIRY

    # Independent model of the report.
    status, conf, disp = VS.UNVERIFIED, 0, 0
    voted: set[str] = set()
    history = [VS.UNVERIFIED.value]
    verified_by: Role | None = None

    def row() -> dict:
        return repo.get(REPORTS_TABLE, {"pk": INC, "sk": rid})

    for step in steps:
        kind = step[0]
        t = clock[0]
        assume(t != expires_at)  # the exact boundary instant is not specified
        time_expired = status not in _TERMINAL and t > expires_at
        inactive = status in _TERMINAL or time_expired
        before = row()

        if kind == "wait":
            clock[0] = t + timedelta(seconds=step[1])
        elif kind == "sweep":
            svc.expire_due(INC)
            if time_expired:
                status = VS.EXPIRED
                history.append(VS.EXPIRED.value)
        elif kind == "vote":
            _, sub, role, vk = step
            p = Principal(sub, role)
            if sub == REPORTER:
                try:
                    svc.vote(p, rid, vk)
                    raise AssertionError(f"own-report {vk} was accepted")
                except OwnReportVote:  # HTTP 403
                    pass
                assert row() == before, "own-report vote changed state"
            elif sub in voted:
                try:
                    svc.vote(p, rid, vk)
                    raise AssertionError(f"duplicate vote by {sub} was accepted")
                except DuplicateVote:  # HTTP 409
                    pass
                assert row() == before, "duplicate vote changed state"
            elif inactive:
                try:
                    svc.vote(p, rid, vk)
                    raise AssertionError(f"vote accepted on {status.value}/expired report")
                except InvalidTransition:
                    pass
                assert row() == before
            else:
                res = svc.vote(p, rid, vk)
                voted.add(sub)
                conf += vk == "confirm"
                disp += vk == "dispute"
                new = _next_status(status, conf, disp, ct, dt)
                assert res.status_changed == (new != status)
                if new != status:
                    history.append(new.value)
                status = new
        else:  # moderate
            _, sub, role, action = step
            p = Principal(sub, role)
            if role in (Role.GUEST, Role.USER):
                # 24.9: Guests and Users cannot set Officially_Verified or Resolved.
                try:
                    svc.moderate(p, rid, action)
                    raise AssertionError(f"{role.value} could {action}")
                except NotPermitted:  # HTTP 403
                    pass
                assert row() == before
            elif inactive or (action == "verify" and status == VS.OFFICIALLY_VERIFIED):
                try:
                    svc.moderate(p, rid, action)
                    raise AssertionError(f"{action} accepted from {status.value}")
                except InvalidTransition:  # HTTP 409
                    pass
                assert row() == before
            elif action == "verify" and role == Role.MODERATOR and not simulated:
                # 24.3: only an Authorized_Official may verify a real-incident report.
                try:
                    svc.moderate(p, rid, action)
                    raise AssertionError("moderator verified a real-incident report")
                except NotPermitted:
                    pass
                assert row() == before
            else:
                res = svc.moderate(p, rid, action)
                if action == "verify":
                    status, verified_by = VS.OFFICIALLY_VERIFIED, role
                    expected_src = (SIMULATED_MODERATOR_SOURCE if role == Role.MODERATOR
                                    else OFFICIAL_SOURCE)
                    assert res.report.source_category == expected_src
                else:
                    status = VS.RESOLVED
                history.append(status.value)

        # ---------------------------------------------------------------- invariants
        r = row()
        stored = r["verification_status"]
        assert stored in ALL_STATUSES  # 24.1
        assert stored == status.value, f"expected {status.value}, stored {stored}"
        assert (r["confirmations"], r["disputes"]) == (conf, disp)
        hist = r["status_history"]
        assert [h["status"] for h in hist] == history
        for prev, cur in zip(hist, hist[1:]):
            assert cur["previous_status"] == prev["status"]
        # Corroborated/Disputed only once the distinct-vote thresholds are met (24.2, 24.4).
        if status == VS.CORROBORATED:
            assert conf >= ct
        if status == VS.DISPUTED:
            assert disp >= dt
        # Officially_Verified only via an official or a demo moderator, never by votes (24.3).
        if VS.OFFICIALLY_VERIFIED.value in history:
            assert verified_by == Role.AUTHORIZED_OFFICIAL or (
                verified_by == Role.MODERATOR and simulated)
            ov = next(h for h in hist if h["status"] == VS.OFFICIALLY_VERIFIED.value)
            assert ov["actor_role"] in (Role.MODERATOR.value, Role.AUTHORIZED_OFFICIAL.value)
        # 44.10: without any vote or moderation the report stays Unverified (until expiry).
        if not voted and verified_by is None and status not in _TERMINAL:
            assert status == VS.UNVERIFIED
        # Lazy read view reports Expired once past expires_at.
        t = clock[0]
        if t != expires_at:
            view = svc.get_report(rid).verification_status
            if status not in _TERMINAL and t > expires_at:
                assert view == VS.EXPIRED
            else:
                assert view == status
