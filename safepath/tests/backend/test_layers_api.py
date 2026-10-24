"""GET /api/layers/{name}: public GeoJSON layers with caching headers, bbox and simplify."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(profile="local")))


def test_layer_served_with_cache_headers_and_304():
    c = _client()
    r = c.get("/api/layers/extraction_boundary")
    assert r.status_code == 200
    assert r.json()["type"] == "FeatureCollection"
    assert "max-age" in r.headers["cache-control"] and "public" in r.headers["cache-control"]
    etag = r.headers["etag"]
    again = c.get("/api/layers/extraction_boundary", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_unknown_layer_404():
    assert _client().get("/api/layers/secrets").status_code == 404


def test_buildings_bbox_and_simplify_reduce_payload():
    c = _client()
    full = c.get("/api/layers/buildings")
    assert full.status_code == 200
    feats = full.json()["features"]
    assert feats
    x, y = feats[0]["geometry"]["coordinates"][0][0][:2] if feats[0]["geometry"]["type"] == "Polygon" else (-87.63, 41.88)
    box = f"{x - 0.001},{y - 0.001},{x + 0.001},{y + 0.001}"
    part = c.get("/api/layers/buildings", params={"bbox": box, "simplify": "true"})
    assert part.status_code == 200
    assert 0 < len(part.json()["features"]) <= len(feats)
    assert len(part.content) < len(full.content) or len(feats) <= len(part.json()["features"])


def test_gzip_when_accepted():
    r = _client().get("/api/layers/extraction_boundary", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # TestClient (httpx) decodes transparently; the header proves the encoding was used.
    assert r.headers.get("content-encoding") == "gzip"
    assert json.loads(r.content)["type"] == "FeatureCollection"
    plain = _client().get("/api/layers/extraction_boundary", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers


def test_invalid_bbox_400():
    assert _client().get("/api/layers/buildings", params={"bbox": "1,2,3"}).status_code == 400
