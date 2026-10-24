"""Sanity tests for the shared Hypothesis strategies in strategies.py."""
from __future__ import annotations

from collections import defaultdict

from hypothesis import HealthCheck, find, given, settings, strategies as st

from app.geo import CoordinateStatus, classify_coordinate
from app.models.domain import IncidentType, VerificationStatus
from app.models.graph import UNKNOWN
from app.routing.graph import Graph
from strategies import (
    NOTICE_TYPES, coordinates, graphs, incident_contexts, incidents, notices, raw_segments,
    reports, restriction_sets,
)

S = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@S
@given(graphs(multilevel=True))
def test_graphs_satisfy_invariants(g):
    assert isinstance(g, Graph) and g.loaded
    assert not g.model.warnings
    assert 3 <= g.num_nodes <= 12
    nodes = {n.node_id: n for n in g.model.nodes}
    assert len(nodes) == g.num_nodes and len(set(g.edge_ids)) == g.num_edges
    for e in g.model.edges:
        assert e.distance_meters > 0 and e.estimated_travel_time_seconds > 0
        assert e.from_node in nodes and e.to_node in nodes
        if nodes[e.from_node].level != nodes[e.to_node].level:
            assert e.edge_kind == "level_transition"
    assert any(e.edge_kind == "level_transition" for e in g.model.edges)
    assert {n.kind == "indoor" for n in nodes.values()} == {True, False}
    assert int(g.adj_ptr[-1]) == g.num_edges


@S
@given(graphs(connected=True, one_way=False, min_nodes=4, max_nodes=6))
def test_connected_graph_is_reachable(g):
    adj = defaultdict(set)
    for a, b in zip(g.edge_from, g.edge_to):
        adj[int(a)].add(int(b))
    seen, stack = {0}, [0]
    while stack:
        for nxt in adj[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert len(seen) == g.num_nodes


def test_graph_variety_is_reachable():
    kw = dict(settings=settings(max_examples=2000, deadline=None, suppress_health_check=list(HealthCheck)))
    gen = graphs(multilevel=True)
    unknown = find(gen, lambda g: any(
        UNKNOWN in (e.elevation_start, e.elevation_end) for e in g.model.edges), **kw)
    assert unknown.num_edges >= 1
    find(gen, lambda g: any(e.bridge_flag is True for e in g.model.edges), **kw)
    find(gen, lambda g: any(e.tunnel_flag is True for e in g.model.edges), **kw)
    find(gen, lambda g: any(e.infrastructure_level == "lower-level" for e in g.model.edges), **kw)
    find(gen, lambda g: any(e.mobility_accessibility == "inaccessible" for e in g.model.edges), **kw)
    find(gen, lambda g: any(e.mobility_accessibility == "accessible" for e in g.model.edges), **kw)

    def one_way(g):
        pairs = set(zip(g.edge_from.tolist(), g.edge_to.tolist()))
        return any((b, a) not in pairs for a, b in pairs)

    find(gen, one_way, **kw)


def test_graph_shrinks_to_minimum():
    g = find(graphs(), lambda g: g.num_edges >= 1, settings=settings(deadline=None))
    assert g.num_nodes == 3 and g.num_edges <= 4  # minimal: 3 nodes, no extra edges


@S
@given(st.data())
def test_restriction_sets_subset_of_edges(data):
    g = data.draw(graphs())
    rs = data.draw(restriction_sets(g))
    assert set(rs.blocked_edges) <= set(g.edge_ids)
    assert all(causes for causes in rs.blocked_edges.values())
    assert rs.mask(g).blocked_count == len(rs.blocked_edges)


@S
@given(notices(edge_ids=["a", "b"]))
def test_notices_valid(n):
    assert n.notice_type in NOTICE_TYPES
    assert n.expires_at is None or n.expires_at > n.effective_at
    assert set(n.edge_ids) <= {"a", "b"}


def test_notice_variety():
    kw = dict(settings=settings(max_examples=1000, deadline=None))
    find(notices(), lambda n: n.source_record_id == "", **kw)
    find(notices(), lambda n: n.cbd_derived, **kw)
    find(notices(), lambda n: n.expires_at is not None and n.expires_at < n.effective_at.replace(year=2100), **kw)
    for t in NOTICE_TYPES:
        find(notices(), lambda n, t=t: n.notice_type == t, **kw)


@S
@given(incidents(), reports())
def test_incidents_and_reports_valid(i, r):
    assert i.incident_type in set(IncidentType)
    assert r.verification_status in set(VerificationStatus)
    assert i.updated_at >= i.created_at


def test_incident_and_report_variety():
    kw = dict(settings=settings(max_examples=1000, deadline=None))
    for t in IncidentType:
        find(incidents(), lambda i, t=t: i.incident_type is t, **kw)
    find(incidents(), lambda i: i.geographic_boundary is None, **kw)
    find(incidents(), lambda i: i.geographic_boundary is not None and i.simulation_flag, **kw)
    for s in VerificationStatus:
        find(reports(), lambda r, s=s: r.verification_status is s, **kw)


@S
@given(coordinates())
def test_coordinates_match_classifier(c):
    assert classify_coordinate(c.lat, c.lon) == CoordinateStatus(c.status)


def test_coordinate_variety():
    kw = dict(settings=settings(max_examples=1000, deadline=None))
    for s in ("inside", "outside-region", "invalid"):
        find(coordinates(), lambda c, s=s: c.status == s, **kw)


@S
@given(raw_segments())
def test_raw_segments_shape(seg):
    assert seg["distance_meters"] > 0 and seg["geometry"].startswith("LINESTRING")
    assert seg["sources"] and seg["classification"] == "Static"
    assert all(v != "" and v is not None for v in seg.values() if not isinstance(v, (list, dict)))


def test_raw_segments_have_missing_attributes():
    kw = dict(settings=settings(max_examples=1000, deadline=None))
    find(raw_segments(), lambda s: "oneway" not in s, **kw)
    find(raw_segments(), lambda s: s.get("bridge_flag") == UNKNOWN, **kw)


@S
@given(st.data())
def test_incident_contexts(data):
    g = data.draw(graphs(multilevel=True))
    ctx = data.draw(incident_contexts(g))
    iid = ctx.incident.incident_id
    assert all(n.incident_id == iid for n in ctx.notices)
    assert all(r.incident_id == iid for r in ctx.reports)
    assert all(o.incident_id == iid for o in ctx.overlays)
    edge_ids = set(g.edge_ids)
    assert all(set(o.edge_ids) <= edge_ids for o in ctx.overlays)
