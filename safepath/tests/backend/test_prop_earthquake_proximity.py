"""Property 12: earthquake proximity penalty depends on hazard association, not height class.

**Validates: Requirements 18.3, 18.4**
"""
from hypothesis import given, settings, strategies as st

from app.disasters.earthquake import Building, EarthquakeModule
from app.models.domain import IncidentType

from test_disaster_modules_eq_tornado_fire import (
    BUILDING_BOX,
    ctx,
    notice,
    penalties,
    report,
    zone,
)
from test_routing_search import six_node_graph

# a second building ~2 km from every edge of the fixture graph
FAR_BOX = {
    "type": "Polygon",
    "coordinates": [[[-87.64922, 41.86048], [-87.64918, 41.86048], [-87.64918, 41.86052],
                     [-87.64922, 41.86052], [-87.64922, 41.86048]]],
}
HEIGHTS = ("Low-rise", "Mid-rise", "High-rise", "Unknown")
WEIGHTS = {"w_highrise_proximity": 60, "proximity_meters": 50}
GRAPH = six_node_graph()
NEAR_EDGES = {"bef", "ber"}  # edges within 50 m of BUILDING_BOX


def _context(assoc):
    """assoc: dict building_key -> association kind (None, zone, notice, report)."""
    overlays, notices, reports = [], [], []
    for i, (key, kind) in enumerate(sorted(assoc.items())):
        box = BUILDING_BOX if key == "near" else FAR_BOX
        if kind == "zone":
            overlays.append(zone(restriction_id=f"z{i}", geometry=box))
        elif kind == "notice":
            notices.append(notice("road_closure", notice_id=f"n{i}", geometry=box))
        elif kind == "report":
            lat = 41.8805 if key == "near" else 41.8605
            reports.append(report("building_damage", "Unverified", rid=f"r{i}", lat=lat))
    return ctx(IncidentType.EARTHQUAKE, overlays=overlays, notices=notices, reports=reports)


def _module(near_h, far_h):
    return EarthquakeModule(
        [Building("near", BUILDING_BOX, height_class=near_h),
         Building("far", FAR_BOX, height_class=far_h)],
        weights=WEIGHTS,
    )


kinds = st.sampled_from([None, "zone", "notice", "report"])
assocs = st.fixed_dictionaries({"near": kinds, "far": kinds})
heights = st.sampled_from(HEIGHTS)


@settings(max_examples=60, deadline=None)
@given(assoc=assocs, h1=heights, h2=heights)
def test_penalty_positive_iff_edge_near_associated_building(assoc, h1, h2):
    p = penalties(_module(h1, h2), _context(assoc), GRAPH)
    positive = {e for e, v in p.items() if v > 0}
    # the far building is beyond 50 m of every edge, so only "near" can matter
    assert positive == (NEAR_EDGES if assoc["near"] else set())
    assert all(v >= 0 for v in p.values())


@settings(max_examples=60, deadline=None)
@given(assoc=assocs, h1=heights, h2=heights, g1=heights, g2=heights)
def test_height_class_does_not_change_penalties(assoc, h1, h2, g1, g2):
    c = _context(assoc)
    assert penalties(_module(h1, h2), c, GRAPH) == penalties(_module(g1, g2), c, GRAPH)
