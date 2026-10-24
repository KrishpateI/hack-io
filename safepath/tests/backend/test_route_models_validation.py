"""Tests for domain/routing models and route request validation (task 6.1)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, strategies as st

from app.models.domain import (
    PolicyDecision,
    PolicyOutcome,
    Restriction,
    VerificationStatus,
)
from app.models.routing import ALGORITHM_LABEL, Route, RouteResponse
from app.routing.validation import (
    SUPPORTED_MOBILITY_PROFILES,
    SUPPORTED_TRAVEL_MODES,
    RequestValidationError,
    validate_route_request,
)


def good(**over):
    base = {
        "incident_id": "inc_1",
        "origin": {"latitude": 41.8883, "longitude": -87.6324},
        "destination": {"latitude": 41.8827, "longitude": -87.6233},
        "travel_mode": "walking",
        "mobility_profile": "standard",
    }
    base.update(over)
    return base


def err(raw, exists=None):
    with pytest.raises(RequestValidationError) as ei:
        validate_route_request(raw, exists)
    return ei.value


def test_enums_have_spec_values():
    assert {o.value for o in PolicyOutcome} == {
        "ROUTE_OPTIONS_AVAILABLE", "SHELTER_IN_PLACE", "FOLLOW_OFFICIAL_INSTRUCTIONS",
        "NO_VERIFIED_ROUTE_AVAILABLE", "INSUFFICIENT_DATA",
    }
    assert {v.value for v in VerificationStatus} == {
        "Unverified", "Corroborated", "Officially_Verified", "Disputed", "Resolved", "Expired",
    }


def test_valid_request_defaults_alternatives_to_one():
    req = validate_route_request(good(), lambda i: True)
    assert req.alternatives == 1 and req.travel_mode.value == "walking"


@pytest.mark.parametrize("alts", [0, 4, -1, 1.5, "2", None, True])
def test_bad_alternatives_rejected(alts):
    e = err(good(alternatives=alts))
    assert e.status == 400 and e.code == "validation_error"
    assert [f["field"] for f in e.fields] == ["alternatives"]


@pytest.mark.parametrize("alts", [1, 2, 3])
def test_good_alternatives(alts):
    assert validate_route_request(good(alternatives=alts)).alternatives == alts


def test_unsupported_mode_and_profile_list_supported_values():
    e = err(good(travel_mode="flying", mobility_profile="jetpack"))
    assert e.status == 400
    assert {f["field"] for f in e.fields} == {"travel_mode", "mobility_profile"}
    text = " ".join(f["problem"] for f in e.fields)
    for v in SUPPORTED_TRAVEL_MODES + SUPPORTED_MOBILITY_PROFILES:
        assert v in text


def test_missing_fields_all_listed():
    e = err({})
    assert {f["field"] for f in e.fields} >= {
        "incident_id", "origin", "destination", "travel_mode", "mobility_profile",
    }


def test_outside_region_identifies_endpoint():
    e = err(good(origin={"latitude": 40.0, "longitude": -87.63}))
    assert (e.status, e.code) == (400, "outside_region")
    assert e.fields[0]["field"] == "origin"
    e = err(good(origin={"latitude": 40.0, "longitude": -87.63},
                 destination={"latitude": 40.0, "longitude": -87.63}))
    assert e.message.startswith("both")


def test_invalid_coordinate():
    e = err(good(destination={"latitude": 95, "longitude": -87.63}))
    assert (e.status, e.code) == (400, "invalid_coordinate")
    assert e.fields[0]["field"] == "destination"
    e = err(good(destination={"latitude": "x", "longitude": -87.63}))
    assert e.code == "invalid_coordinate"


def test_boundary_is_inside():
    validate_route_request(good(origin={"latitude": 41.85, "longitude": -87.68}))


def test_unknown_incident_404():
    e = err(good(), lambda i: False)
    assert (e.status, e.code) == (404, "not_found")
    assert e.envelope()["error"]["fields"][0]["field"] == "incident_id"


@given(alts=st.one_of(st.integers(), st.floats(allow_nan=True), st.text()).filter(
    lambda a: not (isinstance(a, int) and 1 <= a <= 3)))
def test_property_invalid_alternatives_always_400(alts):
    e = err(good(alternatives=alts))
    assert e.status == 400 and any(f["field"] == "alternatives" for f in e.fields)


def test_response_models_roundtrip():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    resp = RouteResponse(
        policy_outcome=PolicyOutcome.SHELTER_IN_PLACE, explanation="x", calculated_at=now
    )
    d = resp.model_dump(mode="json")
    assert d["routes"] == [] and d["label"] == ALGORITHM_LABEL
    r = Route(
        route_id="rt_1", distance_meters=1, estimated_duration_seconds=1,
        excluded_edges_count=0, uncertainty_level="Low",
        policy_outcome="ROUTE_OPTIONS_AVAILABLE", calculated_at=now,
    )
    assert r.label == "algorithm-generated" and r.status == "ok"


def test_restriction_cause_and_decision():
    c = Restriction(restriction_id="rst_1", incident_id="i", kind="closure",
                    source_record_id="src_1", simulated=True).cause()
    assert (c.kind, c.id, c.source_record_id, c.simulated) == ("closure", "rst_1", "src_1", True)
    d = PolicyDecision(outcome=PolicyOutcome.INSUFFICIENT_DATA, explanation="e", missing_data=["graph"])
    assert d.notices == []
