"""Unit tests for ``app.services.map_overlays.build_map_overlays`` (map-legend-layers-fix, task 3.2).

The service is exercised directly (the router comes later) against ``MemoryRepository`` and
``DynamoRepository(FakeDynamoDB(...))`` seeded with ``demo_data.seed`` on the fixture graph.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.3, 3.8, 3.9, 3.10**
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from fake_dynamodb import FakeDynamoDB

from app.adapters import InProcessPublisher, MemoryRepository
from app.adapters.aws.dynamo import DynamoRepository
from app.communication.events import EventBus
from app.models.domain import Incident
from app.routing.graph import Graph
from app.services import demo_data as dd
from app.services import map_overlays as mo
from app.services.overlays import OverlayService, get_incident_row, strip_keys

from test_demo_service import FIXTURE_ROOT, GRAPH

FLOOD, QUAKE, FIRE, TORNADO = "flood_loop", "earthquake_debris", "fire_downtown", "tornado_shelter"
IIDS = {FLOOD: "sim-flood-loop", QUAKE: "sim-earthquake-debris", FIRE: "sim-fire-downtown",
        TORNADO: "sim-tornado-shelter"}
SEED_GRAPH = Graph.load(FIXTURE_ROOT)
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
DEPTH = re.compile("depth", re.IGNORECASE)
PRIVATE = {"users", "user_ids", "steps", "route_before_edge_ids", "route_after_edge_ids",
           "route_with_debris_edge_ids", "building_properties", "moderator_user_id", "source_report_id"}
REPOS = ["memory", "dynamo"]


class ReadOnlyRepo:
    """Delegates reads; any write method fails the test (the service must never write)."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, *a, **kw):
        return self._inner.get(*a, **kw)

    def query(self, *a, **kw):
        return self._inner.query(*a, **kw)

    def __getattr__(self, name):
        raise AssertionError(f"build_map_overlays must not call repo.{name}")


def _repo(kind: str):
    if kind == "memory":
        return MemoryRepository()
    return DynamoRepository(FakeDynamoDB(page_size=50), table_prefix="mso-")


def _seeded(kind: str, scenarios):
    repo = _repo(kind)
    dd.seed(repo, graph=SEED_GRAPH, scenarios=scenarios)
    return repo


def _incident(repo, iid: str) -> Incident:
    return Incident.model_validate(strip_keys(get_incident_row(repo, iid)))


def _build(repo, iid: str, graph=GRAPH) -> dict[str, Any]:
    return mo.build_map_overlays(ReadOnlyRepo(repo), graph, _incident(repo, iid), NOW)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


def _assert_safe(body: dict[str, Any]) -> None:
    keys = set(_keys(body))
    assert not keys & PRIVATE, keys & PRIVATE
    assert not any(DEPTH.search(k) for k in keys)
    assert not any(isinstance(v, Decimal) for v in _values(body))
    for coll in [body["restrictions"], *body["hazards"].values()]:
        for f in coll["features"]:
            p = f["properties"]
            assert p.get("is_official") is not True
            if p.get("simulated") is True:
                assert p["classification"] == "Simulated"
                assert p["label"].startswith("SIMULATED")


def _ids(features):
    return sorted(f["properties"].get("zone_id") for f in features)


@pytest.fixture(scope="module", params=REPOS)
def seeded(request):
    return _seeded(request.param, [FLOOD, QUAKE, FIRE, TORNADO])


# ------------------------------------------------------------------------- payload shape
def test_payload_shape_and_disclaimer(seeded):
    body = _build(seeded, IIDS[FLOOD])
    assert set(body) == {"incident_id", "simulation_flag", "overlay_version", "generated_at",
                         "disclaimer", "restrictions", "hazards", "limitations"}
    assert body["incident_id"] == IIDS[FLOOD]
    assert body["simulation_flag"] is True
    assert body["generated_at"] == NOW.isoformat()
    assert body["disclaimer"] == mo.DISCLAIMER
    assert set(body["hazards"]) == {"flood", "debris", "fire"}
    assert body["limitations"] == {"missing_edge_geometry": 0, "graph_loaded": True}
    _assert_safe(body)


def test_flood_highlight_becomes_one_merged_flood_feature(seeded):
    row = dd.get_scenario(seeded, FLOOD)
    expected = row["resolved"]["flood_highlight"]["features"]
    flood = _build(seeded, IIDS[FLOOD])["hazards"]["flood"]["features"]
    assert len(flood) == len(expected) >= 1
    p = flood[0]["properties"]
    assert p["label"] == mo.LABEL_FLOOD
    assert p["applied_to_routing"] is False and p["simulated"] is True
    assert set(p) <= {"incident_id", "label", "basis", "edge_count", "elevation_percentile",
                      "simulated", "classification", "applied_to_routing"}
    assert flood[0]["geometry"]["type"] in mo.AREA_TYPES


def test_earthquake_scenario_zones_not_applied(seeded):
    zones = dd.get_scenario(seeded, QUAKE)["resolved"]["debris_zones"]
    debris = _build(seeded, IIDS[QUAKE])["hazards"]["debris"]["features"]
    assert _ids(debris) == sorted(z["zone_id"] for z in zones)
    for f in debris:
        assert f["properties"]["label"] == mo.LABEL_SCENARIO_DEBRIS
        assert f["properties"]["applied_to_routing"] is False


def test_fire_perimeter_from_notice(seeded):
    fire = _build(seeded, IIDS[FIRE])["hazards"]["fire"]["features"]
    assert len(fire) == 1
    p = fire[0]["properties"]
    assert p["label"] == mo.LABEL_FIRE_NOTICE and p["applied_to_routing"] is True
    assert p["notice_id"] == dd.get_scenario(seeded, FIRE)["resolved"]["perimeter_notice_id"]


def test_tornado_has_no_hazards_or_restrictions(seeded):
    body = _build(seeded, IIDS[TORNADO])
    assert body["restrictions"]["features"] == []
    assert all(c["features"] == [] for c in body["hazards"].values())


def test_inactive_scenario_closure_is_not_drawn(seeded):
    assert mo.INCLUDE_PENDING_SCENARIO_CLOSURES is False
    assert _build(seeded, IIDS[FLOOD])["restrictions"]["features"] == []


def test_non_simulated_incident_ignores_scenario_row(seeded):
    inc = _incident(seeded, IIDS[QUAKE]).model_copy(update={"simulation_flag": False})
    body = mo.build_map_overlays(ReadOnlyRepo(seeded), GRAPH, inc, NOW)
    assert body["simulation_flag"] is False
    assert all(c["features"] == [] for c in body["hazards"].values())


# --------------------------------------------------------------------- mutating (fresh)
@pytest.mark.parametrize("kind", REPOS)
def test_debris_applied_then_one_removed(kind):
    repo = _seeded(kind, [QUAKE])
    iid = IIDS[QUAKE]
    dd.apply_step(repo, QUAKE, "debris")
    zones = dd.get_scenario(repo, QUAKE)["resolved"]["debris_zones"]
    debris = _build(repo, iid)["hazards"]["debris"]["features"]
    assert _ids(debris) == sorted(z["zone_id"] for z in zones)  # no duplicates
    assert all(f["properties"]["applied_to_routing"] is True for f in debris)
    assert all(f["properties"]["restriction_id"] == f["properties"]["zone_id"] for f in debris)

    OverlayService(repo, EventBus(repo, InProcessPublisher())).remove_closure(iid, zones[0]["zone_id"])
    body = _build(repo, iid)
    by_zone = {f["properties"]["zone_id"]: f["properties"] for f in body["hazards"]["debris"]["features"]}
    assert set(by_zone) == {z["zone_id"] for z in zones}
    assert by_zone[zones[0]["zone_id"]]["applied_to_routing"] is False
    assert by_zone[zones[0]["zone_id"]]["label"] == mo.LABEL_SCENARIO_DEBRIS
    assert body["overlay_version"] >= 2
    _assert_safe(body)


@pytest.mark.parametrize("kind", REPOS)
def test_closure_edge_lines_and_graph_not_loaded(kind):
    repo = _seeded(kind, [FLOOD])
    iid = IIDS[FLOOD]
    dd.apply_step(repo, FLOOD, "closure")
    closure = dd.get_scenario(repo, FLOOD)["resolved"]["closure"]
    lines = _build(repo, iid)["restrictions"]["features"]
    assert sorted(f["properties"]["edge_id"] for f in lines) == sorted(closure["edge_ids"])
    for f in lines:
        assert f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) >= 2
        p = f["properties"]
        assert p["restriction_id"] == closure["restriction_id"]
        assert p["label"] == mo.LABEL_EDGE_SIMULATED
        assert p["status"] == "active" and p["applied_to_routing"] is True and p["simulated"] is True

    no_graph = _build(repo, iid, graph=None)
    assert no_graph["restrictions"]["features"] == []
    assert no_graph["limitations"]["graph_loaded"] is False
    assert len(no_graph["hazards"]["flood"]["features"]) == 1  # hazards still delivered


@pytest.mark.parametrize("kind", REPOS)
def test_unknown_edges_counted_and_non_simulated_label(kind):
    repo = _seeded(kind, [TORNADO])
    iid = IIDS[TORNADO]
    known = GRAPH.edge_ids[0]
    OverlayService(repo, EventBus(repo, InProcessPublisher())).add_closure(
        iid, edge_ids=[known, "no-such-edge-1", "no-such-edge-2"], kind="closure",
        source_report_id="report-private-1", simulated=False, restriction_id="r-real")
    body = _build(repo, iid)
    lines = body["restrictions"]["features"]
    assert [f["properties"]["edge_id"] for f in lines] == [known]
    assert lines[0]["properties"]["label"] == "Closed road segment (closure)"
    assert lines[0]["properties"]["simulated"] is False
    assert body["limitations"]["missing_edge_geometry"] == 2
    _assert_safe(body)  # source_report_id never emitted


@pytest.mark.parametrize("kind", REPOS)
def test_fire_fallback_to_scenario_perimeter_when_notice_missing(kind):
    repo = _seeded(kind, [FIRE])
    iid = IIDS[FIRE]
    repo.delete_where("notices", {"pk": iid})
    fire = _build(repo, iid)["hazards"]["fire"]["features"]
    assert len(fire) == 1
    p = fire[0]["properties"]
    assert p["label"].startswith("SIMULATED") and p["applied_to_routing"] is False
    assert "notice_id" not in p


@pytest.mark.parametrize("kind", REPOS)
def test_sanitizes_private_depth_decimal_and_invalid_geometry(kind):
    repo = _seeded(kind, [QUAKE])
    row = repo.get(dd.DEMO_TABLE, {"pk": dd.DEMO_PK, "sk": QUAKE})
    zones = row["resolved"]["debris_zones"]
    z0 = dict(zones[0])
    ring = [[Decimal("-87.6300001234"), Decimal("41.88")], [Decimal("-87.629"), Decimal("41.88")],
            [Decimal("-87.629"), Decimal("41.881")], [Decimal("-87.6300001234"), Decimal("41.88")]]
    z0.update(geometry={"type": "Polygon", "coordinates": [ring]}, water_depth_m=Decimal("1.5"),
              building_properties={"levels": 3}, is_official=True)
    bad = {**dict(zones[1]), "zone_id": "bad-zone", "geometry": {"type": "Polygon",
                                                                 "coordinates": [[[500, 500]]]}}
    row = {**row, "resolved": {**row["resolved"], "debris_zones": [z0, bad]},
           "users": [{"user_id": "u1"}], "moderator_user_id": "mod-1"}
    repo.put(dd.DEMO_TABLE, row)

    body = _build(repo, IIDS[QUAKE])
    debris = body["hazards"]["debris"]["features"]
    assert _ids(debris) == [z0["zone_id"]]  # invalid geometry skipped
    first = debris[0]["geometry"]["coordinates"][0][0]
    assert first == [-87.63, 41.88] and all(isinstance(v, float) for v in first)
    assert set(debris[0]["properties"]) <= {"incident_id", "zone_id", "label", "disclaimer", "simulated",
                                            "classification", "applied_to_routing"}
    _assert_safe(body)
