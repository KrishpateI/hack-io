"""Property 13: Fire smoke penalty applies within 100 m of unconfirmed fire reports.

**Validates: Requirements 20.3**

For any edge and set of Unverified/Corroborated/Disputed fire/smoke reports, the fire hazard
penalty is > 0 when the edge is within 100 m of at least one report, and 0 otherwise.
The oracle uses an independent local equirectangular distance (point to segment).
"""
import math

from hypothesis import given, settings, strategies as st

from app.disasters.fire import FireModule
from test_disaster_modules_eq_tornado_fire import INC, NOW, ctx, report
from test_routing_search import six_node_graph
from app.models.domain import IncidentType

M_LAT = 111_195.0
LAT0 = 41.8805
M_LON = M_LAT * math.cos(math.radians(LAT0))
RADIUS = 100.0
MARGIN = 4.0  # skip reports whose distance is ambiguous at the 100 m boundary

STATUSES = ("Unverified", "Corroborated", "Disputed")


def _xy(lat, lon):
    return (lon * M_LON, lat * M_LAT)


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _edge_dist(g, edge, lat, lon):
    n = {x.node_id: x for x in g.model.nodes}
    return _seg_dist(_xy(lat, lon), _xy(n[edge.from_node].lat, n[edge.from_node].lon),
                     _xy(n[edge.to_node].lat, n[edge.to_node].lon))


report_st = st.tuples(
    st.floats(41.8785, 41.8825), st.floats(-87.6520, -87.6460), st.sampled_from(STATUSES),
    st.sampled_from(["smoke", "fire", "fire/smoke"]),
)


@settings(max_examples=100, deadline=None)
@given(st.lists(report_st, min_size=0, max_size=4))
def test_smoke_penalty_iff_within_radius(specs):
    g = six_node_graph()
    g_edges = list(g.model.edges)
    dists = {e.edge_id: [_edge_dist(g, e, la, lo) for la, lo, _, _ in specs] for e in g_edges}
    # avoid ambiguous boundary cases
    if any(abs(d - RADIUS) < MARGIN for ds in dists.values() for d in ds):
        return
    reports = [report(cat, status, rid=f"r{i}", lat=la, lon=lo)
               for i, (la, lo, status, cat) in enumerate(specs)]
    m = FireModule(weights={"w_smoke_report": 90, "radius_meters": RADIUS})
    c = ctx(IncidentType.FIRE, reports=reports)
    for e in g_edges:
        hazard = m.edge_penalty(e, c, g).hazard_total
        if any(d <= RADIUS for d in dists[e.edge_id]):
            assert hazard > 0, (e.edge_id, specs)
        else:
            assert hazard == 0, (e.edge_id, specs)
