import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.api.registry import discover_routers
from app.config import Settings
from app.main import create_app


def test_health_reports_status_graph_version_and_profile():
    client = TestClient(create_app(Settings(profile="aws")))
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["profile"] == "aws"
    assert "graph_version" in body


def test_settings_from_env_defaults_to_local():
    assert Settings.from_env({}).profile == "local"


def test_settings_rejects_unknown_profile():
    with pytest.raises(ValueError):
        Settings.from_env({"SAFEPATH_PROFILE": "prod"})


@given(st.sampled_from(["local", "aws"]))
def test_profile_round_trips_through_health(profile):
    client = TestClient(create_app(Settings.from_env({"SAFEPATH_PROFILE": profile})))
    assert client.get("/api/health").json()["profile"] == profile


def test_registry_discovers_routers():
    assert all(isinstance(r, APIRouter) for r in discover_routers())
    assert len(discover_routers()) >= 1
