"""Unit tests for API infrastructure (task 8.4): errors, body cap, sanitizer, rate limits, CORS, OpenAPI."""
from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.adapters.ports import RepositoryUnavailable
from app.adapters.repository import MemoryRepository
from app.api.ratelimit import (
    DEFAULT_LIMITS,
    InMemoryRateLimiter,
    RateLimitExceeded,
    RepositoryRateLimiter,
    rate_limit,
)
from app.api.sanitize import sanitize_text
from app.api.setup import install_api_infrastructure
from app.config import Settings
from app.routing.validation import RequestValidationError


class Body(BaseModel):
    name: str
    count: int


def build_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI()
    install_api_infrastructure(app, settings)
    r = APIRouter()

    @r.post("/echo")
    def echo(body: Body):
        return body

    @r.get("/http")
    def http_err():
        raise HTTPException(404, "nope")

    @r.get("/route-invalid")
    def route_invalid():
        raise RequestValidationError(400, "outside_region", "origin outside", [{"field": "origin", "problem": "x"}])

    @r.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail /etc/passwd")

    @r.get("/down")
    def down():
        raise RepositoryUnavailable("db dead")

    @r.post("/report", dependencies=[Depends(rate_limit("report"))])
    def report():
        return {"ok": True}

    app.include_router(r)
    return app


@pytest.fixture
def client():
    return TestClient(build_app(), raise_server_exceptions=False)


def test_field_level_validation_error(client):
    resp = client.post("/echo", json={"name": 1})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert {f["field"] for f in err["fields"]} == {"name", "count"}
    assert err["retry_after_seconds"] is None


def test_http_exception_envelope(client):
    resp = client.get("/http")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.json()["error"]["message"] == "nope"
    assert client.get("/missing").json()["error"]["code"] == "not_found"


def test_routing_validation_error_uses_its_envelope(client):
    resp = client.get("/route-invalid")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "outside_region"
    assert resp.json()["error"]["fields"] == [{"field": "origin", "problem": "x"}]


def test_uncaught_exception_is_500_without_leaking(client):
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "secret" not in resp.text and "passwd" not in resp.text


def test_repository_unavailable_is_503(client):
    resp = client.get("/down")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"
    assert "db dead" not in resp.text


def test_body_cap_content_length(client):
    resp = client.post("/echo", content=b"x" * 1_000_001, headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_body_cap_chunked(client):
    def gen():
        for _ in range(11):
            yield b"x" * 100_000

    resp = client.post("/echo", content=gen(), headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_body_under_cap_passes(client):
    resp = client.post("/echo", json={"name": "a", "count": 1})
    assert resp.status_code == 200


@pytest.mark.parametrize("raw,expected", [
    ("<b>hi</b>", "hi"),
    ("<script>alert(1)</script>ok", "ok"),
    ('<img src=x onerror="alert(1)">text', "text"),
    ("plain text", "plain text"),
    ("<<b>script>alert(1)<</b>/script>", None),
])
def test_sanitize(raw, expected):
    out = sanitize_text(raw)
    if expected is not None:
        assert out == expected
    assert "<" not in out and ">" not in out
    assert sanitize_text(out) == out


def test_default_limits():
    assert DEFAULT_LIMITS == {"report": (5, 600), "message": (20, 60), "vote": (30, 600), "checkin": (10, 600)}


@pytest.mark.parametrize("make", [
    lambda clock: InMemoryRateLimiter(clock=clock),
    lambda clock: RepositoryRateLimiter(MemoryRepository(), clock=clock),
])
def test_limiter_window(make):
    now = [1000.0]
    lim = make(lambda: now[0])
    for _ in range(5):
        lim.check("u1", "report")
    with pytest.raises(RateLimitExceeded) as ei:
        lim.check("u1", "report")
    assert ei.value.retry_after_seconds == 600
    lim.check("u2", "report")  # per-user
    lim.check("u1", "message")  # per-action
    now[0] += 601
    lim.check("u1", "report")  # window slid


def test_limits_configurable():
    lim = InMemoryRateLimiter({"report": (1, 10)}, clock=lambda: 0.0)
    lim.check("u", "report")
    with pytest.raises(RateLimitExceeded):
        lim.check("u", "report")


def test_429_with_retry_after(client):
    for _ in range(5):
        assert client.post("/report").status_code == 200
    resp = client.post("/report")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    err = resp.json()["error"]
    assert err["code"] == "rate_limited"
    assert err["retry_after_seconds"] == int(resp.headers["Retry-After"])


def test_cors_explicit_origin_allows_credentials():
    c = TestClient(build_app(Settings(cors_origins=("https://app.example",))))
    resp = c.options("/echo", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"})
    assert resp.headers["access-control-allow-origin"] == "https://app.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_cors_wildcard_never_with_credentials():
    s = Settings(cors_origins=("*",))
    assert s.cors_allow_credentials is False
    c = TestClient(build_app(s))
    resp = c.get("/http", headers={"Origin": "https://x.example"})
    assert "access-control-allow-credentials" not in resp.headers


def test_cors_origins_from_env():
    s = Settings.from_env({"SAFEPATH_CORS_ORIGINS": "https://a.example/, https://b.example"})
    assert s.cors_origins == ("https://a.example", "https://b.example")


def test_openapi_metadata():
    from app.main import create_app

    spec = create_app().openapi()
    assert spec["info"]["title"] == "SafePath API"
    assert spec["info"]["version"]
    assert "911" in spec["info"]["description"] and "prototype" in spec["info"]["description"]
    assert spec["tags"]
