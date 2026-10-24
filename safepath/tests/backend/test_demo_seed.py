"""SIMULATED demo scenario seeding (task 11.3; Req 37-40, 7.1)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository, Role
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.geo import haversine
from app.main import create_app
from app.routing import indoor
from app.routing.graph import Graph
from app.services import demo_data as dd
from app.services.overlays import RepositoryContextProvider

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo_incidents.py"
ALL = ["flood_loop", "earthquake_debris", "blocked_stairwell", "tornado_shelter", "fire_downtown"]


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.load(FIXTURE_ROOT)


@pytest.fixture(scope="module")
def seeded(graph):
    repo = MemoryRepository()
    result = dd.seed(repo, graph=graph)
    return repo, result


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_all_incidents_are_simulated_and_scenario_tagged(seeded):
    repo, result = seeded
    assert result.created == ALL and not result.skipped
    assert result.graph["used"] == "simulated_fixture" and result.graph["simulated"] is True
    rows = repo.query("incidents", {"pk": "all"})
    assert {r["scenario_id"] for r in rows} == set(ALL)
    assert all(r["simulation_flag"] is True and "SIMULATED" in r["source"] for r in rows)
    for sid in ALL:
        row = dd.get_scenario(repo, sid)
        assert row["simulation_flag"] is True and row["classification"] == "Simulated"
        assert row["graph"]["used"] == "simulated_fixture"
        assert "SIMULATED" in row["name"] and row["steps"]


def test_notices_are_simulated_and_destination_is_not_a_shelter(seeded):
    repo, _ = seeded
    for sid, n_expected in (("flood_loop", 1), ("earthquake_debris", 1), ("fire_downtown", 2)):
        iid = dd.get_scenario(repo, sid)["incident_id"]
        notices = repo.query("notices", {"pk": iid})
        assert len(notices) == n_expected
        assert all(n["simulation_flag"] is True and "SIMULATED" in n["source_organization"] for n in notices)
    iid = dd.get_scenario(repo, "flood_loop")["incident_id"]
    dest = repo.query("notices", {"pk": iid})[0]
    assert dest["notice_type"] == "incident_destination"
    assert "SIMULATION DESTINATION" in dest["text"] and "NOT an official evacuation shelter" in dest["text"]


def test_flood_scenario_data(seeded, graph):
    repo, _ = seeded
    row = dd.get_scenario(repo, "flood_loop")
    assert haversine(row["origin"]["latitude"], row["origin"]["longitude"], 41.8882, -87.6280) <= 200
    assert row["destination"]["label"].startswith("SIMULATION DESTINATION")
    hl = row["resolved"]["flood_highlight"]
    assert "SIMULATED" in hl["properties"]["label"] and hl["properties"]["simulated"] is True
    assert not any("depth" in k.lower() for k in _keys(hl))
    assert set(hl["edge_ids"]) <= set(graph.edge_ids)
    cl = row["resolved"]["closure"]
    assert cl["simulated"] is True and set(cl["edge_ids"]) <= set(graph.edge_ids)
    assert cl["edge_ids"][0] in row["resolved"]["route_before_edge_ids"]
    assert cl["edge_ids"][0] not in cl["route_after_edge_ids"]


def test_earthquake_debris_zones_reference_real_buildings_and_edges(seeded, graph):
    repo, _ = seeded
    res = dd.get_scenario(repo, "earthquake_debris")["resolved"]
    ids = {(f["properties"].get("id") or f["properties"].get("building_id")) for f in graph.layers["buildings"]["features"]}
    assert res["debris_zones"]
    for z in res["debris_zones"]:
        assert z["building_id"] in ids and z["simulated"] is True and "SIMULATED" in z["label"]
        assert set(z["intersecting_edge_ids"]) <= set(graph.edge_ids)
    assert set(res["closure"]["edge_ids"]) <= set(graph.edge_ids)
    assert res["closure"]["route_after_edge_ids"]


def test_stairwell_scenario_users_and_edges(seeded, graph):
    repo, _ = seeded
    row = dd.get_scenario(repo, "blocked_stairwell")
    res = row["resolved"]
    assert [u["key"] for u in res["users"]] == ["A", "B", "C"] and all(u["simulated"] for u in res["users"])
    nodes, edges = indoor.build_indoor_parts(indoor.load_spec(), list(graph.model.nodes))
    assert {u["indoor_node_id"] for u in res["users"]} <= {n.node_id for n in nodes}
    assert set(res["closure"]["edge_ids"]) <= {e.edge_id for e in edges}
    assert all("stair-A" in e or "-A-" in e for e in res["closure"]["edge_ids"])
    kinds = [s["kind"] for s in row["steps"]]
    assert kinds == ["load_indoor_graph", "submit_report", "observe", "confirm_report",
                     "confirm_stairwell_closure", "calculate_routes", "post_message"]
    assert row["steps"][-1]["message_type"] == "assistance_request"
    for u in res["users"]:
        assert repo.get("users", {"pk": u["user_id"], "sk": ""}) is not None


def test_tornado_and_fire_definitions(seeded):
    repo, _ = seeded
    t = dd.get_scenario(repo, "tornado_shelter")
    assert t["resolved"]["expected_outcome"] == "SHELTER_IN_PLACE"
    assert "no verified shelter available" in t["resolved"]["expected_text"]
    assert repo.query("notices", {"pk": t["incident_id"]}) == []
    f = dd.get_scenario(repo, "fire_downtown")
    assert "SIMULATED" in f["resolved"]["perimeter"]["properties"]["label"]


def test_seed_is_idempotent(graph):
    repo = MemoryRepository()
    dd.seed(repo, graph=graph)

    def snapshot():
        return {t: len(repo.query(t, {"pk": pk}, limit=10_000))
                for t, pk in (("incidents", "all"), ("demo_scenarios", "scenarios"))} | {
            "notices": sum(len(repo.query("notices", {"pk": i["sk"]})) for i in repo.query("incidents", {"pk": "all"}))}

    before = snapshot()
    again = dd.seed(repo, graph=graph)
    assert again.created == [] and again.skipped == ALL
    assert snapshot() == before


def test_reset_removes_only_that_scenario_then_reseed_restores(graph):
    repo = MemoryRepository()
    dd.seed(repo, graph=graph)
    dd.apply_step(repo, "flood_loop", "closure")
    assert not dd.is_seeded_state(repo, "flood_loop")
    fire = dd.get_scenario(repo, "fire_downtown")
    removed = dd.reset_scenario(repo, "blocked_stairwell")
    assert removed["incidents"] == 1 and removed["users"] == 3
    assert dd.get_scenario(repo, "blocked_stairwell") is None
    assert repo.get("users", {"pk": "sim-blocked-stairwell-user-a", "sk": ""}) is None
    assert repo.get("users", {"pk": "sim-demo-moderator", "sk": ""}) is not None
    assert dd.get_scenario(repo, "fire_downtown") == fire
    assert dd.reset_scenario(repo, "blocked_stairwell")["incidents"] == 0  # idempotent
    # flood: reset clears the closure; reseed restores the pristine state
    dd.reset_scenario(repo, "flood_loop")
    assert repo.query("overlays", {"pk": "sim-flood-loop"}) == []
    assert dd.seed(repo, graph=graph).created == ["flood_loop", "blocked_stairwell"]
    assert dd.is_seeded_state(repo, "flood_loop") and dd.is_seeded_state(repo, "blocked_stairwell")


def test_apply_step_rejects_unknown(graph):
    repo = MemoryRepository()
    with pytest.raises(dd.DemoDataError):
        dd.apply_step(repo, "flood_loop", "closure")
    dd.seed(repo, graph=graph, scenarios=["flood_loop"])
    with pytest.raises(dd.DemoDataError):
        dd.apply_step(repo, "flood_loop", "nope")
    assert dd.apply_step(repo, "flood_loop", "route")["applied"] is False


def _client(graph):
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={"mod": Role.MODERATOR})
    app = create_app(Settings())
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    provider = RepositoryContextProvider(repo)
    app.state.routing_context_provider = provider
    app.state.routing_service = None
    app.state.graph = graph
    return TestClient(app, raise_server_exceptions=False), repo, provider


def _route(client, row):
    rr = row["route_request"]
    r = client.post("/api/routes/calculate", json={
        "incident_id": row["incident_id"],
        "origin": {k: row["origin"][k] for k in ("latitude", "longitude")},
        "destination": {k: row["destination"][k] for k in ("latitude", "longitude")},
        "travel_mode": rr["travel_mode"], "mobility_profile": rr["mobility_profile"],
        "alternatives": rr["alternatives"]})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("sid,step", [("flood_loop", "closure"), ("earthquake_debris", "debris")])
def test_route_changes_after_simulated_closure(graph, sid, step):
    client, repo, provider = _client(graph)
    dd.seed(repo, graph=graph, scenarios=[sid], on_change=provider.invalidate)
    row = dd.get_scenario(repo, sid)
    before = _route(client, row)
    before_ids = [p["edge_id"] for p in before["routes"][0]["edge_penalties"]]
    assert before["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE" and before_ids
    if sid == "flood_loop":
        assert row["resolved"]["closure"]["edge_ids"][0] in before_ids
    for s in (step, "closure"):
        dd.apply_step(repo, sid, s, on_change=provider.invalidate)
    after = _route(client, row)
    after_ids = [p["edge_id"] for p in after["routes"][0]["edge_penalties"]]
    assert after["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE" and after_ids
    assert after_ids != before_ids
    assert not set(row["resolved"]["closure"]["edge_ids"]) & set(after_ids)
    route = after["routes"][0]
    assert route["excluded_edges_count"] >= 1
    assert "SIMULATED" in route["reason_summary"]
    assert any(r["simulated"] for r in route["restrictions_considered"])
    if sid == "earthquake_debris":
        assert any(r["id"].startswith("sim-eq-debris") for r in route["restrictions_considered"])


def test_cli_seeds_and_prints_summary(monkeypatch, capsys):
    monkeypatch.setenv("SAFEPATH_GRAPH_ROOT", str(FIXTURE_ROOT))
    spec = importlib.util.spec_from_file_location("seed_demo_incidents", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    repo = MemoryRepository()
    assert mod.main(["--profile", "local", "--json"], repository=repo) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["created"] == ALL and out["graph"]["used"] == "simulated_fixture"
    assert mod.main(["--profile", "local"], repository=repo) == 0
    text = capsys.readouterr().out
    assert "already seeded" in text and "SIMULATED" in text
