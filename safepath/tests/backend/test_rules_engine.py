"""Example-based tests for the Rules_Engine (eligibility and precedence)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.disasters.base import IncidentContext, PolicyOverride
from app.disasters.rules_engine import (
    evaluate,
    evaluate_pre_route,
    filter_notices,
    finalize,
)
from app.models.domain import Incident, IncidentType, OfficialNotice, PolicyOutcome
from app.models.routing import LatLon

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
BOX = {
    "type": "Polygon",
    "coordinates": [[[-87.65, 41.87], [-87.62, 41.87], [-87.62, 41.89], [-87.65, 41.89], [-87.65, 41.87]]],
}
INSIDE = LatLon(latitude=41.88, longitude=-87.63)
OUTSIDE = LatLon(latitude=41.90, longitude=-87.61)


def incident(kind=IncidentType.FLOOD, source="NWS"):
    return Incident(
        incident_id="i1", incident_type=kind, geographic_boundary=BOX, status="active",
        source=source, created_at=NOW, updated_at=NOW,
    )


def notice(nid="n1", ntype="road_closure", geometry=None, **kw):
    base = dict(
        notice_id=nid, incident_id="i1", notice_type=ntype, text=f"text-{nid}", geometry=geometry,
        source_organization="City", source_url="https://x", source_record_id=f"src-{nid}",
        effective_at=NOW - timedelta(hours=1), expires_at=None, published_at=NOW,
        retrieved_at=NOW, verification_status="Officially_Verified", simulation_flag=False,
    )
    base.update(kw)
    return OfficialNotice(**base)


def ctx(notices=(), origin=INSIDE, kind=IncidentType.FLOOD):
    return IncidentContext(incident=incident(kind), now=NOW, origin=origin, notices=tuple(notices))


class Mod:
    incident_type = IncidentType.TORNADO

    def __init__(self, override):
        self._o = override

    def policy_override(self, c):
        return self._o


def test_default_route_options():
    assert evaluate(ctx()).outcome == PolicyOutcome.ROUTE_OPTIONS_AVAILABLE


def test_eligibility_filter():
    bad = [
        notice("nosrc", source_record_id=""),
        notice("cbd", cbd_derived=True),
        notice("hmp", "temporary_perimeter", source_organization="Cook County Hazard Mitigation Plan"),
        notice("exp", expires_at=NOW - timedelta(seconds=61)),
        notice("future", effective_at=NOW + timedelta(seconds=1)),
    ]
    ok = [notice("grace", expires_at=NOW - timedelta(seconds=60))]
    eligible, excluded = filter_notices(ctx(bad + ok))
    assert [n.notice_id for n in eligible] == ["grace"]
    assert {e.notice.notice_id for e in excluded} == {"nosrc", "cbd", "hmp", "exp", "future"}
    assert next(e for e in excluded if e.notice.notice_id == "nosrc").label == "Unknown source"


def test_shelter_in_place_notice_covering_origin():
    d = evaluate(ctx([notice("s", "shelter_in_place", BOX)]))
    assert d.outcome == PolicyOutcome.SHELTER_IN_PLACE
    assert d.notices[0].source_record_id == "src-s"


def test_shelter_notice_not_covering_origin_ignored():
    d = evaluate(ctx([notice("s", "shelter_in_place", BOX)], origin=OUTSIDE))
    assert d.outcome == PolicyOutcome.ROUTE_OPTIONS_AVAILABLE


def test_geometryless_notice_applies_to_incident_boundary():
    n = notice("s", "shelter_in_place")
    assert evaluate(ctx([n])).outcome == PolicyOutcome.SHELTER_IN_PLACE
    assert evaluate(ctx([n], origin=OUTSIDE)).outcome == PolicyOutcome.ROUTE_OPTIONS_AVAILABLE


def test_conflicting_notices_include_all_text_and_sources():
    d = evaluate(ctx([notice("s", "shelter_in_place", BOX), notice("d", "incident_destination", BOX)]))
    assert d.outcome == PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS
    assert {n.notice_id for n in d.notices} == {"s", "d"}
    assert "text-s" in d.explanation and "src-d" in d.explanation


def test_unmodelable_and_fire_instructions():
    d = evaluate(ctx([notice("f", "follow_instructions", BOX)]))
    assert d.outcome == PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS
    fire = evaluate(ctx([notice("s", "shelter_in_place", BOX)], kind=IncidentType.FIRE))
    assert fire.outcome == PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS


def test_module_override_with_and_without_notice():
    ov = PolicyOverride(PolicyOutcome.SHELTER_IN_PLACE, "Take cover.")
    d = evaluate(ctx(), module=Mod(ov))
    assert d.outcome == PolicyOutcome.SHELTER_IN_PLACE
    assert "No verified shelter available" in d.explanation
    d2 = evaluate(ctx([notice("s", "shelter_in_place", BOX)]), module=Mod(ov))
    assert d2.notices and d2.explanation.startswith("Shelter in place")  # rank 2 notice wins


def test_notice_outranks_module_override_text_order():
    ov = PolicyOverride(PolicyOutcome.SHELTER_IN_PLACE, "Take cover.", "src-x", ("t",))
    d = evaluate(ctx([notice("t", "transportation_rule", BOX)]), module=Mod(ov))
    assert d.outcome == PolicyOutcome.SHELTER_IN_PLACE
    assert d.explanation.startswith("text-t") and d.explanation.endswith("Take cover.")


def test_insufficient_data_and_precedence_over_rank6():
    d = evaluate(ctx(), missing_data=["elevation", "graph"], route_found=False)
    assert d.outcome == PolicyOutcome.INSUFFICIENT_DATA
    assert d.missing_data == ["elevation", "graph"]


def test_finalize_no_route_and_isolation():
    pre = evaluate_pre_route(ctx())
    assert finalize(pre, True).outcome == PolicyOutcome.ROUTE_OPTIONS_AVAILABLE
    d = finalize(pre, False, ["origin"])
    assert d.outcome == PolicyOutcome.NO_VERIFIED_ROUTE_AVAILABLE and "origin" in d.explanation
    assert finalize(pre, False).outcome == PolicyOutcome.NO_VERIFIED_ROUTE_AVAILABLE
    shelter = evaluate_pre_route(ctx([notice("s", "shelter_in_place", BOX)]))
    assert finalize(shelter, False).outcome == PolicyOutcome.SHELTER_IN_PLACE


def test_destination_priority_official_first():
    d = evaluate(ctx([notice("b", "incident_destination", BOX), notice("a", "incident_destination", BOX,
                 effective_at=NOW - timedelta(hours=2))]))
    assert d.destination_priority == ["a", "b"]
