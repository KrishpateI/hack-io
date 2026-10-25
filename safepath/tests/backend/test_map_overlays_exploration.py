"""Bug condition exploration: SIMULATED hazards and restrictions reach the map (map-legend-layers-fix, task 1).

Property 1 (design): for every seeded SIMULATED incident, with demo mode OFF (the deployed setting),
the public read-only ``GET /api/incidents/{incident_id}/map-overlays`` payload contains one feature per
scenario flood feature, per debris zone id (scenario or active area restriction), per SIMULATED
``temporary_perimeter`` notice polygon and per active edge restriction edge with known geometry.

These tests encode the EXPECTED behavior. On the unfixed code they FAIL (the route does not exist and
``/api/demo/*`` is 404 with demo mode off), which confirms the bug. They run against both
``MemoryRepository`` and ``DynamoRepository(FakeDynamoDB(...))``.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7**
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
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EventBus
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.services import demo_data as dd
from app.services.overlays import OverlayService, RepositoryContextProvider

from test_demo_service import FIXTURE_ROOT, GRAPH

FLOOD, QUAKE, FIRE, TORNADO = "flood_loop", "earthquake_debris", "fire_downtown", "tornado_shelter"
SEEDED = [FLOOD, QUAKE, FIRE, TORNADO]
SEED_GRAPH = Graph.load(FIXTURE_ROOT)
DEPTH = re.compile("depth", re.IGNORECASE)
REPOS = ["memory", "dynamo"]


# ------------------------------------------------------------------------------ helpers
def _make_repo(kind: str):
    if kind == "memory":
        return MemoryRepository()
    return DynamoRepository(FakeDynamoDB(page_size=50), table_prefix="mlx-")


def _build_app(repo):
    """A public app with demo mode OFF, wired like the deployed API (no demo service)."""
    os.environ.setdefault("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    app = create_app(Settings(demo_mode=False, demo_autoseed=False, profile="local"))
    app.state.repository = repo
    app.state.auth = DemoTokenAuthProvider("t-secret")
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    from app.auth.accounts import AccountService
    app.state.accounts = AccountService(repo)
    return app


def _seeded(kind: str, scenarios=SEEDED):
    repo = _make_repo(kind)
    dd.seed(repo, graph=SEED_GRAPH, scenarios=scenarios)
    app = _build_app(repo)
    return TestClient(app, raise_server_exceptions=False), app, repo


def _overlays(client, incident_id: str) -> dict[str, Any]:
    r = client.get(f"/api/incidents/{incident_id}/map-overlays")
    assert r.status_code == 200, (
        f"GET /api/incidents/{incident_id}/map-overlays -> {r.status_code}: {r.text[:200]}")
    return r.json()


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _features(body, *path) -> list[dict]:
    cur = body
    for p in path:
        cur = (cur or {}).get(p)
    assert isinstance(cur, dict) and cur.get("type") == "FeatureCollection", (path, cur)
    return cur["features"]


def _assert_simulated(features):
    for f in features:
        props = f.get("properties") or {}
        assert props.get("simulated") is True, props
        assert str(props.get("label", "")).startswith("SIMULATED"), props
        assert props.get("is_official") is not True, props


@pytest.fixture(scope="module", params=REPOS)
def seeded_env(request):
    """Read-only seeded state shared by the non-mutating tests of one repository kind."""
    return _seeded(request.param)


# --------------------------------------------------------------------- seeded incidents
def test_demo_routes_are_unavailable_with_demo_mode_off(seeded_env):
    """Context for the counterexamples: the only existing readers of scenario geometry are gated."""
    client, _, _ = seeded_env
    assert client.get("/api/demo/scenarios").status_code == 404


def test_flood_area_is_returned_for_sim_flood_loop(seeded_env):
    client, _, repo = seeded_env
    row = dd.get_scenario(repo, FLOOD)
    expected = row["resolved"]["flood_highlight"]
    expected_n = len(expected["features"]) if expected.get("type") == "FeatureCollection" else 1
    body = _overlays(client, "sim-flood-loop")
    flood = _features(body, "hazards", "flood")
    assert len(flood) == expected_n
    _assert_simulated(flood)
    assert not any(DEPTH.search(k) for k in _keys(body))


def test_debris_zones_are_returned_for_sim_earthquake_debris(seeded_env):
    client, _, repo = seeded_env
    zones = dd.get_scenario(repo, QUAKE)["resolved"]["debris_zones"]
    assert zones
    debris = _features(_overlays(client, "sim-earthquake-debris"), "hazards", "debris")
    ids = [f["properties"].get("zone_id") or f["properties"].get("restriction_id") for f in debris]
    assert sorted(ids) == sorted(z["zone_id"] for z in zones)
    _assert_simulated(debris)


def test_fire_perimeter_is_returned_for_sim_fire_downtown(seeded_env):
    client, _, repo = seeded_env
    perimeter_notices = [n for n in repo.query("notices", {"pk": "sim-fire-downtown"}, limit=100)
                         if n.get("notice_type") == "temporary_perimeter" and n.get("simulation_flag")
                         and n.get("geometry")]
    assert len(perimeter_notices) == 1
    fire = _features(_overlays(client, "sim-fire-downtown"), "hazards", "fire")
    assert len(fire) == 1
    assert fire[0]["properties"].get("notice_id") == perimeter_notices[0]["notice_id"]
    assert fire[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    _assert_simulated(fire)


def test_tornado_has_no_hazards_but_endpoint_answers(seeded_env):
    client, _, _ = seeded_env
    body = _overlays(client, "sim-tornado-shelter")
    for kind in ("flood", "debris", "fire"):
        assert _features(body, "hazards", kind) == []
    assert _features(body, "restrictions") == []


# ------------------------------------------------------------- mutating (fresh state each)
@pytest.mark.parametrize("kind", REPOS)
def test_closure_edges_drawn_after_reload(kind):
    """Apply the demo closure step, then read with a fresh client (a page reload)."""
    _, app, repo = _seeded(kind, [FLOOD])
    dd.apply_step(repo, FLOOD, "closure")
    closure = dd.get_scenario(repo, FLOOD)["resolved"]["closure"]
    known = [e for e in closure["edge_ids"] if e in set(GRAPH.edge_ids)]
    assert known
    fresh = TestClient(app, raise_server_exceptions=False)
    lines = _features(_overlays(fresh, "sim-flood-loop"), "restrictions")
    assert sorted(f["properties"]["edge_id"] for f in lines) == sorted(known)
    assert all(f["geometry"]["type"] == "LineString" for f in lines)
    assert all(f["properties"].get("restriction_id") == closure["restriction_id"] for f in lines)


@pytest.mark.parametrize("kind", REPOS)
def test_applied_debris_zones_are_marked_applied_to_routing(kind):
    client, _, repo = _seeded(kind, [QUAKE])
    dd.apply_step(repo, QUAKE, "debris")
    zones = dd.get_scenario(repo, QUAKE)["resolved"]["debris_zones"]
    debris = _features(_overlays(client, "sim-earthquake-debris"), "hazards", "debris")
    assert len(debris) == len(zones)  # one per zone id, no scenario duplicate
    assert all(f["properties"].get("applied_to_routing") is True for f in debris)
    _assert_simulated(debris)


# ------------------------------------------------------------------ generated variations
def _decimalize(obj):
    if isinstance(obj, float):
        return Decimal(repr(obj))
    if isinstance(obj, dict):
        return {k: _decimalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimalize(v) for v in obj]
    return obj


@pytest.fixture(scope="module", params=REPOS)
def quake_env(request):
    client, app, repo = _seeded(request.param, [QUAKE])
    row = repo.get(dd.DEMO_TABLE, {"pk": dd.DEMO_PK, "sk": QUAKE})
    return client, repo, row


@settings(max_examples=15, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(data=st.data())
def test_property_generated_scenario_rows_and_overlays(quake_env, data):
    """Hypothesis: random debris-zone subsets (Decimal coordinates, depth-like keys) and active/removed
    area restrictions -> one debris feature per zone id, applied flag by overlay, no depth keys."""
    client, repo, base_row = quake_env
    iid = base_row["incident_id"]
    zones = base_row["resolved"]["debris_zones"]
    n = len(zones)
    kept = data.draw(st.lists(st.sampled_from(range(n)), unique=True, max_size=n), label="kept_zones")
    applied = data.draw(st.lists(st.sampled_from(range(n)), unique=True, max_size=n), label="applied")
    removed = data.draw(st.lists(st.sampled_from(applied), unique=True, max_size=len(applied))
                        if applied else st.just([]), label="removed")
    use_decimal = data.draw(st.booleans(), label="decimal_coords")
    add_depth = data.draw(st.booleans(), label="depth_keys")

    new_zones = []
    for i in kept:
        z = dict(zones[i])
        if add_depth:
            z["water_depth_m"] = 1.5
        new_zones.append(_decimalize(z) if use_decimal else z)
    row = {**base_row, "resolved": {**base_row["resolved"], "debris_zones": new_zones}}
    repo.put(dd.DEMO_TABLE, row)

    repo.delete_where("overlays", {"pk": iid})
    ovl = OverlayService(repo, EventBus(repo, InProcessPublisher()))
    for i in applied:
        z = zones[i]
        ovl.add_closure(iid, restriction_id=z["zone_id"], geometry=z["geometry"], kind="impassable_polygon",
                        simulated=True, source_record_id="SIMULATED-DEMO-SOURCE")
    for i in removed:
        ovl.remove_closure(iid, zones[i]["zone_id"])

    active = {zones[i]["zone_id"] for i in applied if i not in removed}
    expected = {zones[i]["zone_id"] for i in kept} | active

    body = _overlays(client, iid)
    debris = _features(body, "hazards", "debris")
    ids = [f["properties"].get("zone_id") or f["properties"].get("restriction_id") for f in debris]
    assert sorted(ids) == sorted(expected)
    for f in debris:
        zid = f["properties"].get("zone_id") or f["properties"].get("restriction_id")
        assert f["properties"].get("applied_to_routing") is (zid in active)
    _assert_simulated(debris)
    assert not any(DEPTH.search(k) for k in _keys(body))
