"""Property 15: Policy precedence follows the documented order.

**Validates: Requirements 11.4, 11.5, 11.6, 11.8, 19.6, 20.4, 44.6**
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from app.disasters.base import IncidentContext, PolicyOverride
from app.disasters.rules_engine import evaluate
from app.models.domain import Incident, IncidentType, OfficialNotice, PolicyOutcome
from app.models.routing import LatLon

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
BOX = {
    "type": "Polygon",
    "coordinates": [[[-87.65, 41.87], [-87.62, 41.87], [-87.62, 41.89], [-87.65, 41.89], [-87.65, 41.87]]],
}
FAR = {
    "type": "Polygon",
    "coordinates": [[[-87.50, 41.70], [-87.49, 41.70], [-87.49, 41.71], [-87.50, 41.71], [-87.50, 41.70]]],
}
ORIGIN = LatLon(latitude=41.88, longitude=-87.63)
TYPES = ["shelter_in_place", "incident_destination", "follow_instructions", "transportation_rule", "road_closure"]


def _notice(nid: str, ntype: str, geometry, **kw) -> OfficialNotice:
    base = dict(
        notice_id=nid, incident_id="i1", notice_type=ntype, text=f"text-{nid}", geometry=geometry,
        source_organization="City", source_url="https://x", source_record_id=f"src-{nid}",
        effective_at=NOW - timedelta(hours=1), expires_at=None, published_at=NOW,
        retrieved_at=NOW, verification_status="Officially_Verified", simulation_flag=False,
    )
    base.update(kw)
    return OfficialNotice(**base)


class _Mod:
    incident_type = IncidentType.TORNADO

    def __init__(self, override):
        self._o = override

    def policy_override(self, ctx):
        return self._o


@st.composite
def scenarios(draw):
    kind = draw(st.sampled_from([IncidentType.FLOOD, IncidentType.FIRE, IncidentType.TORNADO]))
    covering = draw(st.lists(st.sampled_from(TYPES), max_size=5))
    far = draw(st.lists(st.sampled_from(TYPES), max_size=3))  # not covering the origin
    ineligible = draw(st.lists(st.sampled_from(TYPES), max_size=3))  # expired / no source / CBD
    override = draw(st.booleans())
    missing = draw(st.lists(st.sampled_from(["graph", "elevation"]), max_size=2))
    route_found = draw(st.booleans())
    return kind, covering, far, ineligible, override, missing, route_found


def _expected(kind, covering, override, missing, route_found):
    s = "shelter_in_place" in covering
    d = "incident_destination" in covering
    fire = kind == IncidentType.FIRE
    if s and d:
        return PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS
    if s and not fire:
        return PolicyOutcome.SHELTER_IN_PLACE
    if "follow_instructions" in covering or (fire and ("transportation_rule" in covering or s)):
        return PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS
    if override:
        return PolicyOutcome.SHELTER_IN_PLACE
    if missing:
        return PolicyOutcome.INSUFFICIENT_DATA
    if not route_found:
        return PolicyOutcome.NO_VERIFIED_ROUTE_AVAILABLE
    return PolicyOutcome.ROUTE_OPTIONS_AVAILABLE


@settings(max_examples=200, deadline=None)
@given(scenarios())
def test_policy_precedence_follows_documented_order(sc):
    kind, covering, far, ineligible, override, missing, route_found = sc
    notices = [_notice(f"c{i}", t, BOX) for i, t in enumerate(covering)]
    notices += [_notice(f"f{i}", t, FAR) for i, t in enumerate(far)]
    for i, t in enumerate(ineligible):
        notices.append(
            [
                _notice(f"x{i}", t, BOX, expires_at=NOW - timedelta(seconds=61)),
                _notice(f"x{i}", t, BOX, source_record_id=""),
                _notice(f"x{i}", t, BOX, cbd_derived=True),
            ][i % 3]
        )
    inc = Incident(
        incident_id="i1", incident_type=kind, geographic_boundary=BOX, status="active",
        source="NWS", created_at=NOW, updated_at=NOW,
    )
    ctx = IncidentContext(incident=inc, now=NOW, origin=ORIGIN, notices=tuple(notices))
    ov = PolicyOverride(PolicyOutcome.SHELTER_IN_PLACE, "Take cover.") if override else None

    decision = evaluate(ctx, module=_Mod(ov), missing_data=missing, route_found=route_found)

    assert decision.outcome == _expected(kind, covering, override, missing, route_found)

    # Conflicts list every conflicting notice's text and Source_Record (11.8).
    if "shelter_in_place" in covering and "incident_destination" in covering:
        conflicting = [
            f"c{i}" for i, t in enumerate(covering) if t in ("shelter_in_place", "incident_destination")
        ]
        assert {n.notice_id for n in decision.notices} == set(conflicting)
        for nid in conflicting:
            assert f"text-{nid}" in decision.explanation and f"src-{nid}" in decision.explanation

    # Incident-specific destinations are ranked, official only, never ineligible ones (11.5).
    assert decision.destination_priority == sorted(
        [f"c{i}" for i, t in enumerate(covering) if t == "incident_destination"]
        + [f"f{i}" for i, t in enumerate(far) if t == "incident_destination"]
    )
