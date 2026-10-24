"""Property test for Route_Cost decomposition.

# Feature: safepath-evacuation-navigation, Property 8: Route_Cost decomposes into non-negative recorded components
**Validates: Requirements 13.2, 13.5**
"""
import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.disasters.earthquake import EarthquakeModule
from app.disasters.fire import FireModule
from app.disasters.flood import FloodModule
from app.disasters.tornado import TornadoModule
from app.models.domain import IncidentType
from app.routing.cost import CostModel, load_weights
from app.routing.search import dijkstra
from strategies import graphs, incident_contexts

SETTINGS = settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large]
)
W = load_weights()


def module_for(itype: IncidentType):
    return {
        IncidentType.FLOOD: lambda: FloodModule(weights=W),
        IncidentType.EARTHQUAKE: EarthquakeModule,
        IncidentType.FIRE: lambda: FireModule(()),
        IncidentType.TORNADO: TornadoModule,
    }.get(itype, lambda: None)()


@SETTINGS
@given(st.data())
def test_route_cost_decomposes_into_nonnegative_components(data):
    g = data.draw(graphs(min_nodes=2, max_nodes=8))
    ctx = data.draw(incident_contexts(g))
    model = CostModel(g, W, ctx, module_for(ctx.incident.incident_type))

    for e in range(g.num_edges):
        c = model.components(e)
        assert c.travel >= 0
        assert all(v >= 0 and math.isfinite(v) for v in c.hazard.values())
        assert all(v >= 0 and math.isfinite(v) for v in c.uncertainty.values())
        expected = c.travel + sum(c.hazard.values()) + sum(c.uncertainty.values())
        assert model.cost(e) == pytest.approx(expected, rel=1e-9, abs=1e-9)
        assert model.cost(e) >= 0

        # the recorded per-edge penalty carries the same components
        p = c.to_edge_penalty()
        assert p.edge_id == g.edge_ids[e]
        assert p.travel + sum(p.hazard.values()) + sum(p.uncertainty.values()) == pytest.approx(
            model.cost(e), rel=1e-9, abs=1e-9)

    # recorded edge costs along a returned route sum to the route's total cost
    n = g.num_nodes
    src, dst = data.draw(st.integers(0, n - 1)), data.draw(st.integers(0, n - 1))
    path = dijkstra(g, model.step_free_mask(), src, dst, model.cost_fn(), route_filter=model.route_filter())
    if path is not None:
        pens = model.edge_penalties(path.edge_indices)
        assert len(pens) == len(path.edge_indices)
        total = sum(p.travel + sum(p.hazard.values()) + sum(p.uncertainty.values()) for p in pens)
        assert path.total_cost == pytest.approx(total, rel=1e-7, abs=1e-9)
