"""``GET /api/incidents/{incident_id}/map-overlays`` router tests (map-legend-layers-fix, task 3.3).

Exercised over HTTP (FastAPI TestClient, demo mode OFF, no auth token) against ``MemoryRepository``
and ``DynamoRepository(FakeDynamoDB(...))`` seeded with ``demo_data.seed`` on the fixture graph.
Includes hypothesis tests for design Property 1 (one feature per scenario flood feature, debris zone
id, perimeter notice, active area restriction and known restricted edge) and Property 7 (allow-listed
payload: no private, user or depth keys; SIMULATED items labeled and never official).

**Validates: Requirements 2.1, 2.3, 2.4, 2.5, 2.6, 2.7, 3.9, 3.10, 3.11**
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any

import pytest
from fake_dynamodb import FakeDynamoDB
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.adapters.aws.dynamo import DynamoRepository
from app.adapters.ports import RepositoryUnavailable
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EventBus
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.services import demo_data as dd
from app.services import map_overlays as mo
from app.services.overlays import INCIDENTS_PK, INCIDENTS_TABLE, OverlayService, RepositoryContextProvider

from test_demo_service import FIXTURE_ROOT, GRAPH

FLOOD, QUAKE, FIRE, TORNADO = "flood_loop", "earthquake_debris", "fire_downtown", "tornado_shelter"
IIDS = {FLOOD: "sim-flood-loop", QUAKE: "sim-earthquake-debris", FIRE: "sim-fire-downtown",
        TORNADO: "sim-tornado-shelter"}
SEED_GRAPH = Graph.load(FIXTURE_ROOT)
REPOS = ["memory", "dynamo"]
DEPTH = re.compile("depth", re.IGNORECASE)
PRIVATE = {"users", "user_ids", "steps", "route_before_edge_ids", "route_after_edge_ids",
           "route_with_debris_edge_ids", "building_properties", "moderator_user_id", "source_report_id",
           "user_id", "reporter_alias"}
TOP_KEYS = {"incident_id", "simulation_flag", "overlay_version", "generated_at", "disclaimer",
            "restrictions", "hazards", "limitations"}
PROP_KEYS = {"incident_id", "restriction_id", "edge_id", "kind", "simulated", "status",
             "applied_to_routing", "label", "zone_id", "notice_id", "notice_type", "classification",
             "disclaimer", "basis", "edge_count", "elevation_percentile"}


# ------------------------------------------------------------------------------ helpers
def _repo(kind: str):
    if kind == "memory":
        return MemoryRepository()
    return DynamoRepository(FakeDynamoDB(page_size=50), table_prefix="mor-")


def _app(repo, graph=GRAPH):
    os.environ.setdefault("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    app = create_app(Settings(demo_mode=False, demo_autoseed=False, profile="local"))
    app.state.repository = repo
    app.state.auth = DemoTokenAuthProvider("t-secret")
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = graph
    app.state.rate_limiter = InMemoryRateLimiter()
    return app


def _env(kind: str, scenarios, graph=GRAPH):
    repo = _repo(kind)
    dd.seed(repo, graph=SEED_GRAPH, scenarios=scenarios)
    return TestClient(_app(repo, graph), raise_server_exceptions=False), repo


def _get(client, iid: str) -> dict[str, Any]:
    r = client.get(f"/api/incidents/{iid}/map-overlays")
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert r.headers.get("cache-control") == "no-store"
    return r.json()


def _ovl(repo) -> OverlayService:
    return OverlayService(repo, EventBus(repo, InProcessPublisher()))


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _feats(body, kind: str | None = None) -> list[dict]:
    coll = body["restrictions"] if kind is None else body["hazards"][kind]
    assert coll["type"] == "FeatureCollection"
    return coll["features"]


def _zone_ids(features) -> list[str]:
    return sorted(f["properties"]["zone_id"] for f in features)


def _assert_allow_listed(body: dict[str, Any]) -> None:
    """Property 7: only allow-listed keys, nothing private, no depth, SIMULATED never official."""
    assert set(body) == TOP_KEYS
    assert set(body["hazards"]) == {"flood", "debris", "fire"}
    assert set(body["limitations"]) == {"missing_edge_geometry", "graph_loaded"}
    keys = set(_keys(body))
    assert not keys & PRIVATE, keys & PRIVATE
    assert not any(DEPTH.search(k) for k in keys)
    for coll in [body["restrictions"], *body["hazards"].values()]:
        for f in coll["features"]:
            assert set(f) == {"type", "geometry", "properties"}
            p = f["properties"]
            assert set(p) <= PROP_KEYS, set(p) - PROP_KEYS
            assert p.get("is_official") is not True
            if p.get("simulated") is True:
                assert p["classification"] == "Simulated"
                assert p["label"].startswith("SIMULATED")


@pytest.fixture(scope="module", params=REPOS)
def seeded(request):
    """Read-only seeded state shared by the non-mutating tests of one repository kind."""
    return _env(request.param, [FLOOD, QUAKE, FIRE, TORNADO])


# ------------------------------------------------------------------- seeded, read-only
def test_flood_area_public_with_demo_mode_off(seeded):
    client, repo = seeded
    assert client.get("/api/demo/scenarios").status_code == 404  # demo off, endpoint still public
    expected = dd.get_scenario(repo, FLOOD)["resolved"]["flood_highlight"]["features"]
    body = _get(client, IIDS[FLOOD])
    flood = _feats(body, "flood")
    assert len(flood) == len(expected) >= 1
    p = flood[0]["properties"]
    assert p["label"] == mo.LABEL_FLOOD and p["simulated"] is True and p["applied_to_routing"] is False
    assert body["disclaimer"] == mo.DISCLAIMER and body["simulation_flag"] is True
    assert _feats(body) == []  # inactive scenario closure is not drawn (D2b)
    _assert_allow_listed(body)


def test_earthquake_scenario_zones_before_debris_step(seeded):
    client, repo = seeded
    zones = dd.get_scenario(repo, QUAKE)["resolved"]["debris_zones"]
    debris = _feats(_get(client, IIDS[QUAKE]), "debris")
    assert _zone_ids(debris) == sorted(z["zone_id"] for z in zones)
    for f in debris:
        assert f["properties"]["label"] == mo.LABEL_SCENARIO_DEBRIS
        assert f["properties"]["applied_to_routing"] is False


def test_fire_perimeter_from_notice(seeded):
    client, repo = seeded
    body = _get(client, IIDS[FIRE])
    fire = _feats(body, "fire")
    assert len(fire) == 1
    p = fire[0]["properties"]
    assert p["label"] == mo.LABEL_FIRE_NOTICE and p["applied_to_routing"] is True
    assert p["notice_id"] == dd.get_scenario(repo, FIRE)["resolved"]["perimeter_notice_id"]
    assert fire[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    _assert_allow_listed(body)


def test_tornado_is_empty(seeded):
    client, _ = seeded
    body = _get(client, IIDS[TORNADO])
    assert _feats(body) == [] and all(_feats(body, k) == [] for k in ("flood", "debris", "fire"))
    assert body["limitations"] == {"missing_edge_geometry": 0, "graph_loaded": True}


def test_unknown_incident_is_404_envelope(seeded):
    client, _ = seeded
    r = client.get("/api/incidents/no-such-incident/map-overlays")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_path_param_length_validated(seeded):
    client, _ = seeded
    assert client.get(f"/api/incidents/{'x' * 65}/map-overlays").status_code == 400
    assert client.get(f"/api/incidents/{'x' * 64}/map-overlays").status_code == 404


def test_endpoint_is_public_and_read_only(seeded):
    client, _ = seeded
    r = client.get(f"/api/incidents/{IIDS[FLOOD]}/map-overlays", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 200  # no auth dependency; token is ignored
    assert client.post(f"/api/incidents/{IIDS[FLOOD]}/map-overlays").status_code == 405


# ------------------------------------------------------------------ mutating (fresh state)
@pytest.mark.parametrize("kind", REPOS)
def test_earthquake_after_debris_step_and_after_removal(kind):
    client, repo = _env(kind, [QUAKE])
    iid = IIDS[QUAKE]
    dd.apply_step(repo, QUAKE, "debris")
    zones = dd.get_scenario(repo, QUAKE)["resolved"]["debris_zones"]
    debris = _feats(_get(client, iid), "debris")
    assert _zone_ids(debris) == sorted(z["zone_id"] for z in zones)  # no duplicates
    assert all(f["properties"]["applied_to_routing"] is True for f in debris)

    _ovl(repo).remove_closure(iid, zones[0]["zone_id"])
    body = _get(client, iid)
    by_zone = {f["properties"]["zone_id"]: f["properties"] for f in _feats(body, "debris")}
    assert set(by_zone) == {z["zone_id"] for z in zones}
    assert by_zone[zones[0]["zone_id"]]["applied_to_routing"] is False
    assert by_zone[zones[0]["zone_id"]]["label"] == mo.LABEL_SCENARIO_DEBRIS
    assert all(by_zone[z["zone_id"]]["applied_to_routing"] is True for z in zones[1:])
    _assert_allow_listed(body)


@pytest.mark.parametrize("kind", REPOS)
def test_fire_fallback_to_scenario_perimeter(kind):
    client, repo = _env(kind, [FIRE])
    repo.delete_where("notices", {"pk": IIDS[FIRE]})
    fire = _feats(_get(client, IIDS[FIRE]), "fire")
    assert len(fire) == 1
    p = fire[0]["properties"]
    assert p["label"] == mo.LABEL_FIRE_SCENARIO and p["applied_to_routing"] is False
    assert "notice_id" not in p


@pytest.mark.parametrize("kind", REPOS)
def test_closure_edge_lines(kind):
    client, repo = _env(kind, [FLOOD])
    dd.apply_step(repo, FLOOD, "closure")
    closure = dd.get_scenario(repo, FLOOD)["resolved"]["closure"]
    body = _get(TestClient(client.app, raise_server_exceptions=False), IIDS[FLOOD])  # fresh client
    lines = _feats(body)
    assert sorted(f["properties"]["edge_id"] for f in lines) == sorted(closure["edge_ids"])
    for f in lines:
        assert f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) >= 2
        p = f["properties"]
        assert p["restriction_id"] == closure["restriction_id"] and p["label"] == mo.LABEL_EDGE_SIMULATED
        assert p["status"] == "active" and p["applied_to_routing"] is True
    assert body["overlay_version"] >= 1
    _assert_allow_listed(body)


@pytest.mark.parametrize("kind", REPOS)
def test_graph_not_loaded_still_returns_hazards(kind, tmp_path):
    unloaded = Graph.load(tmp_path)  # empty directory: no artifact
    assert not unloaded.loaded
    client, repo = _env(kind, [FLOOD, QUAKE], graph=unloaded)
    dd.apply_step(repo, FLOOD, "closure")
    dd.apply_step(repo, QUAKE, "debris")
    flood = _get(client, IIDS[FLOOD])
    assert _feats(flood) == [] and len(_feats(flood, "flood")) == 1
    assert flood["limitations"]["graph_loaded"] is False
    quake = _get(client, IIDS[QUAKE])
    assert quake["limitations"]["graph_loaded"] is False
    assert all(f["properties"]["applied_to_routing"] is True for f in _feats(quake, "debris"))
    assert _feats(quake, "debris")  # area restrictions still served


@pytest.mark.parametrize("kind", REPOS)
def test_non_simulated_incident_with_scenario_id_ignores_scenario_row(kind):
    client, repo = _env(kind, [QUAKE])
    iid = IIDS[QUAKE]
    row = repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": iid})
    assert row.get("scenario_id")
    repo.put(INCIDENTS_TABLE, {**row, "simulation_flag": False})
    body = _get(client, iid)
    assert body["simulation_flag"] is False
    assert all(_feats(body, k) == [] for k in ("flood", "debris", "fire"))


@pytest.mark.parametrize("kind", REPOS)
def test_non_simulated_overlay_never_leaks_source_report(kind):
    client, repo = _env(kind, [TORNADO])
    iid = IIDS[TORNADO]
    _ovl(repo).add_closure(iid, edge_ids=[GRAPH.edge_ids[0], "no-such-edge"], kind="closure",
                           source_report_id="report-private-1", simulated=False, restriction_id="r-real")
    body = _get(client, iid)
    lines = _feats(body)
    assert [f["properties"]["edge_id"] for f in lines] == [GRAPH.edge_ids[0]]
    assert lines[0]["properties"]["label"] == "Closed road segment (closure)"
    assert body["limitations"]["missing_edge_geometry"] == 1
    assert "report-private-1" not in str(body)
    _assert_allow_listed(body)


class _Unavailable:
    """Repository whose reads fail like a DynamoDB outage."""

    def __getattr__(self, name):
        def fail(*_a, **_kw):
            raise RepositoryUnavailable("simulated outage")
        return fail


def test_repository_unavailable_is_503_envelope():
    client = TestClient(_app(_Unavailable()), raise_server_exceptions=False)
    r = client.get(f"/api/incidents/{IIDS[FLOOD]}/map-overlays")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"


def test_openapi_names_the_response_schema():
    client = TestClient(_app(MemoryRepository()))
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/incidents/{incident_id}/map-overlays"]["get"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/MapOverlays")
    assert "security" not in op


# ----------------------------------------------------------------- property-based tests
def _ring(cx: float, cy: float, d: float) -> list[list[float]]:
    return [[cx, cy], [cx + d, cy], [cx + d, cy + d], [cx, cy + d], [cx, cy]]


def _decimalize(obj):
    if isinstance(obj, float):
        return Decimal(repr(obj))
    if isinstance(obj, dict):
        return {k: _decimalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimalize(v) for v in obj]
    return obj


polygons = st.builds(
    lambda cx, cy, d: {"type": "Polygon", "coordinates": [_ring(cx, cy, d)]},
    st.floats(-87.66, -87.60, allow_nan=False), st.floats(41.86, 41.90, allow_nan=False),
    st.floats(0.0005, 0.003, allow_nan=False))
junk = st.fixed_dictionaries({}, optional={
    # realistic depth-like values (DynamoDB cannot store subnormal floats such as 1e-206)
    "water_depth_m": st.sampled_from([0.25, 1.5, 3.0]), "Depth": st.just("deep"),
    "user_ids": st.just(["u-1"]), "source_report_id": st.just("rep-1"), "is_official": st.just(True),
    "building_properties": st.just({"levels": 3}), "moderator_user_id": st.just("mod-1")})
private_row = st.fixed_dictionaries({}, optional={
    "users": st.just([{"user_id": "u-1", "role": "Moderator"}]), "user_ids": st.just(["u-1", "u-2"]),
    "steps": st.just(["closure", "debris"]), "route_before_edge_ids": st.just(["e1"]),
    "route_after_edge_ids": st.just(["e2"]), "building_properties": st.just({"b1": {"levels": 4}}),
    "moderator_user_id": st.just("mod-1"), "max_depth_m": st.just(2.0)})


@st.composite
def scenario_states(draw):
    """A scenario row, active/removed overlays and perimeter notices for the fire incident."""
    flood = [{"type": "Feature", "geometry": draw(polygons),
              "properties": {"basis": "elevation", "edge_count": draw(st.integers(0, 50)), **draw(junk)}}
             for _ in range(draw(st.integers(0, 2)))]
    n_zones = draw(st.integers(0, 3))
    zones = [{"zone_id": f"zone-{i}", "geometry": draw(polygons), "label": "SIMULATED debris",
              "disclaimer": "SIMULATED", **draw(junk)} for i in range(n_zones)]
    perimeter = draw(st.one_of(st.none(), polygons))
    overlays = []
    for j in range(draw(st.integers(0, 4))):
        rid = draw(st.sampled_from([f"zone-{i}" for i in range(n_zones)] + [f"ov-{j}"]))
        if any(o["rid"] == rid for o in overlays):
            continue
        area = draw(st.booleans())
        overlays.append({
            "rid": rid, "area": area, "simulated": draw(st.booleans()), "removed": draw(st.booleans()),
            "geometry": draw(polygons) if area else None,
            "edges": [] if area else draw(st.lists(st.sampled_from(GRAPH.edge_ids[:40]), min_size=1,
                                                   max_size=3, unique=True)),
        })
    notices = [draw(polygons) for _ in range(draw(st.integers(0, 2)))]
    return {"flood": flood, "zones": zones, "perimeter": perimeter, "overlays": overlays,
            "notices": notices, "private": draw(private_row), "decimal": draw(st.booleans())}


@pytest.fixture(scope="module", params=REPOS)
def fire_env(request):
    client, repo = _env(request.param, [FIRE])
    row = repo.get(dd.DEMO_TABLE, {"pk": dd.DEMO_PK, "sk": FIRE})
    return client, repo, row


def _apply_state(repo, base_row, state) -> None:
    iid = base_row["incident_id"]
    resolved = {**base_row["resolved"],
                "flood_highlight": {"type": "FeatureCollection", "features": state["flood"]},
                "debris_zones": state["zones"],
                "perimeter": ({"type": "Feature", "geometry": state["perimeter"], "properties": {}}
                              if state["perimeter"] else None)}
    row = {**base_row, **state["private"], "resolved": resolved}
    repo.put(dd.DEMO_TABLE, _decimalize(row) if state["decimal"] else row)

    repo.delete_where("notices", {"pk": iid})
    for k, geom in enumerate(state["notices"]):
        repo.put("notices", {"pk": iid, "sk": f"notice-gen-{k}", "notice_id": f"notice-gen-{k}",
                             "incident_id": iid, "notice_type": "temporary_perimeter",
                             "text": "SIMULATED perimeter", "simulation_flag": True,
                             "geometry": _decimalize(geom) if state["decimal"] else geom,
                             "expires_at": None})

    repo.delete_where("overlays", {"pk": iid})
    ovl = _ovl(repo)
    for o in state["overlays"]:
        ovl.add_closure(iid, restriction_id=o["rid"], edge_ids=o["edges"], geometry=o["geometry"],
                        kind="impassable_polygon" if o["area"] else "closure", simulated=o["simulated"],
                        source_report_id="report-private-9", source_record_id="SIMULATED-DEMO-SOURCE")
        if o["removed"]:
            ovl.remove_closure(iid, o["rid"])


PBT = settings(max_examples=20, deadline=None,
               suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])


@PBT
@given(state=scenario_states())
def test_property_1_every_applicable_item_has_a_feature(fire_env, state):
    """Property 1: one feature per scenario flood feature, per debris zone id (scenario or active
    area restriction), per SIMULATED perimeter notice (else the scenario fallback) and per active
    restricted edge with known geometry.

    **Validates: Requirements 2.1, 2.3, 2.4, 2.5, 2.6, 2.7**
    """
    client, repo, base_row = fire_env
    _apply_state(repo, base_row, state)
    body = _get(client, base_row["incident_id"])

    active = [o for o in state["overlays"] if not o["removed"]]
    active_ids = {o["rid"] for o in active}
    active_areas = [o for o in active if o["area"]]
    assert len(_feats(body, "flood")) == len(state["flood"])
    assert _zone_ids(_feats(body, "debris")) == sorted(
        z["zone_id"] for z in state["zones"] if z["zone_id"] not in active_ids)
    fallback = 1 if (not state["notices"] and state["perimeter"]) else 0
    fire = _feats(body, "fire")
    assert len(fire) == len(state["notices"]) + len(active_areas) + fallback  # fire incident: areas -> fire
    assert sorted(f["properties"]["restriction_id"] for f in fire if "restriction_id" in f["properties"]) \
        == sorted(o["rid"] for o in active_areas)
    expected_edges = sorted((o["rid"], e) for o in active if not o["area"] for e in o["edges"])
    assert sorted((f["properties"]["restriction_id"], f["properties"]["edge_id"])
                  for f in _feats(body)) == expected_edges
    for f in fire:
        assert f["properties"]["applied_to_routing"] is ("notice_id" in f["properties"]
                                                         or "restriction_id" in f["properties"])


@PBT
@given(state=scenario_states())
def test_property_7_payload_is_allow_listed_and_never_official(fire_env, state):
    """Property 7: only allow-listed keys, no user ids, no source_report_id, no depth keys; every
    scenario or SIMULATED-notice feature is simulated, classified Simulated and not official.

    **Validates: Requirements 3.9, 3.3**
    """
    client, repo, base_row = fire_env
    _apply_state(repo, base_row, state)
    body = _get(client, base_row["incident_id"])
    _assert_allow_listed(body)
    for coll in ("flood", "debris", "fire"):
        for f in _feats(body, coll):
            p = f["properties"]
            if "restriction_id" not in p:  # scenario data or SIMULATED notice
                assert p["simulated"] is True and p["classification"] == "Simulated"
    for f in _feats(body):
        assert all(isinstance(c, float) for pt in f["geometry"]["coordinates"] for c in pt)
