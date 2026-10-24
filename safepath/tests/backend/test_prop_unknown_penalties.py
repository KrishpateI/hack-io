"""Property 9: Unknown or low-confidence data is never treated as safe.

**Validates: Requirements 12.4, 13.8, 17.4, 44.7**
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from app.disasters.base import IncidentContext
from app.models.domain import Incident, IncidentType, MobilityProfile, TravelMode
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import CostModel, load_weights
from app.routing.graph import Graph

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
W = load_weights()
UNK = "Unknown"
KNOWN = dict(
    infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
    tunnel_flag=False, elevation_confidence="High", mobility_accessibility="accessible",
)
ATTRS = list(W.uncertainty_attributes)


def _graph(overrides_list, dist):
    ns = [Node(node_id=f"n{k}", lat=41.88 + 0.001 * k, lon=-87.63, kind="intersection")
          for k in range(len(overrides_list) + 1)]
    es = [
        Edge(
            edge_id=f"e{k}", from_node=f"n{k}", to_node=f"n{k + 1}", geometry="",
            distance_meters=dist, estimated_travel_time_seconds=dist / 1.4,
            travel_mode=["walking"], source="osm", data_timestamp=TS,
            classification="Static", **{**KNOWN, **o},
        )
        for k, o in enumerate(overrides_list)
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t", built_at=TS), {})


def _model(g, profile=MobilityProfile.STANDARD):
    inc = Incident(incident_id="i1", incident_type=IncidentType.FLOOD, source="t",
                   created_at=TS, updated_at=TS)
    ctx = IncidentContext(incident=inc, now=TS, origin=LatLon(latitude=41.88, longitude=-87.63),
                          profile=profile, mode=TravelMode.WALKING, reports=())
    return CostModel(g, W, ctx)


dists = st.floats(min_value=1, max_value=2000, allow_nan=False)


@settings(max_examples=60, deadline=None)
@given(
    unknown=st.lists(st.sampled_from(ATTRS), min_size=1, unique=True),
    dist=dists,
)
def test_any_unknown_attribute_adds_positive_penalty_and_raises_cost(unknown, dist):
    g = _graph([{a: UNK for a in unknown}, {}], dist)
    m = _model(g)
    c0, c1 = m.components(0), m.components(1)
    assert c1.uncertainty == {}
    assert sum(c0.uncertainty.values()) > 0
    for a in unknown:
        assert c0.uncertainty[f"unknown_{a}"] > 0
    assert m.cost(0) > m.cost(1)


@settings(max_examples=40, deadline=None)
@given(conf=st.sampled_from(["Low", UNK]), dist=dists)
def test_low_or_unknown_elevation_confidence_is_penalized(conf, dist):
    g = _graph([{"elevation_confidence": conf}, {}], dist)
    m = _model(g)
    key = "elevation_low" if conf == "Low" else "elevation_unknown"
    assert m.components(0).uncertainty[key] > 0
    assert m.cost(0) > m.cost(1)


@settings(max_examples=40, deadline=None)
@given(dist=dists)
def test_unknown_mobility_penalized_under_step_free(dist):
    g = _graph([{"mobility_accessibility": UNK}, {}], dist)
    m = _model(g, MobilityProfile.STEP_FREE)
    assert m.components(0).uncertainty["profile_unknown"] > 0
    assert m.cost(0) > m.cost(1)
