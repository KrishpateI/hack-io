"""Optional CTA transit alerts (Req 5.2-5.4) and Source_Catalog validity."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.repository import MemoryRepository
from app.config import Settings
from app.main import create_app
from app.models.provenance import SourceRecord
from app.services import sources as sources_svc
from app.services.transit import (
    UNAVAILABLE_MESSAGE, AlertsParseError, TransitAlertsService, join_stations, parse_alerts,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (ROOT / "tests" / "fixtures" / "cta" / "alerts_sample.json").read_text(encoding="utf-8")
STATIONS = [
    {"properties": {"id": "40540", "name": "Wilson", "routes": ["Red Line", "Purple Line"], "wheelchair_boarding": "accessible"}},
    {"properties": {"id": "40320", "name": "Division", "routes": ["Blue Line"]}},
]


class Fetcher:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


def svc(fetcher, repo=None, configured=True, clock=None):
    ticks = iter(f"2026-01-01T00:00:0{i}Z" for i in range(10))
    return TransitAlertsService(repo, fetcher, lambda: configured, clock or (lambda: next(ticks)))


def test_not_configured_is_unavailable_and_never_fetches():
    f = Fetcher(FIXTURE)
    r = svc(f, configured=False).get_alerts()
    assert r["available"] is False and r["message"] == UNAVAILABLE_MESSAGE == "Live transit alerts are unavailable"
    assert r["alerts"] == [] and f.calls == []


def test_success_labels_current_with_retrieval_timestamp_and_10s_timeout():
    f = Fetcher(FIXTURE)
    r = svc(f).get_alerts()
    assert r["available"] and not r["stale"] and r["data_classification"] == "Current"
    assert f.calls[0][1] == 10.0
    a = r["alerts"][0]
    assert a["data_classification"] == "Current" and a["retrieved_at"] == r["retrieved_at"] == "2026-01-01T00:00:00Z"
    assert a["alert_id"] == "118223" and a["routes"][0]["name"] == "Red Line"
    assert a["stations"] == [{"id": "40540", "name": "Wilson"}]
    assert "<p>" not in a["full_description"]


@pytest.mark.parametrize("failure", [socket.timeout("timed out"), TimeoutError(), OSError("down"), "not json", '{"x":1}'])
def test_failure_after_success_returns_stale_with_original_timestamp(failure):
    repo = MemoryRepository()
    s = svc(Fetcher(FIXTURE, failure), repo)
    first = s.get_alerts()
    second = s.get_alerts()
    assert second["stale"] is True and second["available"] is True
    assert second["retrieved_at"] == first["retrieved_at"] == "2026-01-01T00:00:00Z"
    assert second["alerts"] == first["alerts"]
    # a fresh process falls back to the Repository copy
    fresh = svc(Fetcher(failure), repo).get_alerts()
    assert fresh["stale"] is True and fresh["retrieved_at"] == first["retrieved_at"]


def test_failure_without_cache_is_unavailable():
    r = svc(Fetcher(socket.timeout("t"))).get_alerts()
    assert r["available"] is False and r["message"] == UNAVAILABLE_MESSAGE and r["alerts"] == []


def test_parse_errors():
    for bad in ["", "[]", '{"CTAAlerts": {"ErrorCode": "50", "ErrorMessage": "bad"}}', '{"CTAAlerts": {"Alert": [{}]}}']:
        with pytest.raises(AlertsParseError):
            parse_alerts(bad, "t")
    assert parse_alerts('{"CTAAlerts": {"ErrorCode": "0", "Alert": null}}', "t") == []


def test_single_alert_dict_and_single_service_dict_shapes():
    doc = json.loads(FIXTURE)
    alert = doc["CTAAlerts"]["Alert"][0]
    alert["ImpactedService"]["Service"] = alert["ImpactedService"]["Service"][1]
    doc["CTAAlerts"]["Alert"] = alert
    out = parse_alerts(doc, "t")
    assert len(out) == 1 and out[0]["routes"][0]["id"] == "Red" and out[0]["stations"] == []


def test_join_does_not_infer_entrance_safety_or_accessibility():
    alerts = parse_alerts(FIXTURE, "t")
    joined = join_stations(alerts, STATIONS)
    assert set(joined) == {"40540"}
    j = joined["40540"]
    assert j["entrance_safety_status"] == "Unknown" and "accessib" not in json.dumps({k: v for k, v in j.items() if k != "note"}).lower()
    # only an explicit official statement changes it
    official = join_stations(alerts, STATIONS, {"40540": "Closed per Official_Notice N-1"})
    assert official["40540"]["entrance_safety_status"] == "Closed per Official_Notice N-1"


def test_endpoint_unconfigured_and_configured(monkeypatch):
    monkeypatch.delenv("SAFEPATH_CTA_ALERTS_KEY", raising=False)
    monkeypatch.delenv("SAFEPATH_CTA_ALERTS_ENABLED", raising=False)
    app = create_app(Settings(profile="local"))
    r = TestClient(app).get("/api/transit/alerts").json()
    assert r["available"] is False and r["message"] == UNAVAILABLE_MESSAGE

    app.state.transit_alerts_service = TransitAlertsService(None, Fetcher(FIXTURE), lambda: True)
    r = TestClient(app).get("/api/transit/alerts").json()
    assert r["available"] and r["data_classification"] == "Current" and r["alerts"][0]["retrieved_at"]
    for st in r["station_alerts"].values():
        assert st["entrance_safety_status"] == "Unknown"


def test_every_catalog_record_validates_against_source_record():
    raw = json.loads((ROOT / "data" / "sources" / "catalog.json").read_text(encoding="utf-8"))
    for rec in raw["sources"] if isinstance(raw, dict) else raw:
        SourceRecord.model_validate(rec)
    assert sources_svc.load_catalog()
