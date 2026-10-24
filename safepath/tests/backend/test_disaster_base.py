"""Tests for disasters/base.py: PenaltyParts, RestrictionSet/EdgeMask, context, registry."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import numpy as np
import pytest
from hypothesis import given, strategies as st

from app.disasters import base
from app.disasters.base import (
    DisasterModule,
    EdgeMask,
    IncidentContext,
    PenaltyParts,
    RestrictionSet,
)
from app.models.domain import Incident, IncidentType, RestrictionCause
from app.models.routing import LatLon
from app.routing.graph import Graph

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _graph(n: int) -> Graph:
    return Graph(edge_ids=[f"e{i}" for i in range(n)])


def _cause(i: str = "r1") -> RestrictionCause:
    return RestrictionCause(kind="closure", id=i)


def _ctx() -> IncidentContext:
    inc = Incident(
        incident_id="i1", incident_type=IncidentType.FLOOD, source="s",
        created_at=NOW, updated_at=NOW,
    )
    return IncidentContext(incident=inc, now=NOW, origin=LatLon(latitude=41.88, longitude=-87.63))


@given(
    blocked=st.sets(st.integers(0, 29)),
    extra=st.sets(st.text(min_size=1, max_size=3).map(lambda s: "x" + s)),
)
def test_mask_false_exactly_for_blocked_edges(blocked, extra):
    g = _graph(30)
    rs = RestrictionSet({**{f"e{i}": (_cause(),) for i in blocked}, **{e: (_cause(),) for e in extra}}, 3)
    m = rs.mask(g)
    assert isinstance(m, EdgeMask) and m.dtype == bool and m.shape == (30,)
    assert {i for i in range(30) if not m[i]} == blocked  # unknown edge ids ignored


def test_empty_set_allows_all_and_helpers():
    m = RestrictionSet().mask(_graph(4))
    assert m.all() and m.blocked_count == 0
    assert EdgeMask.all_allowed(3).all()
    assert isinstance(np.asarray(m), np.ndarray)


def test_merged_unions_causes():
    a = RestrictionSet({"e1": (_cause("a"),)}, 1)
    b = RestrictionSet({"e1": (_cause("a"), _cause("b")), "e2": (_cause("c"),)}, 5)
    r = a.merged(b)
    assert [c.id for c in r.causes("e1")] == ["a", "b"]
    assert r.is_blocked("e2") and r.overlay_version == 5


def test_penalty_parts_non_negative():
    p = PenaltyParts({"tunnel": 1.5}, {"unknown": 2})
    assert p.total == 3.5 and p.hazard_total == 1.5
    with pytest.raises(ValueError):
        PenaltyParts({"x": -0.1})
    with pytest.raises(ValueError):
        PenaltyParts(uncertainty={"x": float("nan")})


def test_context_is_frozen():
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.now = NOW  # type: ignore[misc]
    assert ctx.notices == ()


class _Mod:
    incident_type = IncidentType.TORNADO

    def policy_override(self, ctx): return None
    def hard_restrictions(self, ctx, graph): return []
    def edge_penalty(self, edge, ctx, graph): return PenaltyParts()
    def route_filter(self, edge, ctx): return True
    def uncertainty_floor(self, ctx): return None
    def advisories(self, ctx): return []


def test_registry_roundtrip():
    mod = _Mod()
    assert isinstance(mod, DisasterModule)
    base.unregister_module(IncidentType.TORNADO)
    try:
        base.register_module(mod)
        assert base.get_module("tornado") is mod
        assert base.registered_modules()[IncidentType.TORNADO] is mod
        with pytest.raises(ValueError):
            base.register_module(_Mod())
        base.register_module(_Mod(), replace=True)
    finally:
        base.unregister_module(IncidentType.TORNADO)
    with pytest.raises(KeyError):
        base.get_module(IncidentType.TORNADO)
