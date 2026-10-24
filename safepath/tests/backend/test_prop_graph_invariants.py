"""Property tests for Graph structural invariants.

Validates: Requirements 12.5, 12.7, 12.8, 21.2, 44.9, 5.6
"""

from collections import defaultdict
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import ValidationError

from app.models.graph import Edge, Graph, GraphIntegrityError, Node

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
LEVELS = ["surface", "lower-level", "platform", "floor-1", "floor-2"]
POOL = [f"n{i}" for i in range(8)]  # edges may reference ids beyond the present nodes

pos = st.floats(min_value=0.01, max_value=1e5, allow_nan=False, allow_infinity=False)


def mk_node(nid, level):
    kind = "intersection" if level == "surface" else "indoor"
    return Node(node_id=nid, lat=41.87, lon=-87.65, kind=kind, level=level)


def mk_edge(eid, a, b, kind="street", dist=10.0, secs=8.0):
    return Edge(
        edge_id=eid, from_node=a, to_node=b, geometry="abc", distance_meters=dist,
        estimated_travel_time_seconds=secs, travel_mode=["walking"], source="osm",
        data_timestamp=TS, classification="Static", edge_kind=kind,
        transition_type="stairwell" if kind == "level_transition" else None,
    )


@st.composite
def graph_inputs(draw):
    present = draw(st.lists(st.sampled_from(POOL), unique=True, min_size=1, max_size=8))
    levels = {n: draw(st.sampled_from(LEVELS)) for n in present}
    nodes = [mk_node(n, levels[n]) for n in present]
    specs = draw(st.lists(st.tuples(st.sampled_from(POOL), st.sampled_from(POOL), pos, pos),
                          max_size=15))
    edges = []
    for i, (a, b, d, t) in enumerate(specs):
        # Level-crossing edges between present nodes must be transitions to build.
        crossing = a in levels and b in levels and levels[a] != levels[b]
        kind = "level_transition" if crossing else "street"
        edges.append(mk_edge(f"e{i}", a, b, kind, d, t))
    return nodes, edges


# Feature: safepath-evacuation-navigation, Property 18: Graph structural invariants
@SETTINGS
@given(graph_inputs())
def test_built_graph_invariants(data):
    nodes, edges = data
    g = Graph.build(nodes, edges)
    ids = {n.node_id: n for n in nodes}

    # Every kept edge: positive distance/time, both endpoints present (12.7)
    for e in g.edges:
        assert e.distance_meters > 0 and e.estimated_travel_time_seconds > 0
        assert e.from_node in ids and e.to_node in ids
        # Level changes only via Level_Transition (12.5, 44.9)
        if ids[e.from_node].level != ids[e.to_node].level:
            assert e.edge_kind == "level_transition"

    # Edges with absent endpoints are excluded with a warning naming edge_id (12.8)
    kept = {e.edge_id for e in g.edges}
    for e in edges:
        absent = e.from_node not in ids or e.to_node not in ids
        if absent:
            assert e.edge_id not in kept
            assert any(f"edge {e.edge_id} " in w for w in g.warnings)
        else:
            assert e.edge_id in kept
    assert len(g.warnings) == sum(
        1 for e in edges if e.from_node not in ids or e.to_node not in ids
    )


# Feature: safepath-evacuation-navigation, Property 18: Graph structural invariants
@SETTINGS
@given(graph_inputs())
def test_no_indoor_outdoor_path_without_level_transition(data):
    nodes, edges = data
    g = Graph.build(nodes, edges)
    kind = {n.node_id: n.kind for n in nodes}
    adj = defaultdict(set)
    for e in g.edges:
        if e.edge_kind != "level_transition":  # drop all transitions, then reachability
            adj[e.from_node].add(e.to_node)
            adj[e.to_node].add(e.from_node)
    for start in kind:
        seen, stack = {start}, [start]
        while stack:
            for nxt in adj[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        outdoor = kind[start] != "indoor"
        assert all((kind[s] != "indoor") == outdoor for s in seen)


# Feature: safepath-evacuation-navigation, Property 18: Graph structural invariants
@SETTINGS
@given(
    st.sampled_from(LEVELS),
    st.sampled_from(LEVELS),
    st.sampled_from(["street", "indoor"]),
)
def test_level_crossing_non_transition_rejected(la, lb, kind):
    nodes = [mk_node("a", la), mk_node("b", lb)]
    e = mk_edge("x", "a", "b", kind)
    if la == lb:
        assert Graph.build(nodes, [e]).edges == [e]
    else:
        with pytest.raises(GraphIntegrityError):
            Graph.build(nodes, [e])


# Feature: safepath-evacuation-navigation, Property 18: Graph structural invariants
@SETTINGS
@given(
    st.one_of(st.floats(max_value=0, allow_nan=False), st.just(0)),
    st.booleans(),
)
def test_non_positive_distance_or_time_rejected(bad, on_distance):
    with pytest.raises(ValidationError):
        if on_distance:
            mk_edge("x", "a", "b", dist=bad)
        else:
            mk_edge("x", "a", "b", secs=bad)
