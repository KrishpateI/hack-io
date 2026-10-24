"""Property 4: directed edges are traversed only in their permitted direction (task 6.17).

Covers search (never traverses an edge backwards), road_direction_restriction notices (block only
the listed directed edges via RestrictionResolver and RoutingService) and the one-way handling of
scripts/build_graph.py (walking both directions, driving the permitted direction).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from hypothesis import HealthCheck, given, settings, strategies as st

from app.disasters.base import IncidentContext
from app.models.domain import Incident, IncidentType, OfficialNotice
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import load_weights
from app.routing.graph import Graph
from app.routing.restrictions import RestrictionResolver
from app.routing.search import astar, dijkstra
from app.routing.service import InMemoryContextProvider, RoutingService

from strategies import NOW, graphs

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_graph as bg  # noqa: E402

PBT = settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
INC = "inc-dir"
DIRECTED_TAG = "Property 4: Directed edges are traversed only in their permitted direction"


def _cost(g):
    return lambda e: float(g.edge_dist[e])


def _pairs(g):
    return {(int(g.edge_from[e]), int(g.edge_to[e])) for e in range(g.num_edges)}


# ------------------------------------------------------------------ search never goes backwards
# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@PBT
@given(data=st.data())
def test_paths_follow_edge_direction(data):
    g = data.draw(graphs(min_nodes=3, max_nodes=12, one_way=True))
    mask = np.array([data.draw(st.booleans()) for _ in range(g.num_edges)], dtype=bool)
    s = data.draw(st.integers(0, g.num_nodes - 1))
    t = data.draw(st.integers(0, g.num_nodes - 1))
    pairs = _pairs(g)
    for path in (
        dijkstra(g, mask, s, t, _cost(g)),
        astar(g, mask, s, t, _cost(g), v_max=1.0),
    ):
        if path is None:
            continue
        assert path.node_indices[0] == s and path.node_indices[-1] == t
        assert len(path.edge_indices) == len(path.node_indices) - 1
        for k, e in enumerate(path.edge_indices):
            assert mask[e]
            # the edge is traversed from its from_node to its to_node, never reversed
            assert int(g.edge_from[e]) == path.node_indices[k]
            assert int(g.edge_to[e]) == path.node_indices[k + 1]
            assert (path.node_indices[k], path.node_indices[k + 1]) in pairs


# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@PBT
@given(data=st.data())
def test_one_way_pair_has_no_direct_reverse_path(data):
    g = data.draw(graphs(min_nodes=3, max_nodes=12, one_way=True))
    pairs = _pairs(g)
    one_way = sorted((a, b) for (a, b) in pairs if a != b and (b, a) not in pairs)
    if not one_way:
        return
    a, b = data.draw(st.sampled_from(one_way))
    # edges whose only forward edge exists: head -> tail has no direct single-edge path
    for path in (dijkstra(g, None, b, a, _cost(g)), astar(g, None, b, a, _cost(g), v_max=1.0)):
        assert path is None or len(path.edge_indices) >= 2
    # and with every edge except the forward one removed, head -> tail is unreachable
    # only when no other route exists; blocking everything but a->b must make b->a None
    only = np.array([(int(g.edge_from[e]), int(g.edge_to[e])) == (a, b) for e in range(g.num_edges)])
    assert dijkstra(g, only, b, a, _cost(g)) is None
    fwd = dijkstra(g, only, a, b, _cost(g))
    assert fwd is not None and len(fwd.edge_indices) >= 1


# ------------------------------------------------------------------ road_direction_restriction
def _incident():
    return Incident(incident_id=INC, incident_type=IncidentType.FLOOD, source="t", created_at=NOW,
                    updated_at=NOW, simulation_flag=True)


def _notice(edge_ids, nid="n-dir"):
    return OfficialNotice(
        notice_id=nid, incident_id=INC, notice_type="road_direction_restriction", text="t",
        edge_ids=list(edge_ids), source_organization="CDOT", source_url="https://example.invalid",
        source_record_id="src-" + nid, effective_at=NOW - timedelta(hours=1), published_at=NOW,
        retrieved_at=NOW, verification_status="Officially_Verified", simulation_flag=True)


# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@PBT
@given(data=st.data())
def test_direction_notice_blocks_exactly_listed_edges(data):
    g = data.draw(graphs(min_nodes=3, max_nodes=12, one_way=True))
    ids = list(g.edge_ids)
    listed = data.draw(st.lists(st.sampled_from(ids), unique=True)) if ids else []
    ctx = IncidentContext(incident=_incident(), now=NOW, origin=LatLon(
        latitude=float(g.node_lat[0]), longitude=float(g.node_lon[0])),
        notices=(_notice(listed),))
    rs = RestrictionResolver().resolve(ctx, g)
    assert set(rs.blocked_edges) == set(listed)
    for eid in listed:
        assert [c.kind for c in rs.causes(eid)] == ["road_direction"]
    mask = np.asarray(rs.mask(g))
    for e, eid in enumerate(ids):
        assert bool(mask[e]) == (eid not in listed)
    # reverse edges of blocked edges that are not themselves listed remain usable
    pairs = {(int(g.edge_from[e]), int(g.edge_to[e])): e for e in range(g.num_edges)}
    for eid in listed:
        e = ids.index(eid)
        rev = pairs.get((int(g.edge_to[e]), int(g.edge_from[e])))
        if rev is not None and ids[rev] not in listed:
            assert mask[rev]
    # routing on the masked graph never uses a listed edge
    s = data.draw(st.integers(0, g.num_nodes - 1))
    t = data.draw(st.integers(0, g.num_nodes - 1))
    path = dijkstra(g, mask, s, t, _cost(g))
    if path is not None:
        assert not set(path.edge_ids) & set(listed)


def _line_graph(n=4):
    """n nodes on a north-south line (~45 m apart); edges e{i}f (i->i+1) and e{i}r (i+1->i)."""
    nodes = [Node(node_id=f"n{i}", lat=41.88 + 0.0004 * i, lon=-87.63, kind="intersection")
             for i in range(n)]
    known = dict(infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
                 tunnel_flag=False, elevation_confidence="High",
                 mobility_accessibility="accessible", road_type="residential")
    edges = []
    for i in range(n - 1):
        a, b = nodes[i], nodes[i + 1]
        for suffix, (f, t) in (("f", (a, b)), ("r", (b, a))):
            edges.append(Edge(
                edge_id=f"e{i}{suffix}", from_node=f.node_id, to_node=t.node_id,
                geometry=f"LINESTRING ({f.lon} {f.lat}, {t.lon} {t.lat})", distance_meters=44.5,
                estimated_travel_time_seconds=31.8, travel_mode=["walking"], source="osm",
                data_timestamp=NOW, classification="Static", edge_kind="street", **known))
    return Graph._from_model(GraphModel.build(nodes, edges), SimpleNamespace(version="line", built_at=NOW), {})


_LINE = _line_graph()
_WEIGHTS = load_weights()


def _service(blocked_ids):
    prov = InMemoryContextProvider()
    prov.add_incident(_incident())
    if blocked_ids:
        prov.add_notice(_notice(blocked_ids))
    return RoutingService(lambda: _LINE, lambda: _WEIGHTS, prov, clock=lambda: NOW)


def _route(svc, a: int, b: int):
    na, nb = _LINE.model.nodes[a], _LINE.model.nodes[b]
    req = svc.validate({
        "incident_id": INC, "origin": {"latitude": na.lat, "longitude": na.lon},
        "destination": {"latitude": nb.lat, "longitude": nb.lon},
        "travel_mode": "walking", "mobility_profile": "standard", "alternatives": 1})
    return svc.calculate(req)


# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@settings(max_examples=100, deadline=None)
@given(blocked=st.integers(0, 2), reverse_direction=st.booleans())
def test_service_direction_notice_blocks_only_listed_direction(blocked, reverse_direction):
    suffix, other = ("r", "f") if reverse_direction else ("f", "r")
    svc = _service([f"e{blocked}{suffix}"])
    last = 3
    # travelling along the blocked direction is impossible; the opposite direction still works
    along = (last, 0) if reverse_direction else (0, last)
    against = (along[1], along[0])
    blocked_resp = _route(svc, *along)
    assert blocked_resp.policy_outcome.value == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert blocked_resp.route_count == 0
    ok = _route(svc, *against)
    assert ok.policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE" and ok.route_count == 1
    ids = [p.edge_id for p in ok.routes[0].edge_penalties]
    assert ids and all(i.endswith(other) for i in ids)
    assert f"e{blocked}{suffix}" not in ids


# ------------------------------------------------------------------ build_graph one-way handling
_MODES = st.lists(st.sampled_from(["walking", "wheelchair", "driving", "bicycle"]),
                  max_size=4, unique=True)
_ONEWAY = st.sampled_from(["forward", "reverse", "no", "Unknown", "bogus"])


def _expected(oneway, modes):
    modes = [m for m in modes if m in ("walking", "wheelchair", "driving")]
    walk = [m for m in modes if m != "driving"]
    if oneway == "forward":
        return {"f": modes, **({"r": walk} if walk else {})}
    if oneway == "reverse":
        return {**({"f": walk} if walk else {}), "r": modes}
    return {"f": modes, "r": modes}


# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@PBT
@given(oneway=_ONEWAY, modes=_MODES, omit=st.booleans())
def test_build_graph_directions_per_mode(oneway, modes, omit):
    seg = {"travel_mode": modes}
    if not omit:
        seg["oneway"] = oneway
    got = dict(bg._directions(seg))
    want = _expected("Unknown" if omit else oneway, modes)
    assert got == want
    eff = "Unknown" if omit else oneway
    for mode in ("walking", "wheelchair"):
        if mode in modes:  # pedestrian modes are available in both directions regardless of oneway
            assert mode in got["f"] and mode in got["r"]
    if "driving" in modes and eff in ("forward", "reverse"):
        permitted, opposite = ("f", "r") if eff == "forward" else ("r", "f")
        assert "driving" in got[permitted]
        assert "driving" not in got.get(opposite, [])
    elif "driving" in modes:
        assert "driving" in got["f"] and "driving" in got["r"]


# Feature: safepath-evacuation-navigation, Property 4: Directed edges are traversed only in their permitted direction
# **Validates: Requirements 11.7, 12.6, 44.3**
@PBT
@given(oneway=_ONEWAY, modes=_MODES.filter(bool))
def test_build_network_edges_have_correct_endpoints_and_modes(oneway, modes):
    seg = {
        "segment_id": "s1", "geometry": "LINESTRING (-87.6300000 41.8800000, -87.6300000 41.8810000)",
        "distance_meters": 111.0, "oneway": oneway, "travel_mode": modes, "road_type": "residential",
        "sources": ["osm_streets"], "source_ids": {"osm_streets": "1"},
    }
    nodes, edges, _ = bg.build_network([seg], {}, datetime(2026, 3, 15, tzinfo=timezone.utc))
    by_id = {e.edge_id: e for e in edges}
    start, end = bg.node_id_for(-87.63, 41.88), bg.node_id_for(-87.63, 41.881)
    want = _expected(oneway, modes)
    assert set(by_id) == {f"s1:{k}" for k in want}
    for k, m in want.items():
        e = by_id[f"s1:{k}"]
        assert (e.from_node, e.to_node) == ((start, end) if k == "f" else (end, start))
        assert list(e.travel_mode) == m
        if "driving" in m and oneway == "Unknown":
            assert "oneway_unknown" in e.hazard_flags
    # driving routing on the built graph follows the permitted direction only
    model = GraphModel.build(nodes, edges)
    g = Graph._from_model(model, SimpleNamespace(version="b"), {})
    for mode in ("walking", "driving"):
        usable = np.array([mode in e.travel_mode for e in model.edges], dtype=bool)
        fwd = dijkstra(g, usable, start, end, _cost(g))
        rev = dijkstra(g, usable, end, start, _cost(g))
        for path, key in ((fwd, "f"), (rev, "r")):
            has = key in want and mode in want[key]
            assert (path is not None) == has
