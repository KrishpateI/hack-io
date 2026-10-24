"""Photo presign/upload tests (Req 23.5, 23.9, 28, 31)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository
from app.adapters.blob import LocalBlobStore
from app.api.routers.photos import photo_url_for, user_prefix
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def env(tmp_path):
    app = create_app(Settings())
    provider = DemoTokenAuthProvider("test-secret")
    install_auth(app, auth=provider, repository=MemoryRepository())
    clock = Clock()
    app.state.blob_store = LocalBlobStore(tmp_path, "s", base_url="/api/photos/upload", clock=clock)
    client = TestClient(app, raise_server_exceptions=False)
    g = provider.issue_guest()
    return app, client, {"Authorization": f"Bearer {g.token}"}, g.principal.subject, clock


def presign(client, h, ct="image/jpeg", size=200):
    return client.post("/api/photos/presign", json={"content_type": ct, "size": size}, headers=h)


def put(client, up, data, ct="image/jpeg"):
    return client.put(up["upload_url"], content=data, headers={"Content-Type": ct})


def test_requires_auth(env):
    _, client, *_ = env
    assert client.post("/api/photos/presign", json={"content_type": "image/png", "size": 1}).status_code == 401


def test_type_and_size_limits(env):
    _, client, h, *_ = env
    assert presign(client, h, "image/gif").status_code == 415
    assert presign(client, h, "image/jpeg", 10 * 1024 * 1024 + 1).status_code == 413
    assert presign(client, h, "image/jpeg", 10 * 1024 * 1024).status_code == 200


def test_key_namespaced_per_user_and_roundtrip(env):
    app, client, h, subject, clock = env
    up = presign(client, h).json()
    assert up["key"].startswith(user_prefix(subject)) and up["key"].endswith(".jpg")
    assert put(client, up, JPEG).status_code == 204
    # Not available until uploaded; then a 15-minute signed GET works.
    url = photo_url_for(app, up["key"])
    assert url and client.get(url).content == JPEG
    assert photo_url_for(app, user_prefix(subject) + "missing.jpg") is None
    assert photo_url_for(app, None) is None
    assert int(parse_qs(urlparse(url).query)["exp"][0]) == int(clock.now) + 900
    clock.now += 901
    assert client.get(url).status_code == 403


def test_expired_and_tampered_put_rejected(env):
    _, client, h, _, clock = env
    up = presign(client, h).json()
    bad = dict(up, upload_url=up["upload_url"].replace("sig=", "sig=0"))
    assert put(client, bad, JPEG).status_code == 403
    bigger = dict(up, upload_url=up["upload_url"].replace("max=200", "max=9999"))
    assert put(client, bigger, JPEG).status_code == 403
    clock.now += 901
    assert put(client, up, JPEG).status_code == 403


def test_upload_validation(env):
    _, client, h, *_ = env
    up = presign(client, h).json()
    assert put(client, up, PNG).status_code == 415  # magic bytes mismatch
    assert put(client, up, JPEG, ct="image/png").status_code == 415  # header mismatch
    assert put(client, up, JPEG + b"x" * 150).status_code == 413  # over signed size
    assert put(client, up, JPEG).status_code == 204
    assert put(client, up, JPEG).status_code == 409  # single use


def test_png_upload_and_unsigned_get_forbidden(env):
    _, client, h, *_ = env
    up = presign(client, h, "image/png").json()
    assert put(client, up, PNG, "image/png").status_code == 204
    assert client.get(f"/api/photos/upload/{up['key']}").status_code == 403
    assert client.get("/api/photos").status_code in (404, 405)


def test_rate_limited_and_disabled(env, monkeypatch):
    _, client, h, *_ = env
    for _ in range(10):
        assert presign(client, h).status_code == 200
    assert presign(client, h).status_code == 429
    monkeypatch.setenv("SAFEPATH_PHOTOS_ENABLED", "false")
    assert presign(client, h).status_code in (429, 503)
