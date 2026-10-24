"""GET /api/indoor/building serves the SIMULATED indoor GeoJSON."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_indoor_building_endpoint_is_public_and_simulated():
    client = TestClient(create_app(Settings(profile="local")))
    res = client.get("/api/indoor/building")
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "FeatureCollection"
    assert "SIMULATED" in body["properties"]["notice"]
    roles = {f["properties"]["role"] for f in body["features"]}
    assert {"floor", "corridor", "zone", "stairwell", "exit", "start_position"} <= roles
    assert all(f["properties"]["simulated"] is True for f in body["features"])
    stairs = {f["properties"]["stairwell_id"] for f in body["features"] if f["properties"]["role"] == "stairwell"}
    assert stairs == {"A", "B", "C"}
    corridor = next(f for f in body["features"] if f["properties"]["role"] == "corridor")
    assert corridor["properties"]["node_ids"][0].startswith("sim-indoor-")
