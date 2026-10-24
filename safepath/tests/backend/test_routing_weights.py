"""Tests for the routing weights loader and degraded state (task 6.2)."""
from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.main import create_app
from app.routing.cost import (
    DEFAULT_WEIGHTS_PATH,
    WeightsError,
    load_weights,
    load_weights_state,
)


def _default_data() -> dict:
    return yaml.safe_load(DEFAULT_WEIGHTS_PATH.read_text(encoding="utf-8"))


def _write(tmp_path, data) -> str:
    p = tmp_path / "routing_weights.yaml"
    p.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


def test_defaults_load():
    w = load_weights(DEFAULT_WEIGHTS_PATH)
    assert w.uncertainty["w_unknown_attr"] == 8
    assert w.mode_factor["wheelchair"] == 1.4
    assert w.flood["E_high"] == 184.0
    assert "tunnel_flag" in w.uncertainty_attributes


def test_missing_file(tmp_path):
    with pytest.raises(WeightsError) as ei:
        load_weights(tmp_path / "routing_weights.yaml")
    assert ei.value.file == "routing_weights.yaml"


def test_malformed_yaml(tmp_path):
    with pytest.raises(WeightsError):
        load_weights(_write(tmp_path, "a: [unclosed"))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("uncertainty", "w_elev_low", -1),
        ("flood", "w_tunnel", "heavy"),
        ("fire", "radius_meters", True),
        ("mode_factor", "walking", None),
        ("uncertainty", "w_unknown_attr", 0),
        ("uncertainty", "w_unknown_attr", -3),
    ],
)
def test_invalid_values_name_field(tmp_path, section, key, value):
    data = _default_data()
    data[section][key] = value
    with pytest.raises(WeightsError) as ei:
        load_weights(_write(tmp_path, data))
    assert ei.value.field == f"{section}.{key}"
    assert ei.value.file == "routing_weights.yaml"


def test_missing_section_and_negative_dmin(tmp_path):
    data = _default_data()
    del data["flood"]
    with pytest.raises(WeightsError) as ei:
        load_weights(_write(tmp_path, data))
    assert ei.value.field == "flood"
    data = _default_data()
    data["d_min_meters"] = -1
    with pytest.raises(WeightsError) as ei:
        load_weights(_write(tmp_path, data))
    assert ei.value.field == "d_min_meters"


@given(st.floats(min_value=1e-6, max_value=1e6))
def test_any_positive_unknown_weight_accepted(x):
    from app.routing.cost import parse_weights

    data = _default_data()
    data["uncertainty"]["w_unknown_attr"] = x
    assert parse_weights(data).uncertainty["w_unknown_attr"] == x


def test_state_degrades_and_recovers(tmp_path):
    good = _default_data()
    p = _write(tmp_path, good)
    state = load_weights_state(p)
    assert state.status == "ok" and state.require() is state.weights

    bad = _default_data()
    bad["uncertainty"]["w_unknown_attr"] = 0
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    state.reload()
    assert state.status == "invalid_configuration"
    with pytest.raises(HTTPException) as ei:
        state.require()
    assert ei.value.status_code == 503
    assert ei.value.detail["file"] == "routing_weights.yaml"
    assert ei.value.detail["field"] == "uncertainty.w_unknown_attr"

    p.write_text(yaml.safe_dump(good), encoding="utf-8")
    assert state.reload().status == "ok"


def test_health_reports_routing_status(tmp_path):
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/health").json()["routing"] == "ok"

    app.state.routing_weights = load_weights_state(tmp_path / "missing.yaml")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["routing"] == "invalid_configuration"
