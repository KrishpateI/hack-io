"""Preservation tests for the map-legend-layers fix (Property 2 / design Properties 5 and 7 scope).

Observation-first: every frozen value below was recorded on the UNFIXED code (seeded SIMULATED
demo scenarios on the SIMULATED fixture graph, demo mode off) and must stay the same after the
fix, which only adds a read-only ``GET /api/incidents/{id}/map-overlays`` endpoint and moves the
edge-geometry helper of ``graph_edges.py``.

Covered:
- Route responses (route types, edge sequence, distances, outcome) for each seeded scenario and
  for generated origin/destination pairs equal a control app. Once the map-overlays endpoint
  exists (anything but 404) the routes must also be unchanged after interleaved endpoint calls
  and the repository must be byte-identical before/after those calls; while the endpoint
  returns 404 that part is a no-op.
- Demo console path: the ``closure`` and ``add_debris`` steps write the same overlays,
  overlay_meta versions and ``restriction_*`` events as today.
- ``/api/incidents``, ``/api/incidents/{id}``, ``/api/notices``, ``/api/events``,
  ``/api/layers/*`` responses for seeded data are unchanged.
- ``/api/graph/edges/near`` returns the same edge geometry (guards the helper move).

Runs against ``MemoryRepository`` and ``DynamoRepository(FakeDynamoDB(...))``.

Feature: map-legend-layers-fix, Property 2 (Preservation) / design Property 5.
**Validates: Requirements 3.1, 3.3, 3.4, 3.7, 3.8, 3.9, 3.10, 3.11**
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fake_dynamodb import FakeDynamoDB
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository, Role
from app.adapters.aws.dynamo import DynamoRepository
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EventBus
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph, decode_geometry
from app.services import demo_data as dd
from app.services.overlays import RepositoryContextProvider

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
GRAPH = Graph.load(FIXTURE_ROOT)
# Close to wall-clock time: the DynamoDB adapter hides rows whose TTL has passed (events).
NOW = datetime.now(timezone.utc).replace(microsecond=0)
KINDS = ["memory", "dynamo"]

FLOOD, QUAKE, FIRE, TORNADO = "sim-flood-loop", "sim-earthquake-debris", "sim-fire-downtown", "sim-tornado-shelter"
SEEDED = ["sim-blocked-stairwell", QUAKE, FIRE, FLOOD, TORNADO]
ROUTE_INCIDENTS = [FLOOD, QUAKE, FIRE, TORNADO]
SCENARIO_OF = {FLOOD: "flood_loop", QUAKE: "earthquake_debris", FIRE: "fire_downtown", TORNADO: "tornado_shelter"}
# The tornado scenario has no destination; a fixed SIMULATED point inside the fixture graph.
FALLBACK_DESTINATION = {"latitude": 41.8800, "longitude": -87.6330}


# ------------------------------------------------------------------------------- environment
@dataclass
class Env:
    client: TestClient
    app: Any
    repo: Any
    fake: FakeDynamoDB | None
    provider: RepositoryContextProvider


def build(kind: str) -> Env:
    fake = FakeDynamoDB() if kind == "dynamo" else None
    repo = DynamoRepository(fake, table_prefix="pres-") if fake is not None else MemoryRepository()
    app = create_app(Settings())  # demo mode off (deployed setting)
    app.state.repository = repo
    app.state.auth = DemoTokenAuthProvider("t-secret", roles={"mod": Role.MODERATOR})
    app.state.audit = AuditWriter(repo)
    app.state.publisher = InProcessPublisher()
    app.state.event_bus = EventBus(repo, app.state.publisher, clock=lambda: NOW)
    provider = RepositoryContextProvider(repo)
    app.state.routing_context_provider = provider
    app.state.routing_service = None
    app.state.graph = GRAPH
    dd.seed(repo, graph=GRAPH, now=NOW, bus=app.state.event_bus, on_change=provider.invalidate)
    return Env(TestClient(app, raise_server_exceptions=False), app, repo, fake, provider)


@pytest.fixture(scope="module", params=KINDS)
def pair(request):
    """(env under test, control env) seeded identically, never mutated by the tests using it."""
    return build(request.param), build(request.param)


@pytest.fixture(params=KINDS)
def fresh(request):
    return build(request.param)


# ------------------------------------------------------------------------------- helpers
_ID = re.compile(r"\b(ntc|evt)_[0-9A-Z]{26}\b")
_TS_KEYS = {"created_at", "updated_at", "seeded_at", "calculated_at", "removed_at", "ttl",
            "effective_at", "published_at", "retrieved_at", "expires_at"}


def normalize(obj: Any) -> Any:
    """Replace generated ids (numbered per prefix by order of first appearance) and wall-clock
    timestamps."""
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}

    def sub(m: re.Match) -> str:
        if m.group(0) not in seen:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            seen[m.group(0)] = f"<{m.group(1)}-{counts[m.group(1)]}>"
        return seen[m.group(0)]

    def walk(o: Any, key: str | None = None) -> Any:
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if key in _TS_KEYS and o is not None:
            return "<ts>"
        if isinstance(o, str):
            return _ID.sub(sub, o)
        return o

    return walk(obj)


def digest(obj: Any) -> str:
    raw = json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snapshot(env: Env) -> bytes:
    """Canonical bytes of every stored row (MemoryRepository or the fake DynamoDB tables)."""
    tables = env.fake.tables if env.fake is not None else env.repo._data
    out = {t: sorted(json.dumps([list(k) if isinstance(k, tuple) else k, v], sort_keys=True, default=repr)
                     for k, v in rows.items()) for t, rows in tables.items()}
    return json.dumps(out, sort_keys=True).encode("utf-8")


def overlays_endpoint_exists(client: TestClient) -> bool:
    return client.get(f"/api/incidents/{FLOOD}/map-overlays").status_code != 404


def route_body(env: Env, iid: str, origin=None, destination=None, alternatives=None) -> dict:
    row = dd.get_scenario(env.repo, SCENARIO_OF[iid])
    rr = row["route_request"]
    o = origin or row["origin"]
    d = destination or row["destination"] or FALLBACK_DESTINATION
    return {"incident_id": iid,
            "origin": {"latitude": o["latitude"], "longitude": o["longitude"]},
            "destination": {"latitude": d["latitude"], "longitude": d["longitude"]},
            "travel_mode": rr["travel_mode"], "mobility_profile": rr["mobility_profile"],
            "alternatives": alternatives if alternatives is not None else rr["alternatives"]}


def calculate(client: TestClient, body: dict) -> dict:
    r = client.post("/api/routes/calculate", json=body)
    assert r.status_code == 200, r.text
    return norm_route(r.json())


def norm_route(resp: dict) -> dict:
    """Drop calculation timestamps; route ids are random per calculation, so replace them (also
    inside explanation text) by the route position."""
    resp = copy.deepcopy(resp)
    resp.pop("calculated_at", None)
    ids = {r["route_id"]: f"route-{i}" for i, r in enumerate(resp.get("routes", []))}
    for r in resp.get("routes", []):
        r.pop("calculated_at", None)
    raw = json.dumps(resp)
    raw = re.sub(r"\brt-[0-9a-f]{12}\b", lambda m: ids.get(m.group(0), "route-other"), raw)
    return normalize(json.loads(raw))


def summary(resp: dict) -> tuple:
    return (resp["policy_outcome"],
            [(r["route_type"], [p["edge_id"] for p in r["edge_penalties"]], round(r["distance_meters"], 2))
             for r in resp["routes"]])


def interleave_overlay_calls(env: Env, iids: list[str]) -> None:
    """Call the map-overlays endpoint (when it exists) and assert the store is untouched."""
    if not overlays_endpoint_exists(env.client):
        return  # endpoint not implemented yet (unfixed code): nothing to interleave
    before = snapshot(env)
    for iid in iids:
        r = env.client.get(f"/api/incidents/{iid}/map-overlays")
        assert r.status_code == 200, (iid, r.status_code, r.text)
    assert snapshot(env) == before, "map-overlays must never write to the repository"


# ------------------------------------------------------------------- frozen observations
# (policy_outcome, [(route_type, edge sequence, distance_m rounded to 2)]) per seeded scenario.
GOLDEN_ROUTES = {
    FLOOD: ("ROUTE_OPTIONS_AVAILABLE", [
        ("disaster-adjusted", ["fx-sim-e175", "fx-sim-e161", "fx-sim-e056", "fx-sim-e058"], 1030.94),
        ("alternate", ["fx-sim-e175", "fx-sim-e068", "fx-sim-e070", "fx-sim-e165"], 1030.91)]),
    QUAKE: ("ROUTE_OPTIONS_AVAILABLE", [
        ("disaster-adjusted", ["fx-sim-e133", "fx-sim-e031", "fx-sim-e117"], 920.98)]),
    FIRE: ("ROUTE_OPTIONS_AVAILABLE", [("disaster-adjusted", ["fx-sim-e117"], 277.99)]),
    TORNADO: ("SHELTER_IN_PLACE", [("policy", [], 0.0)]),
}
# sha256 of the normalized (ids and timestamps replaced) JSON responses for the seeded data.
GOLDEN_DIGESTS = {
    "incidents": "ec655fd78f3b35a8179b43946d6dae9ff823b30050458895d156607da14be846",
    "incident:sim-blocked-stairwell": "55cfa5065ff115d8d4a75ac27a91b3a780e6495d58138b1dec3fc476366c0e8d",
    "notices:sim-blocked-stairwell": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "events:sim-blocked-stairwell": "22045ce8dad572ef31362fdd22ad20422c407d64a57b15b2217aa9dc8ee162c2",
    "incident:sim-earthquake-debris": "1033a723ed0286ad2b4484746bfe73accd3c531979f74bbb07451439a5871b6d",
    "notices:sim-earthquake-debris": "28e1ab33e97bf693592060136c4d6149d298dfc13c0ad600f1b61384d6fdbe78",
    "events:sim-earthquake-debris": "5f057cd8f55b4dc74c2d2d7ed1ae66ceacd98c9f25b54b4efe144e65602f2544",
    "incident:sim-fire-downtown": "eeef032a66c1d53720bb67b0240aab7616c989cab08a2f5dd17c4c1e85341a27",
    "notices:sim-fire-downtown": "b2fd2f5ed51bf9e0f19d322775738e77e07063bb72e2f3f693f1f1798b9dc3d5",
    "events:sim-fire-downtown": "724b3ee0c8154c5de5f173a2c54190c597f4e855ada0f52b737e53ecc0ee76d0",
    "incident:sim-flood-loop": "9b667b39a6d8670f08b98de6d69eba5f79159eb3754fed656d12625b4cbb0ded",
    "notices:sim-flood-loop": "b9f261de1a1138235e41c3ace502c520b386764ebf1fb634d774a4c14d4b1286",
    "events:sim-flood-loop": "d37eb0e546de4ead84a9f7e2b6be1489ab5a11f5984be4155366d3c732b82651",
    "incident:sim-tornado-shelter": "42266522d2365f6d206204fe583a037ab02f9f246cea7ab3e7358bb32875d97f",
    "notices:sim-tornado-shelter": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "events:sim-tornado-shelter": "6f993d1b96416cc56595b6088dfff4d4e670c8987b56f8693ba61097bcdf9c4b",
}


# ======================================================================= route preservation
def test_seeded_scenario_routes_match_golden_and_control(pair):
    env, control = pair
    for iid in ROUTE_INCIDENTS:
        body = route_body(env, iid)
        got = calculate(env.client, body)
        assert got == calculate(control.client, body), iid
        assert summary(got) == GOLDEN_ROUTES[iid], iid
        assert all(r["label"] == "algorithm-generated" for r in got["routes"])
        interleave_overlay_calls(env, [iid, FLOOD, QUAKE, FIRE, TORNADO])
        assert calculate(env.client, body) == got, f"route changed after map-overlays calls ({iid})"


_NODES = st.integers(0, len(GRAPH.node_lat) - 1)
_JITTER = st.floats(-0.0003, 0.0003, allow_nan=False)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(iid=st.sampled_from(ROUTE_INCIDENTS), o=_NODES, d=_NODES, jo=st.tuples(_JITTER, _JITTER),
       jd=st.tuples(_JITTER, _JITTER), alternatives=st.integers(1, 2), probe=st.booleans())
def test_generated_routes_match_control_and_survive_overlay_calls(pair, iid, o, d, jo, jd, alternatives, probe):
    env, control = pair
    origin = {"latitude": float(GRAPH.node_lat[o]) + jo[0], "longitude": float(GRAPH.node_lon[o]) + jo[1]}
    dest = {"latitude": float(GRAPH.node_lat[d]) + jd[0], "longitude": float(GRAPH.node_lon[d]) + jd[1]}
    body = route_body(env, iid, origin, dest, alternatives)
    got = calculate(env.client, body)
    assert got == calculate(control.client, body)
    if probe:
        interleave_overlay_calls(env, [iid])
        assert calculate(env.client, body) == got


# ======================================================================= demo console path
def _overlay_rows(env: Env, iid: str) -> list[dict]:
    rows = env.repo.query("overlays", {"pk": iid})
    return sorted((normalize(r) for r in rows), key=lambda r: r["restriction_id"])


def _events(env: Env, iid: str) -> list[tuple[str, dict]]:
    r = env.client.get("/api/events", params={"incident_id": iid})
    assert r.status_code == 200
    return [(e["type"], e["payload"]) for e in normalize(r.json())["events"]]


def test_demo_closure_and_debris_steps_write_the_same_overlays_and_events(fresh):
    env = fresh
    published: list[str] = []
    for iid in (FLOOD, QUAKE):
        env.app.state.publisher.subscribe(iid, lambda e: published.append(e.type))
    bus, inv = env.app.state.event_bus, env.provider.invalidate
    flood = dd.apply_step(env.repo, "flood_loop", "closure", now=NOW, bus=bus, on_change=inv)
    quake = dd.apply_step(env.repo, "earthquake_debris", "debris", now=NOW, bus=bus, on_change=inv)
    assert flood == {"step": "closure", "kind": "add_closure", "applied": True, "overlay_versions": [2],
                     "simulated": True}
    assert quake == {"step": "debris", "kind": "add_debris", "applied": True, "overlay_versions": [2, 3],
                     "simulated": True}
    assert published == ["restriction_added"] * 3

    common = {"source_report_id": None, "source_record_id": "SIMULATED-DEMO-SOURCE", "simulated": True,
              "active": True, "status": "active", "created_at": "<ts>", "removed_at": None}
    assert _overlay_rows(env, FLOOD) == [{
        **common, "restriction_id": "sim-flood-closure-1", "incident_id": FLOOD, "kind": "closure",
        "edge_ids": ["fx-sim-e056"], "geometry": None, "pk": FLOOD, "sk": "sim-flood-closure-1"}]
    zones = dd.get_scenario(env.repo, "earthquake_debris")["resolved"]["debris_zones"]
    rows = _overlay_rows(env, QUAKE)
    assert [r["restriction_id"] for r in rows] == ["sim-eq-debris-1", "sim-eq-debris-2"]
    for row, zone in zip(rows, sorted(zones, key=lambda z: z["zone_id"])):
        geometry = json.loads(json.dumps(row.pop("geometry"), default=float))
        assert geometry == zone["geometry"] and geometry["type"] == "Polygon"
        assert row == {**common, "restriction_id": zone["zone_id"], "incident_id": QUAKE,
                       "kind": "impassable_polygon", "edge_ids": [], "pk": QUAKE, "sk": zone["zone_id"]}
    for iid, version in ((FLOOD, 2), (QUAKE, 3)):
        meta = env.repo.get("overlay_meta", {"pk": iid, "sk": "meta"})
        assert int(meta["overlay_version"]) == version

    created = lambda t: ("incident_created", {"incident_type": t, "simulation_flag": True})
    dest_notice = ("notice_published", {"notice_id": "<ntc-1>", "notice_type": "incident_destination",
                                        "overlay_version": 1, "simulation_flag": True})
    assert _events(env, FLOOD) == [created("flood"), dest_notice, ("restriction_added", {
        "restriction_id": "sim-flood-closure-1", "overlay_version": 2, "kind": "closure",
        "edge_ids": ["fx-sim-e056"], "simulated": True})]
    assert _events(env, QUAKE) == [created("earthquake"), dest_notice] + [
        ("restriction_added", {"restriction_id": f"sim-eq-debris-{i}", "overlay_version": i + 1,
                               "kind": "impassable_polygon", "edge_ids": [], "simulated": True})
        for i in (1, 2)]

    # the routes still react to the applied steps exactly as before
    flood_row = dd.get_scenario(env.repo, "flood_loop")
    after = calculate(env.client, route_body(env, FLOOD))
    assert [p["edge_id"] for p in after["routes"][0]["edge_penalties"]] == \
        flood_row["resolved"]["closure"]["route_after_edge_ids"]
    quake_row = dd.get_scenario(env.repo, "earthquake_debris")
    with_debris = calculate(env.client, route_body(env, QUAKE))
    assert [p["edge_id"] for p in with_debris["routes"][0]["edge_penalties"]] == \
        quake_row["resolved"]["route_with_debris_edge_ids"]
    interleave_overlay_calls(env, [FLOOD, QUAKE])
    assert calculate(env.client, route_body(env, FLOOD)) == after
    assert calculate(env.client, route_body(env, QUAKE)) == with_debris


# ======================================================================= existing endpoints
def _public_responses(client: TestClient) -> dict[str, Any]:
    out: dict[str, Any] = {"incidents": client.get("/api/incidents").json()}
    for iid in SEEDED:
        out[f"incident:{iid}"] = client.get(f"/api/incidents/{iid}").json()
        out[f"notices:{iid}"] = client.get("/api/notices", params={"incident_id": iid}).json()
        out[f"events:{iid}"] = client.get("/api/events", params={"incident_id": iid}).json()
    return out


def test_existing_public_endpoints_unchanged_for_seeded_data(fresh):
    env = fresh
    responses = _public_responses(env.client)
    assert [i["incident_id"] for i in responses["incidents"]] == SEEDED
    assert all(i["simulation_flag"] is True and "SIMULATED" in i["source"] for i in responses["incidents"])
    tornado = responses[f"incident:{TORNADO}"]
    assert tornado["geographic_boundary"]["type"] == "Polygon"
    assert all(responses[f"incident:{i}"]["geographic_boundary"] is None for i in SEEDED if i != TORNADO)
    for iid in SEEDED:
        for n in responses[f"notices:{iid}"]:
            assert n["simulation_flag"] is True and n.get("is_official") is not True
    actual = {k: digest(v) for k, v in responses.items()}
    assert actual == GOLDEN_DIGESTS, json.dumps(actual, indent=1)
    interleave_overlay_calls(env, [FLOOD, QUAKE, FIRE, TORNADO])
    assert {k: digest(v) for k, v in _public_responses(env.client).items()} == actual


GOLDEN_LAYERS = {
    "buildings": (200, "c27d50ae92289c84a4c1f43a0da0e873f1d8210fc9c18d9c78d0a2f2c2c21397"),
    "extraction_boundary": (200, "60588c2787d87427fdf054bbecc39172151dc655c76b99de821e662f57ad5a09"),
    "cta_stations": (503, None),  # not in the SIMULATED fixture graph
    "simulated_station": (503, None),
    "indoor_building": (503, None),
}
GOLDEN_BUILDINGS_SIMPLIFIED = "f1a8f017fd33605365aeaaa339b35b148e7a2728900aa28aaab64e0ccdbd581b"


def test_layers_unchanged(fresh):
    client = fresh.client
    plain = {"Accept-Encoding": "identity"}
    for name, (status, sha) in GOLDEN_LAYERS.items():
        r = client.get(f"/api/layers/{name}", headers=plain)
        assert r.status_code == status, name
        if sha is not None:
            assert hashlib.sha256(r.content).hexdigest() == sha, name
            assert r.json() == GRAPH.layers[name]
            assert r.headers["cache-control"] == "public, max-age=3600"
    simp = client.get("/api/layers/buildings", params={"simplify": "true"}, headers=plain)
    assert hashlib.sha256(simp.content).hexdigest() == GOLDEN_BUILDINGS_SIMPLIFIED
    assert client.get("/api/layers/graph").status_code == 404


# ======================================================================= graph edges near
def _reference_geometry(idx: int) -> dict:
    coords = decode_geometry(GRAPH.edge(idx).geometry)
    if coords is None:
        a, b = int(GRAPH.edge_from[idx]), int(GRAPH.edge_to[idx])
        coords = [(float(GRAPH.node_lon[a]), float(GRAPH.node_lat[a])),
                  (float(GRAPH.node_lon[b]), float(GRAPH.node_lat[b]))]
    return {"type": "LineString", "coordinates": [[x, y] for x, y in coords]}


GOLDEN_NEAR = {  # edge index -> [(edge_id, coordinates)] observed at the edge midpoint
    0: [("fx-sim-e001", [[-87.6489, 41.8708], [-87.6505, 41.8708]]),
        ("fx-sim-e000", [[-87.6505, 41.8708], [-87.6489, 41.8708]])],
    5: [("fx-sim-e005", [[-87.631, 41.8708], [-87.64, 41.8708]]),
        ("fx-sim-e004", [[-87.64, 41.8708], [-87.631, 41.8708]])],
    17: [("fx-sim-e016", [[-87.64, 41.876], [-87.631, 41.876]]),
         ("fx-sim-e017", [[-87.631, 41.876], [-87.64, 41.876]])],
    42: [("fx-sim-e043", [[-87.6278, 41.8819], [-87.631, 41.8819]]),
         ("fx-sim-e042", [[-87.631, 41.8819], [-87.6278, 41.8819]])],
}


def _near(client: TestClient, idx: int) -> list[dict]:
    a, b = int(GRAPH.edge_from[idx]), int(GRAPH.edge_to[idx])
    lat = float((GRAPH.node_lat[a] + GRAPH.node_lat[b]) / 2)
    lon = float((GRAPH.node_lon[a] + GRAPH.node_lon[b]) / 2)
    r = client.get("/api/graph/edges/near", params={"lat": lat, "lon": lon, "radius": 30, "limit": 3})
    assert r.status_code == 200, r.text
    return r.json()["edges"]


def test_graph_edges_near_golden_geometry(pair):
    env, _ = pair
    for idx, expected in GOLDEN_NEAR.items():
        edges = _near(env.client, idx)
        assert [(e["edge_id"], e["geometry_geojson"]["coordinates"]) for e in edges] == expected
        assert all(e["geometry_geojson"]["type"] == "LineString" for e in edges)


@settings(max_examples=40, deadline=None)
@given(idx=st.integers(0, GRAPH.num_edges - 1))
def test_graph_edges_near_geometry_matches_graph(pair, idx):
    env, _ = pair
    for e in _near(env.client, idx):
        assert e["geometry_geojson"] == _reference_geometry(GRAPH.edge_ids.index(e["edge_id"]))
