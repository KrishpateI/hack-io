"""ReportService and /api/reports tests (Req 23.1-23.4, 23.7, 23.8, 28.3, 41.6, 16.1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository
from app.api.ratelimit import InMemoryRateLimiter
from app.api.routers.events import get_event_bus
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EVENTS_TABLE
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
INC = "inc_1"
NOW = datetime.now(timezone.utc)
POINT = {"latitude": 41.8708, "longitude": -87.6505}  # fixture node fx-uic
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("test-secret")
    app = create_app(Settings())
    install_auth(app, auth=auth, repository=repo)
    app.state.publisher = InProcessPublisher()
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    get_event_bus(app)
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC, "simulation_flag": True})
    return app, repo, auth, TestClient(app)


def hdr(auth, subject="usr_a", guest=False):
    tok, _ = auth.issue_token(subject, is_guest=guest)
    return {"Authorization": f"Bearer {tok}"}


def body(**kw):
    return {"incident_id": INC, "category": "blocked road", "description": "tree down", **POINT, **kw}


def test_create_persists_unverified_with_edges_and_hides_reporter(env):
    app, repo, auth, c = env
    r = c.post("/api/reports", json=body(), headers=hdr(auth))
    assert r.status_code == 201
    j = r.json()
    assert j["verification_status"] == "Unverified" and j["confirmations"] == 0
    assert j["affected_edge_ids"], "edges within 25 m of a graph node must be associated"
    assert j["simulated"] is True and j["source_label"] == "Community report"
    assert "reporter_id" not in j
    exp = datetime.fromisoformat(j["expires_at"]) - datetime.fromisoformat(j["created_at"])
    assert exp == timedelta(hours=4)
    row = repo.get("reports", {"pk": INC, "sk": j["report_id"]})
    assert row["reporter_id"] == "usr_a" and row["status_history"][0]["status"] == "Unverified"
    pub = c.get("/api/reports", params={"incident_id": INC}).json()["reports"]
    assert len(pub) == 1 and "reporter_id" not in pub[0] and "usr_a" not in str(pub)
    assert pub[0]["alias"] and pub[0]["status_label"] == "Unverified"
    assert "photo_key" not in pub[0] and "client_report_id" not in pub[0]
    assert repo.query("audit", {"pk": datetime.now(timezone.utc).date().isoformat()})


def test_no_graph_gives_empty_edges(env):
    app, _, auth, c = env
    app.state.graph = Graph()  # not loaded
    r = c.post("/api/reports", json=body(), headers=hdr(auth))
    assert r.status_code == 201 and r.json()["affected_edge_ids"] == []


def test_far_from_edges_has_no_association(env):
    _, _, auth, c = env
    r = c.post("/api/reports", json=body(latitude=41.9099, longitude=-87.6799), headers=hdr(auth))
    assert r.status_code == 201 and r.json()["affected_edge_ids"] == []


def test_event_published_in_same_transaction(env):
    app, repo, auth, c = env
    rid = c.post("/api/reports", json=body(), headers=hdr(auth)).json()["report_id"]
    (inc, ev), = app.state.publisher.published
    assert inc == INC and ev.type == "report_created" and ev.payload["report_id"] == rid
    assert repo.query(EVENTS_TABLE, {"pk": INC})


def test_unauthenticated_401_and_guest_allowed(env):
    _, _, auth, c = env
    assert c.post("/api/reports", json=body()).status_code == 401
    assert c.post("/api/reports", json=body(), headers=hdr(auth, "g1", guest=True)).status_code == 201


def test_validation_errors_are_field_level(env):
    _, _, auth, c = env
    h = hdr(auth)
    r = c.post("/api/reports", json=body(category="nope", description="x" * 1001), headers=h)
    assert r.status_code == 400
    fields = {f["field"] for f in r.json()["error"]["fields"]}
    assert {"category", "description"} <= fields
    r = c.post("/api/reports", json={"incident_id": INC, "category": "other"}, headers=h)
    assert r.status_code == 400
    assert {"latitude", "longitude"} <= {f["field"] for f in r.json()["error"]["fields"]}
    r = c.post("/api/reports", json=body(latitude=95.0), headers=h)
    assert r.status_code == 400


def test_outside_region_400_and_unknown_incident_404(env):
    _, _, auth, c = env
    r = c.post("/api/reports", json=body(latitude=40.7, longitude=-74.0), headers=hdr(auth))
    assert r.status_code == 400 and r.json()["error"]["fields"][0]["field"] == "latitude"
    assert c.post("/api/reports", json=body(incident_id="inc_missing"), headers=hdr(auth)).status_code == 404


def test_description_is_sanitized(env):
    _, _, auth, c = env
    r = c.post("/api/reports", json=body(description="<script>alert(1)</script><b>wet</b> road"),
               headers=hdr(auth))
    assert r.status_code == 201 and "<" not in r.json()["description"]
    assert "wet" in r.json()["description"]


def test_idempotent_resubmission_returns_same_report(env):
    _, repo, auth, c = env
    h = hdr(auth)
    a = c.post("/api/reports", json=body(client_report_id="c-1"), headers=h)
    b = c.post("/api/reports", json=body(client_report_id="c-1"), headers=h)
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()["report_id"] == b.json()["report_id"]
    assert len(repo.query("reports", {"incident_id": INC})) == 1
    other = c.post("/api/reports", json=body(client_report_id="c-1"), headers=hdr(auth, "usr_b"))
    assert other.json()["report_id"] != a.json()["report_id"]


def test_duplicate_is_linked_to_first_report(env):
    _, _, auth, c = env
    first = c.post("/api/reports", json=body(), headers=hdr(auth, "usr_a")).json()
    near = c.post("/api/reports", json=body(latitude=41.87085), headers=hdr(auth, "usr_b")).json()
    other_cat = c.post("/api/reports", json=body(category="flooding"), headers=hdr(auth, "usr_c")).json()
    far = c.post("/api/reports", json=body(latitude=41.8790), headers=hdr(auth, "usr_d")).json()
    assert first["duplicate_of"] is None
    assert near["duplicate_of"] == first["report_id"]
    assert other_cat["duplicate_of"] is None and far["duplicate_of"] is None


def test_duplicate_outside_time_window_not_linked(env):
    app, repo, auth, c = env
    first = c.post("/api/reports", json=body(), headers=hdr(auth, "usr_a")).json()
    row = repo.get("reports", {"pk": INC, "sk": first["report_id"]})
    row["created_at"] = (NOW - timedelta(minutes=61)).isoformat()
    repo.put("reports", row)
    again = c.post("/api/reports", json=body(), headers=hdr(auth, "usr_b")).json()
    assert again["duplicate_of"] is None


def test_rate_limit_429_after_five(env):
    _, _, auth, c = env
    h = hdr(auth)
    codes = [c.post("/api/reports", json=body(category="other"), headers=h).status_code for _ in range(6)]
    assert codes[:5] == [201] * 5 and codes[5] == 429


def test_list_excludes_resolved_and_expired(env):
    _, repo, auth, c = env
    ids = [c.post("/api/reports", json=body(category=cat), headers=hdr(auth, f"u{i}")).json()["report_id"]
           for i, cat in enumerate(["blocked road", "flooding", "other"])]
    for rid, patch in ((ids[0], {"verification_status": "Resolved"}),
                       (ids[1], {"expires_at": (NOW - timedelta(minutes=1)).isoformat()})):
        row = repo.get("reports", {"pk": INC, "sk": rid})
        repo.put("reports", {**row, **patch})
    listed = c.get("/api/reports", params={"incident_id": INC}).json()["reports"]
    assert [r["report_id"] for r in listed] == [ids[2]]
    allr = c.get("/api/reports", params={"incident_id": INC, "include_inactive": "true"}).json()["reports"]
    assert {r["report_id"]: r["verification_status"] for r in allr} == {
        ids[0]: "Resolved", ids[1]: "Expired", ids[2]: "Unverified"}


def test_indoor_stairwell_report_maps_to_indoor_edges(env):
    _, _, auth, c = env
    r = c.post("/api/reports", json={"incident_id": INC, "category": "inaccessible stairwell",
                                     "indoor_feature_id": "stair-A"}, headers=hdr(auth))
    assert r.status_code == 201
    ids = r.json()["affected_edge_ids"]
    assert ids and all(i.startswith("sim-indoor-t-stair-A-") for i in ids)
    bad = c.post("/api/reports", json={"incident_id": INC, "category": "inaccessible stairwell",
                                       "indoor_feature_id": "stair-Z"}, headers=hdr(auth))
    assert bad.status_code == 400
