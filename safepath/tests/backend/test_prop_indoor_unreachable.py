"""Task 11.7: indoor tests including the indoor variant of Property 6.

# Feature: safepath-evacuation-navigation, Property 6: Unreachable or isolated endpoints yield NO_VERIFIED_ROUTE_AVAILABLE
**Validates: Requirements 21.2, 21.4, 21.7, 44.9, 44.4**

Indoor variant: the fictional SIMULATED Indoor_Graph merged into the fixture outdoor graph,
routed through RoutingService with ``origin_indoor_node_id``. Confirmed stairwell closures are
in-memory overlays (demo Moderator-confirmed), which the Building_Emergency_Module expands to
every edge touching the stairwell.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import assume, given, settings, strategies as st

from app.disasters.building import build_stair_index
from app.models.domain import Incident, IncidentType, Restriction
from app.models.routing import LatLon, RouteRequest
from app.routing import indoor
from app.routing.cost import load_weights
from app.routing.graph import Graph
from app.routing.service import InMemoryContextProvider, RoutingService

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
INC = "inc-building"
ANCHOR = LatLon(latitude=41.8830, longitude=-87.6345)
DEST = LatLon(latitude=41.8835, longitude=-87.6350)
W = load_weights()

GRAPH = indoor.merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
STAIRS = build_stair_index(GRAPH)
INDOOR = {i for i, n in enumerate(GRAPH.model.nodes) if n.kind == "indoor"}
INDOOR_IDS = sorted(GRAPH.model.nodes[i].node_id for i in INDOOR)
UPPER_IDS = [n for n in INDOOR_IDS if GRAPH.model.nodes[GRAPH.node_index[n]].level in ("L2", "L3")]
STAIR_EDGE_IDS = {e for ids in STAIRS.edges.values() for e in ids}
# indoor edges not touching a stairwell (corridors, zones, exit transitions)
OTHER_INDOOR_EDGES = sorted(
    e.edge_id for e in GRAPH.model.edges
    if (GRAPH.node_index[e.from_node] in INDOOR or GRAPH.node_index[e.to_node] in INDOOR)
    and e.edge_id not in STAIR_EDGE_IDS
)


def _service(closed_edges: list[str]) -> RoutingService:
    provider = InMemoryContextProvider()
    provider.add_incident(Incident(
        incident_id=INC, incident_type=IncidentType.BUILDING, source="test", created_at=NOW,
        updated_at=NOW, simulation_flag=True))
    if closed_edges:
        provider.add_overlay(Restriction(
            restriction_id="cl-indoor", incident_id=INC, kind="stairwell_closure",
            edge_ids=sorted(closed_edges), source_record_id="SIM-MODERATOR", simulated=True))
    return RoutingService(lambda: GRAPH, lambda: W, provider, clock=lambda: NOW)


def _calc(origin_node: str, closed_edges: list[str]):
    return _service(closed_edges).calculate(RouteRequest(
        incident_id=INC, origin=ANCHOR, destination=DEST, travel_mode="walking",
        mobility_profile="standard", alternatives=1, origin_indoor_node_id=origin_node))


def _closure(stairs: set[str], extra: list[str] = ()) -> list[str]:
    return sorted({e for s in stairs for e in STAIRS.edges[s]} | set(extra))


def _reaches_outdoor(origin_node: str, closed: set[str]) -> bool:
    """Oracle: BFS over directed, non-closed edges; True if any outdoor node is reachable."""
    src = GRAPH.node_index[origin_node]
    seen, queue = {src}, deque([src])
    while queue:
        u = queue.popleft()
        if u not in INDOOR:
            return True
        for e in GRAPH.out_edges(u):
            e = int(e)
            v = int(GRAPH.edge_to[e])
            if GRAPH.edge_ids[e] not in closed and v not in seen:
                seen.add(v)
                queue.append(v)
    return False


def _origin_isolated(origin_node: str, closed: set[str]) -> bool:
    return all(GRAPH.edge_ids[int(e)] in closed for e in GRAPH.out_edges(GRAPH.node_index[origin_node]))


def _assert_no_route(resp, origin_isolated: bool) -> None:
    assert resp.policy_outcome.value == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert resp.explanation.strip()
    assert resp.route_count == 0
    assert all(r.geometry.coordinates == [] for r in resp.routes)
    if origin_isolated:
        assert "origin" in resp.explanation, resp.explanation


# --------------------------------------------------------------------------- property 6 (indoor)
@settings(settings.get_profile("ci"), max_examples=100, deadline=None)
@given(
    origin=st.sampled_from(UPPER_IDS),
    extra=st.lists(st.sampled_from(OTHER_INDOOR_EDGES), unique=True, max_size=6),
)
def test_all_stairwells_removed_yields_no_verified_route_for_upper_floors(origin, extra):
    closed = _closure({"A", "B", "C"}, extra)
    assert not _reaches_outdoor(origin, set(closed))
    _assert_no_route(_calc(origin, closed), _origin_isolated(origin, set(closed)))


@settings(settings.get_profile("ci"), max_examples=100, deadline=None)
@given(
    origin=st.sampled_from(INDOOR_IDS),
    stairs=st.sets(st.sampled_from(sorted(STAIRS.edges))),
    extra=st.lists(st.sampled_from(OTHER_INDOOR_EDGES), unique=True, max_size=len(OTHER_INDOOR_EDGES)),
)
def test_indoor_origin_cut_from_every_exit_yields_no_verified_route(origin, stairs, extra):
    closed = _closure(stairs, extra)
    assume(not _reaches_outdoor(origin, set(closed)))
    _assert_no_route(_calc(origin, closed), _origin_isolated(origin, set(closed)))


# --------------------------------------------------------------------------- examples
def _edge_ids(resp) -> list[str]:
    return [p.edge_id for p in resp.routes[0].edge_penalties]


EDGE_BY_ID = {e.edge_id: e for e in GRAPH.model.edges}


def _stairs_used(resp) -> set[str]:
    return {STAIRS.edge_stair[e] for e in _edge_ids(resp)
            if EDGE_BY_ID[e].transition_type == "stairwell"}


def test_indoor_and_outdoor_connect_only_via_exit_level_transitions():
    crossing = [
        e for e in GRAPH.model.edges
        if (GRAPH.node_index[e.from_node] in INDOOR) != (GRAPH.node_index[e.to_node] in INDOOR)
    ]
    assert crossing
    for e in crossing:
        assert e.edge_kind == "level_transition" and e.transition_type == "exit"
    exit_nodes = {n for n in INDOOR_IDS if "-exit-" in n}
    assert len(exit_nodes) >= 2
    for x in exit_nodes:  # every building exit reaches an outdoor node via a Level_Transition
        assert any(e.from_node == x and GRAPH.node_index[e.to_node] not in INDOOR for e in crossing)
    # a routed indoor path leaves the building through an exit transition
    resp = _calc("sim-indoor-L3-zone-1", [])
    assert resp.policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE"
    ids = _edge_ids(resp)
    first_outdoor = next(eid for eid in ids if GRAPH.node_index[EDGE_BY_ID[eid].to_node] not in INDOOR)
    assert EDGE_BY_ID[first_outdoor].transition_type == "exit"


def test_individual_stairwell_closures_reroute():
    origin = "sim-indoor-L3-cor-W"
    base = _calc(origin, [])
    assert base.policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE"
    assert _stairs_used(base) == {"A"}
    for closed in ({"A"}, {"B"}, {"C"}, {"A", "B"}, {"A", "C"}, {"B", "C"}):
        resp = _calc(origin, _closure(closed))
        assert resp.policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE", closed
        used = _stairs_used(resp)
        assert used and not (used & closed), (closed, used)
        assert not any(e in STAIR_EDGE_IDS and STAIRS.edge_stair[e] in closed for e in _edge_ids(resp))
        assert resp.routes[0].excluded_edges_count >= sum(len(STAIRS.edges[s]) for s in closed)


def test_all_stairwells_removed_only_affects_upper_floors():
    closed = _closure({"A", "B", "C"})
    for origin in ("sim-indoor-L2-zone-1", "sim-indoor-L3-zone-2"):
        _assert_no_route(_calc(origin, closed), False)
    assert _calc("sim-indoor-L1-cor-W", closed).policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE"


def test_both_exits_closed_yields_no_verified_route_from_ground_floor():
    exits = [e.edge_id for e in GRAPH.model.edges if e.transition_type == "exit"]
    _assert_no_route(_calc("sim-indoor-L1-cor-W", exits), False)
