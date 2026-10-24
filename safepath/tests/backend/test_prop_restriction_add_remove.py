"""Property 3: restriction add then remove restores routing (task 6.16).

# Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing

**Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**

(a) add a confirmed closure on an edge subset then remove it -> original route result exactly;
(b) closing an edge of the best route changes the route (avoids it) or yields no route;
(c) overlay_version strictly increases per add/remove and the resolver cache never serves a
    stale mask (with and without version bumps);
(d) the same through OverlayService + RepositoryContextProvider + RoutingService on the
    fixture graph.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.communication.events import EventBus
from app.disasters.base import IncidentContext
from app.models.domain import Incident, IncidentType, Restriction
from app.models.routing import LatLon
from app.routing.cost import load_weights
from app.routing.graph import Graph
from app.routing.restrictions import RestrictionResolver
from app.routing.search import dijkstra
from app.routing.service import InMemoryContextProvider, RoutingService
from app.services.overlays import (
    INCIDENTS_PK,
    INCIDENTS_TABLE,
    OverlayService,
    RepositoryContextProvider,
)
from strategies import graphs

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
INC = "inc-1"
PBT = settings(max_examples=100, deadline=None, derandomize=False,
               suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                      HealthCheck.function_scoped_fixture])


# ------------------------------------------------------------------------------ helpers
def _incident() -> Incident:
    return Incident(incident_id=INC, incident_type=IncidentType.FLOOD, source="test",
                    created_at=NOW, updated_at=NOW, simulation_flag=True)


def _ctx(graph: Graph, overlays, src: int) -> IncidentContext:
    return IncidentContext(
        incident=_incident(), now=NOW,
        origin=LatLon(latitude=float(graph.node_lat[src]), longitude=float(graph.node_lon[src])),
        overlays=tuple(overlays),
    )


def _closure(rid: str, edge_ids) -> Restriction:
    return Restriction(restriction_id=rid, incident_id=INC, kind="closure",
                       edge_ids=list(edge_ids), source_record_id="src-" + rid, simulated=True)


def _route(graph: Graph, mask: np.ndarray, s: int, t: int):
    p = dijkstra(graph, mask, s, t, lambda e: float(graph.edge_dist[e]))
    return None if p is None else (p.edge_ids, p.total_cost)


def _reachable(graph: Graph, mask: np.ndarray, s: int, t: int) -> bool:
    seen, q = {s}, deque([s])
    while q:
        n = q.popleft()
        if n == t:
            return True
        for e in graph.out_edges(n):
            m = int(graph.edge_to[int(e)])
            if mask[int(e)] and m not in seen:
                seen.add(m)
                q.append(m)
    return False


class _World:
    """InMemoryContextProvider + one shared resolver; mask/route as the pipeline computes them."""

    def __init__(self, graph: Graph, src: int, dst: int) -> None:
        self.graph, self.src, self.dst = graph, src, dst
        self.provider = InMemoryContextProvider()
        self.provider.add_incident(_incident())
        self.resolver = RestrictionResolver()

    def mask(self, *, bump: bool = True, resolver: RestrictionResolver | None = None) -> np.ndarray:
        data = self.provider.load(INC, NOW)
        res = (resolver or self.resolver).resolve(
            _ctx(self.graph, data.overlays, self.src), self.graph, [],
            overlay_version=data.overlay_version if bump else 0)
        return np.asarray(res.mask(self.graph))

    def fresh_mask(self) -> np.ndarray:
        return self.mask(resolver=RestrictionResolver())

    def version(self) -> int:
        return self.provider.load(INC, NOW).overlay_version

    def route(self):
        return _route(self.graph, self.mask(), self.src, self.dst)


@st.composite
def _scenario(draw, min_nodes=4, max_nodes=10):
    g = draw(graphs(min_nodes=min_nodes, max_nodes=max_nodes, connected=draw(st.booleans())))
    s = draw(st.integers(0, g.num_nodes - 1))
    t = draw(st.integers(0, g.num_nodes - 1).filter(lambda x: x != s))
    edges = draw(st.lists(st.sampled_from(list(g.edge_ids)), unique=True, max_size=len(g.edge_ids))
                 ) if g.num_edges else []
    return g, s, t, edges


# --------------------------------------------------------------------- (a) restore exactly
@PBT
@given(_scenario())
def test_add_then_remove_restores_route(sc):
    # Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing
    # **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
    g, s, t, edges = sc
    w = _World(g, s, t)
    original = w.route()
    w.provider.add_overlay(_closure("c1", edges))
    w.provider.remove_overlay(INC, "c1")
    assert w.route() == original  # same edge ids and same cost, exactly


@PBT
@given(_scenario(), st.lists(st.sampled_from(list("ab")), min_size=2, max_size=2, unique=True))
def test_remove_one_of_two_closures_equals_only_the_other(sc, order):
    # Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing
    # **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
    g, s, t, edges = sc
    half = len(edges) // 2
    sets = {"a": edges[:half], "b": edges[half:]}
    w = _World(g, s, t)
    for k in "ab":
        w.provider.add_overlay(_closure(k, sets[k]))
    w.provider.remove_overlay(INC, order[0])
    keep = order[1]
    ref = _World(g, s, t)
    ref.provider.add_overlay(_closure(keep, sets[keep]))
    assert w.route() == ref.route()
    w.provider.remove_overlay(INC, keep)
    assert w.route() == _route(g, np.ones(g.num_edges, dtype=bool), s, t)


# ---------------------------------------------------------- (b) best-route edge is avoided
@PBT
@given(_scenario(), st.data())
def test_closing_best_route_edge_changes_route_or_is_unreachable(sc, data):
    # Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing
    # **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
    g, s, t, _ = sc
    w = _World(g, s, t)
    base = w.route()
    if base is None or not base[0]:
        return
    closed = data.draw(st.lists(st.sampled_from(list(base[0])), unique=True, min_size=1))
    w.provider.add_overlay(_closure("c1", closed))
    mask = w.mask()
    after = _route(g, mask, s, t)
    if after is None:
        assert not _reachable(g, mask, s, t)  # truly no alternative
    else:
        assert not set(after[0]) & set(closed)
        assert after[0] != base[0]
        assert after[1] >= base[1] - 1e-9  # removing options never makes the route cheaper
    w.provider.remove_overlay(INC, "c1")
    assert w.route() == base


# ------------------------------------------------------- (c) versions and the resolver cache
@PBT
@given(_scenario(), st.lists(st.booleans(), min_size=1, max_size=6))
def test_overlay_version_increases_and_cache_never_stale(sc, ops):
    # Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing
    # **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
    g, s, t, edges = sc
    w = _World(g, s, t)            # shared resolver, keyed on overlay_version
    nobump = RestrictionResolver()  # same inputs but version pinned at 0 (fingerprint guard only)
    active: list[str] = []
    last = w.version()
    assert np.array_equal(w.mask(), w.fresh_mask())
    for i, add in enumerate(ops):
        if add or not active:
            rid = f"c{i}"
            w.provider.add_overlay(_closure(rid, edges[i::3] or edges[:1]))
            active.append(rid)
        else:
            w.provider.remove_overlay(INC, active.pop(0))
        v = w.version()
        assert v > last
        last = v
        fresh = w.fresh_mask()
        assert np.array_equal(w.mask(), fresh)                       # with version bumps
        assert np.array_equal(w.mask(bump=False, resolver=nobump), fresh)  # without bumps


def test_same_version_reuses_cache_and_new_version_recomputes():
    g = Graph.load(FIXTURE_ROOT)
    s = 0
    w = _World(g, s, 5)
    a = w.mask()
    w.provider.add_overlay(_closure("c1", g.edge_ids[:3]))
    b = w.mask()
    assert a.all() and (~b).sum() == 3
    w.provider.remove_overlay(INC, "c1")
    assert w.mask().all()
    assert w.resolver.resolve(_ctx(g, [], s), g, [], overlay_version=w.version()) is \
        w.resolver.resolve(_ctx(g, [], s), g, [], overlay_version=w.version())


# ---------------------------------------- (d) OverlayService + RepositoryContextProvider
@pytest.fixture(scope="module")
def graph() -> Graph:
    g = Graph.load(FIXTURE_ROOT)
    assert g.loaded
    return g


ORIGIN = LatLon(latitude=41.8708, longitude=-87.6505)  # fx-uic
DEST = LatLon(latitude=41.892, longitude=-87.6278)  # fx-sim-destination


def _stack(graph: Graph):
    repo = MemoryRepository()
    inc = _incident()
    repo.put(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": INC, **inc.model_dump(mode="json")})
    provider = RepositoryContextProvider(repo, clock=lambda: NOW, version_ttl=2.0)
    bus = EventBus(repo, InProcessPublisher(), clock=lambda: NOW)
    t = [NOW]

    def clock():
        t[0] += timedelta(seconds=1)
        return t[0]

    ovl = OverlayService(repo, bus, clock=clock, on_change=provider.invalidate)
    svc = RoutingService(lambda: graph, load_weights, provider, clock=lambda: NOW)
    return ovl, provider, svc


def _calc(svc: RoutingService):
    req = svc.validate({"incident_id": INC, "origin": ORIGIN.model_dump(),
                        "destination": DEST.model_dump(), "travel_mode": "walking",
                        "mobility_profile": "standard", "alternatives": 1})
    return svc.calculate(req)


def _summary(resp):
    r = resp.routes[0]
    return (resp.policy_outcome.value, resp.route_count,
            tuple(p.edge_id for p in r.edge_penalties) if resp.route_count else (),
            r.distance_meters, r.estimated_duration_seconds)


@PBT
@given(st.data())
def test_repository_backed_add_remove_restores_routing(graph, data):
    # Feature: safepath-evacuation-navigation, Property 3: Restriction add then remove restores routing
    # **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
    ovl, provider, svc = _stack(graph)
    base = _calc(svc)
    assert base.policy_outcome.value == "ROUTE_OPTIONS_AVAILABLE"
    base_edges = [p.edge_id for p in base.routes[0].edge_penalties]
    on_route = data.draw(st.lists(st.sampled_from(base_edges), unique=True, max_size=len(base_edges)))
    others = data.draw(st.lists(st.sampled_from(list(graph.edge_ids)), unique=True, max_size=25))
    closed = sorted(set(on_route) | set(others))

    v0 = ovl.overlay_version(INC)
    v1 = ovl.add_closure(INC, edge_ids=closed, source_record_id="src-c", simulated=True,
                         restriction_id="ovl-a")
    assert v1 > v0 and provider.load(INC, NOW).overlay_version == v1  # no stale cached version
    after = _calc(svc)
    if after.route_count == 0:
        assert after.policy_outcome.value == "NO_VERIFIED_ROUTE_AVAILABLE"
    else:
        new_edges = [p.edge_id for p in after.routes[0].edge_penalties]
        assert not set(new_edges) & set(closed)
        if set(on_route):
            assert new_edges != base_edges
    v2 = ovl.remove_closure(INC, "ovl-a")
    assert v2 > v1 and provider.load(INC, NOW).overlay_version == v2
    assert _summary(_calc(svc)) == _summary(base)


def test_closing_everything_yields_no_verified_route(graph):
    ovl, _, svc = _stack(graph)
    base = _calc(svc)
    ovl.add_closure(INC, edge_ids=list(graph.edge_ids), simulated=True, restriction_id="all")
    blocked = _calc(svc)
    assert blocked.policy_outcome.value == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert blocked.route_count == 0
    ovl.remove_closure(INC, "all")
    assert _summary(_calc(svc)) == _summary(base)
