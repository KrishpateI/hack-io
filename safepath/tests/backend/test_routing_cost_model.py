from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.disasters.base import IncidentContext, PenaltyParts
from app.models.domain import (
    HazardReport,
    Incident,
    IncidentType,
    MobilityProfile,
    TravelMode,
    VerificationStatus,
)
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import CostModel, load_weights
from app.routing.graph import Graph
from app.routing.search import astar, dijkstra
from app.geo import haversine

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
W = load_weights()
KNOWN = dict(
    infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
    tunnel_flag=False, elevation_confidence="High", mobility_accessibility="accessible",
)


def make_graph(specs):
    """specs: [(edge_id, from, to, meters, overrides)] on a line of nodes."""
    ids = sorted({x for s in specs for x in s[1:3]})
    ns = [Node(node_id=n, lat=41.88 + 0.001 * k, lon=-87.63, kind="intersection") for k, n in enumerate(ids)]
    es = [
        Edge(
            edge_id=i, from_node=f, to_node=t, geometry="", distance_meters=d,
            estimated_travel_time_seconds=d / 1.4, travel_mode=["walking"], source="osm",
            data_timestamp=TS, classification="Static", **{**KNOWN, **o},
        )
        for i, f, t, d, o in specs
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t"), {})


def ctx(mode=TravelMode.WALKING, profile=MobilityProfile.STANDARD, reports=()):
    inc = Incident(
        incident_id="i1", incident_type=IncidentType.FLOOD, source="t", created_at=TS, updated_at=TS
    )
    return IncidentContext(
        incident=inc, now=TS, origin=LatLon(latitude=41.88, longitude=-87.63),
        profile=profile, mode=mode, reports=tuple(reports),
    )


def report(rid, status, edges):
    return HazardReport(
        report_id=rid, incident_id="i1", category="x", latitude=41.88, longitude=-87.63,
        affected_edge_ids=edges, verification_status=status, created_at=TS, expires_at=TS,
    )


def test_known_edge_cost_is_scaled_travel_only():
    g = make_graph([("a", "n0", "n1", 100, {})])
    c = CostModel(g, W, ctx()).components(0)
    assert c.travel == pytest.approx(100 / 1.4)
    assert c.hazard == {} and c.uncertainty == {} and c.total == c.travel


def test_each_unknown_attribute_adds_positive_scaled_penalty():
    g = make_graph([("a", "n0", "n1", 200, {"bridge_flag": "Unknown", "tunnel_flag": "Unknown"})])
    c = CostModel(g, W, ctx()).components(0)
    assert c.uncertainty == {
        "unknown_bridge_flag": pytest.approx(8 * 2.0),
        "unknown_tunnel_flag": pytest.approx(8 * 2.0),
    }


def test_short_edges_use_d_min_scale_and_elevation_penalties():
    g = make_graph([
        ("low", "n0", "n1", 1, {"elevation_confidence": "Low"}),
        ("unk", "n1", "n2", 1, {"elevation_confidence": "Unknown"}),
    ])
    m = CostModel(g, W, ctx())
    assert m.components(0).uncertainty == {"elevation_low": pytest.approx(6 * 0.1)}
    assert m.components(1).uncertainty == {
        "unknown_elevation_confidence": pytest.approx(8 * 0.1),
        "elevation_unknown": pytest.approx(12 * 0.1),
    }


def test_unknown_mobility_only_penalized_for_step_free_and_inaccessible_excluded():
    g = make_graph([
        ("u", "n0", "n1", 100, {"mobility_accessibility": "Unknown"}),
        ("x", "n1", "n2", 100, {"mobility_accessibility": "inaccessible"}),
        ("ok", "n2", "n3", 100, {}),
    ])
    std = CostModel(g, W, ctx())
    assert std.components(0).uncertainty == {} and std.step_free_mask().all()
    sf = CostModel(g, W, ctx(profile=MobilityProfile.STEP_FREE))
    assert sf.components(0).uncertainty == {"profile_unknown": pytest.approx(15.0)}
    assert list(sf.step_free_mask()) == [True, False, True]
    assert [sf.route_filter()(i) for i in range(3)] == [True, False, True]
    assert sf.mode_factor == 1.4  # step-free uses at least the wheelchair factor


def test_reports_add_warnings_not_restrictions():
    g = make_graph([("a", "n0", "n1", 100, {}), ("b", "n1", "n2", 100, {})])
    rs = [
        report("r1", VerificationStatus.UNVERIFIED, ["a"]),
        report("r2", VerificationStatus.CORROBORATED, ["a"]),
        report("r3", VerificationStatus.DISPUTED, ["b"]),
        report("r4", VerificationStatus.RESOLVED, ["b"]),
    ]
    m = CostModel(g, W, ctx(reports=rs))
    a, b = m.components(0), m.components(1)
    assert a.hazard == {"report_unverified": 20.0, "report_corroborated": 40.0}
    assert a.report_ids == ("r1", "r2") and a.hazard_flags == ["report_unverified", "report_corroborated"]
    assert b.hazard == {} and b.uncertainty == {"disputed_report": 10.0} and b.report_ids == ("r3",)
    assert np.isfinite(m.cost(0)) and m.cost(0) > a.travel


class FakeModule:
    incident_type = IncidentType.FLOOD

    def edge_penalty(self, edge, c, graph):
        return PenaltyParts(hazard={"flood_tunnel": 50.0}, uncertainty={"m": 1.0})


def test_module_parts_recorded_and_edge_penalty_model():
    g = make_graph([("a", "n0", "n1", 100, {})])
    c = CostModel(g, W, ctx(), FakeModule()).components(0)
    assert c.hazard == {"flood_tunnel": 50.0} and c.uncertainty == {"m": 1.0}
    assert c.total == pytest.approx(c.travel + 51.0)
    ep = c.to_edge_penalty()
    assert ep.edge_id == "a" and ep.hazard == {"flood_tunnel": 50.0}


@pytest.mark.parametrize("mode", list(TravelMode))
def test_v_max_admissible_and_search_works(mode):
    g = make_graph([
        ("ab", "n0", "n1", 111, {"bridge_flag": "Unknown"}),
        ("bc", "n1", "n2", 111, {}),
        ("ac", "n0", "n2", 400, {}),
    ])
    m = CostModel(g, W, ctx(mode=mode))
    for i in range(g.num_edges):
        assert m.cost(i) >= g.edge_dist[i] / m.v_max - 1e-9
    p1 = astar(g, m.step_free_mask(), "n0", "n2", m.cost_fn(), v_max=m.v_max)
    p2 = dijkstra(g, m.step_free_mask(), "n0", "n2", m.cost_fn())
    assert p1.edge_ids == p2.edge_ids and p1.total_cost == pytest.approx(p2.total_cost)
    assert p1.total_cost == pytest.approx(sum(m.cost(i) for i in p1.edge_indices))


def test_v_max_value_from_weights():
    g = make_graph([("a", "n0", "n1", 100, {})])
    assert CostModel(g, W, ctx(mode=TravelMode.DRIVING)).v_max == 20.0
    assert CostModel(g, W, ctx(mode=TravelMode.WALKING)).v_max == 2.0
