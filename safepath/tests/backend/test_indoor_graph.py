"""Task 11.1: fictional SIMULATED Indoor_Graph (Req 21.1-21.3, 21.6, 12.5)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from app import geo
from app.models.graph import Graph as GraphModel
from app.routing import indoor
from app.routing.graph import Graph
from app.routing.search import dijkstra
from app.services.graph_artifact import read_artifact

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))

from scripts import make_fixture_graph as mfg  # noqa: E402


@pytest.fixture(scope="module")
def spec():
    return indoor.load_spec()


@pytest.fixture(scope="module")
def merged():
    model, _, _ = read_artifact(mfg.FIXTURE_ROOT)
    return indoor.merge_indoor(model)


@pytest.fixture(scope="module")
def rt():
    return indoor.merge_indoor_runtime(Graph.load(mfg.FIXTURE_ROOT))


def _indoor_ids(m):
    return {n.node_id for n in m.nodes if n.kind == "indoor"}


def test_structure(spec, merged):
    ind = [n for n in merged.nodes if n.kind == "indoor"]
    assert {n.level for n in ind} == {"L1", "L2", "L3"}
    stair = [e for e in merged.edges if e.transition_type == "stairwell"]
    assert {e.edge_id.split("-")[4] for e in stair} == {"A", "B", "C"}
    assert len(stair) == 3 * 2 * 2  # 3 stairwells x 2 floor pairs x both directions
    exits = [e for e in merged.edges if e.transition_type == "exit"]
    assert len(exits) == 4 and len({e.edge_id.split("-")[4] for e in exits}) == 2
    assert all(e.edge_kind == "level_transition" for e in stair + exits)
    # start positions on floors 2 and 3, exits only on ground floor
    assert {s["level"] for s in spec["start_positions"]} == {"L2", "L3"}
    assert {n.level for n in ind if "exit" in n.node_id} == {"L1"}


def test_everything_simulated_and_unknown_preserved(spec, merged):
    ind = _indoor_ids(merged)
    for e in merged.edges:
        if e.from_node in ind or e.to_node in ind:
            assert e.classification == "Simulated"
            assert e.elevation_start == geo.UNKNOWN and e.current_closure_status == "Unknown"
            assert e.bridge_flag == "Unknown" and e.mobility_accessibility == "Unknown"
    assert all(n.elevation_m == "Unknown" for n in merged.nodes if n.node_id in ind)
    assert "SIMULATED" in spec["name"] and "Not a real building" in spec["notice"]


def test_inside_extraction_area(merged):
    for n in merged.nodes:
        if n.kind == "indoor":
            assert geo.SOUTH <= n.lat <= geo.NORTH and geo.WEST <= n.lon <= geo.EAST


def test_indoor_outdoor_only_connect_through_exit_transitions(merged):
    ind = _indoor_ids(merged)
    for e in merged.edges:
        crossing = (e.from_node in ind) != (e.to_node in ind)
        assert crossing == (e.transition_type == "exit")
    # fixed ids not hardcoded: exits attach to the nearest outdoor node
    for e in merged.edges:
        if e.transition_type == "exit":
            out = e.to_node if e.from_node in ind else e.from_node
            assert not out.startswith("sim-indoor")


def test_merge_is_idempotent_and_leaves_original_untouched(merged):
    model, _, _ = read_artifact(mfg.FIXTURE_ROOT)
    assert not any(n.kind == "indoor" for n in model.nodes)
    assert indoor.merge_indoor(merged) is merged
    assert isinstance(merged, GraphModel)


def _route(rt, src, dst, closed_stairs=()):
    mask = np.ones(rt.num_edges, dtype=bool)
    for i, e in enumerate(rt.model.edges):
        if e.transition_type == "stairwell" and any(f"-stair-{s}-" in e.edge_id for s in closed_stairs):
            mask[i] = False
    return dijkstra(rt, mask, src, dst, lambda i: float(rt.edge_time[i])), mask


EXITS = ["sim-indoor-L1-exit-1", "sim-indoor-L1-exit-2"]


def test_route_from_floor_3_to_exit_via_mask(rt):
    p, mask = _route(rt, "sim-indoor-L3-zone-2", EXITS[1])
    assert p is not None and all(mask[i] for i in p.edge_indices)
    levels = [rt.model.nodes[i].level for i in p.node_indices]
    assert levels[0] == "L3" and levels[-1] == "L1" and "L2" in levels


def test_stairwell_closure_leaves_others_usable(rt):
    base, _ = _route(rt, "sim-indoor-L3-cor-W", EXITS[0])
    assert any("stair-A" in e for e in base.edge_ids)  # nearest stairwell used first
    for closed in (("A",), ("A", "B")):
        p, mask = _route(rt, "sim-indoor-L3-cor-W", EXITS[0], closed)
        assert p is not None
        assert not any(f"-stair-{s}-" in e for e in p.edge_ids for s in closed)
    p, _ = _route(rt, "sim-indoor-L3-cor-W", EXITS[0], ("A", "B", "C"))
    assert p is None


def test_geojson_layer_labels(rt, spec):
    layer = rt.layers[indoor.LAYER_NAME]
    roles = {f["properties"]["role"] for f in layer["features"]}
    assert {"floor", "corridor", "zone", "stairwell", "exit", "start_position"} <= roles
    for f in layer["features"]:
        assert f["properties"]["classification"] == "Simulated" and f["properties"]["simulated"]
        assert "SIMULATED" in f["properties"]["name"]
    assert sum(f["properties"]["role"] == "exit" for f in layer["features"]) == 2
