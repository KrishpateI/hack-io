"""Property test: tornado warning yields shelter-in-place and no outdoor edges (design.md Property 14).

# Feature: safepath-evacuation-navigation, Property 14: Tornado warning yields shelter-in-place and no outdoor edges

**Validates: Requirements 19.1, 19.5, 19.7**
"""
from __future__ import annotations

from types import SimpleNamespace

from hypothesis import HealthCheck, given, settings, strategies as st
from shapely.geometry import Point, shape

from app.disasters.base import IncidentContext
from app.disasters.tornado import TornadoModule
from app.models.domain import Incident, IncidentType, PolicyOutcome
from app.models.routing import LatLon, RouteRequest
from app.routing.cost import load_weights
from app.routing.service import InMemoryContextProvider, RoutingService

from strategies import NOW, graphs, incidents

SETTINGS = settings(max_examples=60, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def _centroid(incident: Incident) -> LatLon:
    c = shape(incident.geographic_boundary).centroid
    return LatLon(latitude=c.y, longitude=c.x)


@st.composite
def tornado_incidents(draw, status: str):
    inc = draw(incidents(IncidentType.TORNADO).filter(lambda i: i.geographic_boundary is not None))
    return inc.model_copy(update={"status": status})


@SETTINGS
@given(g=graphs(), inc=tornado_incidents("active"))
def test_active_tornado_shelters_in_place_with_no_outdoor_edges(g, inc):
    origin = _centroid(inc)
    ctx = IncidentContext(incident=inc, now=NOW, origin=origin)
    module = TornadoModule()

    # the module overrides with SHELTER_IN_PLACE for any origin inside the boundary
    override = module.policy_override(ctx)
    assert override is not None and override.outcome is PolicyOutcome.SHELTER_IN_PLACE

    # no outdoor street edge survives the route filter while the warning is active
    for e in g.model.edges:
        if e.edge_kind == "street":
            assert module.route_filter(e, ctx) is False

    # end to end: empty geometry, no routes
    object.__setattr__(g, "manifest", SimpleNamespace(version=g.version, built_at=NOW))
    provider = InMemoryContextProvider()
    provider.add_incident(inc)
    svc = RoutingService(lambda: g, load_weights, provider, clock=lambda: NOW)
    dst = LatLon(latitude=float(g.node_lat[0]), longitude=float(g.node_lon[0]))
    resp = svc.calculate(RouteRequest(
        incident_id=inc.incident_id, origin=origin, destination=dst,
        travel_mode="walking", mobility_profile="standard", alternatives=3))
    assert resp.policy_outcome is PolicyOutcome.SHELTER_IN_PLACE
    assert all(not r.geometry.coordinates for r in resp.routes)


@SETTINGS
@given(g=graphs(), inc=tornado_incidents("resolved"))
def test_inactive_tornado_applies_no_override_and_allows_outdoor_edges(g, inc):
    ctx = IncidentContext(incident=inc, now=NOW, origin=_centroid(inc))
    module = TornadoModule()
    assert module.policy_override(ctx) is None
    assert all(module.route_filter(e, ctx) for e in g.model.edges)
