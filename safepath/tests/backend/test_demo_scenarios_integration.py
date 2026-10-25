"""Backend scenario integration tests for the three demo scenarios (task 11.8).

Driven end to end through the HTTP API (FastAPI TestClient, local profile, in-process
publisher, MemoryRepository) on the SIMULATED fixture graph merged with the fictional
Indoor_Graph. Each scenario follows its data-defined steps from ``data/demo/scenarios.json``:
the console applies server steps through ``/api/demo/...`` and replays the ``client_action``
request bodies against the public API, exactly as the Demo_Admin_Console does.

Requirements: 37.4, 37.5, 38.3, 38.4, 39.2, 39.3, 39.4, 39.5, 44.11.
"""
from __future__ import annotations

import time

import pytest

from app.routing.search import dijkstra
from app.services import demo_data as dd

from test_demo_service import build, hdr

FLOOD = "flood_loop"
QUAKE = "earthquake_debris"
STAIR = "blocked_stairwell"
MOD = "mod"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    client, app, repo, auth = build()
    return client, app, repo, auth, hdr(auth, MOD)


# ------------------------------------------------------------------------------- helpers
def start(client, m, sid):
    r = client.post(f"/api/demo/scenarios/{sid}/start", headers=m)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simulation_flag"] is True and body["activated"] is True
    return body


def apply(client, m, sid, step):
    r = client.post(f"/api/demo/scenarios/{sid}/steps/{step}/apply", headers=m)
    assert r.status_code == 200, r.text
    return r.json()


def calculate(client, body):
    started = time.monotonic()
    r = client.post("/api/routes/calculate", json=body)
    elapsed = time.monotonic() - started
    assert r.status_code == 200, r.text
    assert elapsed < 5.0, f"route calculation took {elapsed:.2f}s"
    return r.json()


def edges_of(route):
    return [p["edge_id"] for p in route["edge_penalties"]]


def cited(route, kind=None):
    return {c["id"] for c in route["restrictions_considered"] if kind is None or c["kind"] == kind}


def assert_graph_derived(app, resp):
    """Req 44.11: the route API answers from the loaded (processed) Graph and its geometry is
    built from Graph nodes along the returned edges."""
    graph = app.state.graph
    assert resp["graph_version"] == graph.version
    for route in resp["routes"]:
        ids = edges_of(route)
        assert ids and all(e in graph.edge_ids for e in ids)
        idx = [graph.edge_ids.index(e) for e in ids]
        for a, b in zip(idx, idx[1:]):  # a connected, directed path
            assert graph.edge_to[a] == graph.edge_from[b]
        coords = route["geometry"]["coordinates"]
        assert len(coords) >= 2
        first, last = int(graph.edge_from[idx[0]]), int(graph.edge_to[idx[-1]])
        assert coords[0] == pytest.approx([graph.node_lon[first], graph.node_lat[first]], abs=1e-6)
        assert coords[-1] == pytest.approx([graph.node_lon[last], graph.node_lat[last]], abs=1e-6)
        assert route["distance_meters"] == pytest.approx(sum(graph.edge_dist[i] for i in idx), rel=1e-6)
        assert route["label"] == "algorithm-generated"


def shortest_distance(app, route):
    """Reference distance-only shortest path between the route's endpoint nodes (no hazards)."""
    graph = app.state.graph
    idx = [graph.edge_ids.index(e) for e in edges_of(route)]
    p = dijkstra(graph, None, int(graph.edge_from[idx[0]]), int(graph.edge_to[idx[-1]]),
                 lambda e: float(graph.edge_dist[e]))
    assert p is not None
    return p.total_cost


def events(client, iid):
    return [e["type"] for e in client.get("/api/events", params={"incident_id": iid}).json()["events"]]


# --------------------------------------------------------------- Scenario 1: Loop flooding
def test_scenario1_flood_routes_and_recalculation_after_verified_closure(env):
    client, app, repo, auth, m = env
    s = start(client, m, FLOOD)
    iid = s["incident_id"]
    row = dd.get_scenario(repo, FLOOD)

    # step "route": the console replays the client_action body against the public route API
    action = apply(client, m, FLOOD, "route")["client_action"]
    assert action["endpoint"] == "POST /api/routes/calculate"
    body = action["body"]
    assert body["alternatives"] >= 2
    before = calculate(client, body)
    assert before["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    assert_graph_derived(app, before)

    # Req 37.4: the least-cost (disaster-adjusted) route plus at least one distinct alternative
    routes = before["routes"]
    assert before["route_count"] == len(routes) >= 2
    assert routes[0]["route_type"] == "disaster-adjusted"
    assert all(r["route_type"] == "alternate" for r in routes[1:])
    assert len({tuple(edges_of(r)) for r in routes}) == len(routes)
    assert edges_of(routes[0]) == row["resolved"]["route_before_edge_ids"]
    # the disaster-adjusted cost includes the SIMULATED flood module penalties
    assert "flood_elevation" in routes[0]["hazard_flags"]
    assert any(sum(p["hazard"].values()) > 0 for p in routes[0]["edge_penalties"])
    assert routes[0]["reason_summary"].startswith("SIMULATED")
    # the distance-shortest path is a lower bound for every option; the route set contains a
    # route that is (near-)shortest by distance between the same endpoints
    shortest = shortest_distance(app, routes[0])
    assert all(r["distance_meters"] >= shortest - 1e-6 for r in routes)
    assert min(r["distance_meters"] for r in routes) <= shortest * 1.05
    assert all(r["excluded_edges_count"] == 0 and not r["restrictions_considered"] for r in routes)

    # step "closure": a SIMULATED verified closure on the current best route
    closure = row["resolved"]["closure"]
    closed = set(closure["edge_ids"])
    assert closed & set(edges_of(routes[0]))
    got = []
    app.state.publisher.subscribe(iid, got.append)
    applied = apply(client, m, FLOOD, "closure")
    assert applied["applied"] is True and applied["simulated"] is True and applied["overlay_versions"]
    assert any(e.type.startswith("restriction") or "overlay" in e.type for e in got), [e.type for e in got]

    # step "reroute": Req 37.5 / 44.11 the verified closure triggers a different route + explanation
    reroute = apply(client, m, FLOOD, "reroute")["client_action"]
    assert reroute["expect"] == "route_changed"
    started = time.monotonic()
    after = calculate(client, reroute["body"])
    assert time.monotonic() - started < 5.0
    assert after["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    assert_graph_derived(app, after)
    best = after["routes"][0]
    assert edges_of(best) != edges_of(routes[0])
    assert edges_of(best) == closure["route_after_edge_ids"]
    for r in after["routes"]:
        assert not closed & set(edges_of(r))
        assert closure["restriction_id"] in cited(r, "closure")
        assert all(c["simulated"] for c in r["restrictions_considered"])
        assert r["excluded_edges_count"] >= len(closed)
    assert best["reason_summary"] and best["reason_summary"] != routes[0]["reason_summary"]

    # removing the closure restores the original best route
    listed = client.get("/api/demo/scenarios", headers=m).json()["scenarios"]
    assert "closure" in next(x for x in listed if x["scenario_id"] == FLOOD)["applied_steps"]
    client.post(f"/api/demo/scenarios/{FLOOD}/reset", headers=m)
    restored = calculate(client, body)
    assert edges_of(restored["routes"][0]) == edges_of(routes[0])


# ---------------------------------------------------- Scenario 2: earthquake debris exclusion
def test_scenario2_debris_zones_block_edges_and_are_cited(env):
    client, app, repo, auth, m = env
    s = start(client, m, QUAKE)
    iid = s["incident_id"]
    row = dd.get_scenario(repo, QUAKE)
    zones = row["resolved"]["debris_zones"]
    assert zones and all(z["simulated"] is True and z["zone_id"].startswith("sim-") for z in zones)
    zone_edges = {z["zone_id"]: set(z["intersecting_edge_ids"]) for z in zones}
    blocked_by_zones = set().union(*zone_edges.values())
    assert blocked_by_zones, "the seeded debris zones intersect at least one road edge"

    body = apply(client, m, QUAKE, "route")["client_action"]["body"]
    before = calculate(client, body)
    assert_graph_derived(app, before)
    assert before["routes"][0]["excluded_edges_count"] == 0
    probe_edge = sorted(blocked_by_zones)[0]
    assert client.get(f"/api/graph/edges/{probe_edge}", params={"incident_id": iid}).json()[
        "restrictions"]["causes"] == []

    # step "debris": Req 38.3 intersecting edges are marked blocked (well within 5 s) and excluded
    started = time.monotonic()
    applied = apply(client, m, QUAKE, "debris")
    assert applied["applied"] is True and len(applied["overlay_versions"]) == len(zones)
    for zid, eids in zone_edges.items():
        for eid in eids:
            info = client.get(f"/api/graph/edges/{eid}", params={"incident_id": iid}).json()
            assert info["restrictions"]["available"] is True
            assert zid in {c["id"] for c in info["restrictions"]["causes"]}, (zid, eid, info["restrictions"])
    assert time.monotonic() - started < 5.0
    with_debris = calculate(client, body)
    r = with_debris["routes"][0]
    assert not blocked_by_zones & set(edges_of(r))
    assert r["excluded_edges_count"] >= len(blocked_by_zones)
    assert edges_of(r) == row["resolved"]["route_with_debris_edge_ids"]

    # step "closure" then "reroute": the alternate route cites the debris zones (Req 38.4)
    apply(client, m, QUAKE, "closure")
    reroute = apply(client, m, QUAKE, "reroute")["client_action"]
    assert reroute["expect"] == "alternate_route"
    after = calculate(client, reroute["body"])
    assert after["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    assert_graph_derived(app, after)
    alt = after["routes"][0]
    assert edges_of(alt) != edges_of(before["routes"][0])
    assert edges_of(alt) == row["resolved"]["closure"]["route_after_edge_ids"]
    excluded = blocked_by_zones | set(row["resolved"]["closure"]["edge_ids"])
    assert not excluded & set(edges_of(alt))
    zone_refs = cited(alt, "impassable_polygon")
    for zid, eids in zone_edges.items():
        if eids:  # every zone that caused an exclusion is referenced in the explanation
            assert zid in zone_refs, (zid, alt["restrictions_considered"])
    assert zone_refs <= set(zone_edges)
    assert all(c["simulated"] for c in alt["restrictions_considered"])
    assert row["resolved"]["closure"]["restriction_id"] in cited(alt, "closure")
    assert alt["reason_summary"].startswith("SIMULATED")


# --------------------------------------------------------- Scenario 3: blocked stairwell
def test_scenario3_report_corroborate_confirm_reroute_and_assistance(env):
    client, app, repo, auth, m = env
    start(client, m, STAIR)
    row = dd.get_scenario(repo, STAIR)
    iid = row["incident_id"]
    users = {u["key"]: u for u in row["resolved"]["users"]}
    assert {"A", "B", "C"} <= set(users)
    stair_edges = set(row["resolved"]["closure"]["edge_ids"])
    received = []  # what User B's subscription to the incident channel delivers
    app.state.publisher.subscribe(iid, received.append)

    def act(actor, action, **kw):
        return client.post(f"/api/demo/scenarios/{STAIR}/act",
                           json={"actor": actor, "action": action, **kw}, headers=m)

    reroute_bodies = apply(client, m, STAIR, "reroute")["client_action"]["bodies_by_actor"]
    assert set(reroute_bodies) == {"A", "B", "C"}
    before = {k: calculate(client, b) for k, b in reroute_bodies.items()}
    for k, resp in before.items():
        assert resp["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE", (k, resp["explanation"])
    assert stair_edges & set(edges_of(before["A"]["routes"][0])), "User A initially routes via Stairwell A"

    # Req 39.2: A reports, B receives the report on the incident channel and via the public API
    t0 = time.monotonic()
    rep = act("A", "report").json()["report"]
    assert rep["verification_status"] == "Unverified"
    assert any(e.payload.get("report_id") == rep["report_id"] or rep["report_id"] in str(e.payload)
               for e in received), [e.type for e in received]
    listed = client.get("/api/reports", params={"incident_id": iid}).json()["reports"]
    assert rep["report_id"] in [x["report_id"] for x in listed]
    assert time.monotonic() - t0 < 5.0

    # Req 39.3: B and C corroborate; threshold gives Corroborated, labeled not officially verified
    assert act("B", "corroborate").json()["report"]["verification_status"] == "Unverified"
    rc = act("C", "corroborate").json()["report"]
    assert rc["verification_status"] == "Corroborated"
    assert "not officially verified" in rc["status_label"]
    assert repo.query("overlays", {"pk": iid}) == []
    # an unconfirmed (Corroborated) report is only a soft penalty: nothing is excluded yet
    still = {k: calculate(client, b) for k, b in reroute_bodies.items()}
    for resp in still.values():
        assert all(r["excluded_edges_count"] == 0 and not r["restrictions_considered"]
                   for r in resp["routes"])

    # Req 39.4: the demo moderator confirms; Stairwell A edges are removed; routes update < 5 s
    t0 = time.monotonic()
    confirm = act("moderator", "confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["report"]["verification_status"] == "Officially_Verified"
    after = {k: calculate(client, b) for k, b in reroute_bodies.items()}
    assert time.monotonic() - t0 < 5.0
    for k, resp in after.items():
        assert resp["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE", (k, resp["explanation"])
        for r in resp["routes"]:
            assert not stair_edges & set(edges_of(r)), k
            assert all(c["simulated"] for c in r["restrictions_considered"])
            assert "safe" not in r["reason_summary"].lower().replace("unsafe", "")
    assert edges_of(after["A"]["routes"][0]) != edges_of(before["A"]["routes"][0])
    assert before["A"]["routes"][0]["excluded_edges_count"] == 0
    assert after["A"]["routes"][0]["excluded_edges_count"] >= len(stair_edges)

    # Req 39.5: B's assistance request is published to the incident channel within 5 s
    n = len(received)
    t0 = time.monotonic()
    msg = act("B", "message")
    assert msg.status_code == 200, msg.text
    assert msg.json()["message_type"] == "assistance_request"
    published = received[n:]
    assert any("message" in e.type for e in published), [e.type for e in published]
    assert time.monotonic() - t0 < 5.0
    msgs = client.get("/api/messages", params={"incident_id": iid}).json()["messages"]
    assert [x["message_type"] for x in msgs] == ["assistance_request"]
    # the same events are persisted for catch-up by clients that were disconnected (Req 44.11)
    persisted = events(client, iid)
    assert any("report" in t for t in persisted) and any("message" in t for t in persisted)
