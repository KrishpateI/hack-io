"""Property test: hard restrictions are never traversed (design.md Property 1).

# Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed

**Validates: Requirements 14.1, 14.3, 14.4, 14.6, 17.1, 17.5, 18.1, 18.2, 20.1, 20.2, 44.5**

The expected blocked set is computed by an independent oracle (plain shapely in lon/lat plus
the written eligibility rules), then compared with the resolver output. Routes are searched
with the resolved mask via the lower layers (A*, k_routes) and through RoutingService.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
from hypothesis import HealthCheck, assume, given, settings, strategies as st
from shapely.geometry import LineString, shape

from app.disasters.base import IncidentContext
from app.disasters.earthquake import EarthquakeModule
from app.disasters.fire import FireModule, FirePerimeter
from app.disasters.flood import FloodModule
from app.disasters.tornado import TornadoModule
from app.models.domain import (
    HazardReport,
    IncidentType,
    OfficialNotice,
    Restriction,
    VerificationStatus,
)
from app.routing.alternatives import k_routes
from app.routing.cost import load_weights
from app.routing.restrictions import NOTICE_EXPIRY_GRACE, RestrictionResolver
from app.routing.search import astar
from app.routing.service import InMemoryContextProvider, RoutingService
from app.models.routing import LatLon, RouteRequest

from strategies import NOW, graphs, incident_contexts

# Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
PBT = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)

SPEED = 1.4  # m/s used by the plain distance cost in the search tests


# ============================================================ helpers
def polygon_around(graph, i: int, half: float) -> dict:
    lon, lat = float(graph.node_lon[i]), float(graph.node_lat[i])
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half], [lon - half, lat - half]]
    return {"type": "Polygon", "coordinates": [ring]}


@st.composite
def local_polygons(draw, graph):
    """A polygon centred on a random node, so it usually intersects some edges."""
    i = draw(st.integers(0, graph.num_nodes - 1))
    return polygon_around(graph, i, draw(st.floats(0.00005, 0.0008)))


def edge_line(graph, e: int) -> LineString:
    a, b = int(graph.edge_from[e]), int(graph.edge_to[e])
    return LineString([(graph.node_lon[a], graph.node_lat[a]), (graph.node_lon[b], graph.node_lat[b])])


def hit_by(graph, geojson) -> set[str]:
    poly = shape(geojson)
    lines = [(graph.edge_ids[e], edge_line(graph, e)) for e in range(graph.num_edges)]
    inner, outer = poly.buffer(-1e-9), poly.buffer(1e-9)
    hit_in = {eid for eid, ln in lines if ln.intersects(inner)}
    hit_out = {eid for eid, ln in lines if ln.intersects(outer)}
    # Discard examples where an edge merely touches the polygon boundary: the exact predicate is
    # fp-sensitive there (lon/lat here vs the metric projection in the resolver).
    assume(hit_in == hit_out)
    return hit_out


def notice_in_force(n: OfficialNotice, ctx: IncidentContext) -> bool:
    if n.incident_id != ctx.incident.incident_id or n.cbd_derived or not n.source_record_id:
        return False
    if n.effective_at > ctx.now:
        return False
    return n.expires_at is None or ctx.now <= n.expires_at + NOTICE_EXPIRY_GRACE


def expected_blocked(ctx: IncidentContext, graph, perimeters=()) -> set[str]:
    """Oracle: edges that must be blocked by the written rules."""
    iid = ctx.incident.incident_id
    known = set(graph.edge_ids)
    out: set[str] = set()
    for r in ctx.reports:  # only Officially_Verified, unexpired, same incident
        if (r.incident_id == iid and r.verification_status == VerificationStatus.OFFICIALLY_VERIFIED
                and r.expires_at > ctx.now):
            out |= set(r.affected_edge_ids)
    for o in ctx.overlays:
        if o.active and o.incident_id == iid:
            out |= set(o.edge_ids)
            if o.geometry:
                out |= hit_by(graph, o.geometry)
    for n in ctx.notices:
        if not notice_in_force(n, ctx):
            continue
        if n.notice_type == "road_direction_restriction":
            out |= set(n.edge_ids)
        elif n.notice_type in ("road_closure", "temporary_perimeter"):
            out |= set(n.edge_ids)
            if n.geometry:
                out |= hit_by(graph, n.geometry)
    for p in perimeters:  # fire module: verified perimeters
        if p.source_record_id or p.verification_status == "Officially_Verified":
            out |= hit_by(graph, p.geometry)
    return out & known


@st.composite
def scenario(draw, *, incident_type=None, with_perimeters=False):
    """(graph, ctx, perimeters): random context plus injected polygons and flood-worded notices."""
    g = draw(graphs(min_nodes=3, max_nodes=12))
    ctx = draw(incident_contexts(g))
    itype = incident_type or draw(st.sampled_from(list(IncidentType)))
    inc = ctx.incident.model_copy(update={"incident_type": itype, "status": "active"})
    iid = inc.incident_id

    # In-force notices / overlays with real polygons (the base strategy rarely intersects the graph).
    extra_notices = []
    for k in range(draw(st.integers(0, 2))):
        extra_notices.append(OfficialNotice(
            notice_id=f"poly-n{k}", incident_id=iid,
            notice_type=draw(st.sampled_from(["temporary_perimeter", "road_closure"])),
            text=draw(st.sampled_from(["Flooded underpass closed", "Debris", "Fire perimeter"])),
            geometry=draw(local_polygons(g)),
            source_organization="OEMC", source_url="https://example.invalid/n",
            source_record_id=f"src-poly-n{k}", effective_at=NOW - timedelta(hours=1),
            expires_at=draw(st.one_of(st.none(), st.just(NOW + timedelta(hours=1)),
                                      st.just(NOW - timedelta(seconds=30)),
                                      st.just(NOW - timedelta(minutes=5)))),
            published_at=NOW, retrieved_at=NOW, verification_status="Officially_Verified",
            simulation_flag=draw(st.booleans()), cbd_derived=draw(st.booleans())))
    extra_overlays = [
        Restriction(
            restriction_id=f"poly-o{k}", incident_id=iid,
            kind=draw(st.sampled_from(["impassable_polygon", "prohibited_area", "closure"])),
            geometry=draw(local_polygons(g)), source_record_id=f"src-poly-o{k}",
            active=draw(st.booleans()))
        for k in range(draw(st.integers(0, 2)))
    ]
    perimeters = []
    if with_perimeters and itype is IncidentType.FIRE:  # only the fire module carries perimeters
        for k in range(draw(st.integers(0, 2))):
            perimeters.append(FirePerimeter(
                perimeter_id=f"per{k}", geometry=draw(local_polygons(g)),
                verification_status=draw(st.sampled_from(["Unverified", "Officially_Verified"])),
                source_record_id=draw(st.one_of(st.none(), st.just(f"src-per{k}")))))
    ctx = replace(ctx, incident=inc, notices=(*ctx.notices, *extra_notices),
                  overlays=(*ctx.overlays, *extra_overlays))
    return g, ctx, tuple(perimeters)


def module_for(itype: IncidentType, perimeters=()):
    return {
        IncidentType.FLOOD: lambda: FloodModule(weights=load_weights()),
        IncidentType.EARTHQUAKE: lambda: EarthquakeModule(),
        IncidentType.FIRE: lambda: FireModule(perimeters),
        IncidentType.TORNADO: lambda: TornadoModule(),
    }.get(itype, lambda: None)()


def assert_resolution(resolved, graph, expected: set[str]) -> np.ndarray:
    mask = np.asarray(resolved.mask(graph))
    blocked_by_mask = {graph.edge_ids[i] for i in np.flatnonzero(~mask)}
    assert set(resolved.blocked_edges) == expected
    assert blocked_by_mask == expected
    assert resolved.excluded_edges_count == len(expected)
    return mask


def assert_no_blocked(graph, mask, path) -> None:
    assert all(mask[e] for e in path.edge_indices), "route traverses a blocked edge"


def search_routes(graph, mask, src: int, dst: int, k: int = 3):
    dist = graph.edge_dist
    cost = lambda e: float(dist[e]) / SPEED  # noqa: E731
    first = astar(graph, mask, src, dst, cost, v_max=SPEED)
    alts = k_routes(graph, mask, src, dst, cost, k, v_max=SPEED)
    return ([first] if first else []) + alts


# ============================================================ properties
@PBT
@given(data=st.data())
def test_generic_resolver_blocks_exactly_confirmed_restrictions(data):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    g, ctx, _ = data.draw(scenario())
    resolved = RestrictionResolver().resolve(ctx, g, [])
    assert_resolution(resolved, g, expected_blocked(ctx, g))


@PBT
@given(data=st.data(), itype=st.sampled_from(list(IncidentType)))
def test_module_resolution_matches_oracle_for_every_incident_type(data, itype):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    g, ctx, perimeters = data.draw(scenario(incident_type=itype, with_perimeters=itype is IncidentType.FIRE))
    module = module_for(itype, perimeters)
    resolved = RestrictionResolver().resolve(ctx, g, [module] if module else [])
    assert_resolution(resolved, g, expected_blocked(ctx, g, perimeters))


@PBT
@given(data=st.data())
def test_routes_never_traverse_masked_edges(data):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    itype = data.draw(st.sampled_from(list(IncidentType)))
    g, ctx, perimeters = data.draw(scenario(incident_type=itype, with_perimeters=True))
    module = module_for(itype, perimeters)
    resolved = RestrictionResolver().resolve(ctx, g, [module] if module else [])
    mask = assert_resolution(resolved, g, expected_blocked(ctx, g, perimeters))
    for _ in range(3):
        src = data.draw(st.integers(0, g.num_nodes - 1))
        dst = data.draw(st.integers(0, g.num_nodes - 1))
        for p in search_routes(g, mask, src, dst):
            assert_no_blocked(g, mask, p)


@PBT
@given(data=st.data())
def test_random_masks_are_never_traversed(data):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    from strategies import restriction_sets

    g = data.draw(graphs(min_nodes=3, max_nodes=12))
    rs = data.draw(restriction_sets(g))
    mask = np.asarray(rs.mask(g))
    assert int((~mask).sum()) == len(rs.blocked_edges)
    src = data.draw(st.integers(0, g.num_nodes - 1))
    dst = data.draw(st.integers(0, g.num_nodes - 1))
    for p in search_routes(g, mask, src, dst):
        assert_no_blocked(g, mask, p)


@PBT
@given(data=st.data())
def test_non_verified_reports_never_block(data):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    g, ctx, _ = data.draw(scenario())
    assume(g.num_edges > 0)
    iid = ctx.incident.incident_id
    ids = list(g.edge_ids)
    reps = tuple(
        HazardReport(
            report_id=f"r{k}", incident_id=iid, category="road_closure", latitude=41.871,
            longitude=-87.65, affected_edge_ids=data.draw(st.lists(st.sampled_from(ids), min_size=1)),
            verification_status=data.draw(st.sampled_from(
                [VerificationStatus.UNVERIFIED, VerificationStatus.CORROBORATED,
                 VerificationStatus.DISPUTED])),
            created_at=NOW, expires_at=NOW + timedelta(hours=1), simulated=True,
            source_record_id="src-r")
        for k in range(data.draw(st.integers(1, 4)))
    )
    only_reports = replace(ctx, reports=reps, notices=(), overlays=())
    module = module_for(ctx.incident.incident_type)
    resolved = RestrictionResolver().resolve(only_reports, g, [module] if module else [])
    assert not resolved.blocked_edges
    assert resolved.excluded_edges_count == 0


@PBT
@given(data=st.data())
def test_routing_service_routes_avoid_blocked_edges(data):
    # Feature: safepath-evacuation-navigation, Property 1: Hard restrictions are never traversed
    itype = data.draw(st.sampled_from(
        [IncidentType.FLOOD, IncidentType.EARTHQUAKE, IncidentType.FIRE]))
    g, ctx, _ = data.draw(scenario(incident_type=itype))
    # the strategies' stub manifest only carries ``version``; the service also reads ``built_at``
    object.__setattr__(g, "manifest", SimpleNamespace(version=g.version, built_at=NOW))
    provider = InMemoryContextProvider()
    provider.add_incident(ctx.incident)
    for n in ctx.notices:
        provider.add_notice(n)
    for r in ctx.reports:
        provider.add_report(r)
    for o in ctx.overlays:
        provider.add_overlay(o)
    svc = RoutingService(lambda: g, load_weights, provider, clock=lambda: NOW)

    src = data.draw(st.integers(0, g.num_nodes - 1))
    dst = data.draw(st.integers(0, g.num_nodes - 1))
    req = RouteRequest(
        incident_id=ctx.incident.incident_id,
        origin=LatLon(latitude=float(g.node_lat[src]), longitude=float(g.node_lon[src])),
        destination=LatLon(latitude=float(g.node_lat[dst]), longitude=float(g.node_lon[dst])),
        travel_mode="walking", mobility_profile="standard", alternatives=3,
    )
    resp = svc.calculate(req)

    # What the service itself loaded (reports pass through the provider's live filter).
    loaded = provider.load(ctx.incident.incident_id, NOW)
    svc_ctx = replace(ctx, notices=loaded.notices, reports=loaded.reports, overlays=loaded.overlays)
    blocked = expected_blocked(svc_ctx, g)
    mask = np.array([eid not in blocked for eid in g.edge_ids])

    node_at = {(round(float(g.node_lon[i]), 5), round(float(g.node_lat[i]), 5)): i
               for i in range(g.num_nodes)}
    for route in resp.routes:
        if resp.route_count:
            assert route.excluded_edges_count == len(blocked)
        coords = route.geometry.coordinates
        pts = [node_at.get((round(x, 5), round(y, 5))) for x, y in coords]
        for a, b in zip(pts, pts[1:]):
            if a is None or b is None or a == b:
                continue
            usable_between = [
                e for e in range(g.num_edges)
                if int(g.edge_from[e]) == a and int(g.edge_to[e]) == b and mask[e]
            ]
            assert usable_between, "route step has no unblocked edge"
