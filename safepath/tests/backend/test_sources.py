"""Tests for the Source_Catalog service and GET /api/sources."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.main import app
from app.models.provenance import SourceRecord
from app.services import sources as svc


def _rec(i: int, org: str, title: str, cls: str = "Static", ver: str = "verified") -> SourceRecord:
    return SourceRecord(
        source_id=f"s{i}", organization=org, url="https://example.org", title=title,
        retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        geographic_applicability="Test", data_classification=cls,
        verification_status=ver, is_official_source=True,
    )


RECS = [
    _rec(1, "Cal Fire", "Evacuation Zones"),
    _rec(2, "UIC Police", "Campus Safety", "Current", "unverified"),
    _rec(3, "City Hall", "Shelter list", "Simulated", "simulated"),
]


def test_substring_case_insensitive():
    assert [r.source_id for r in svc.search_sources(RECS, q="CAL")] == ["s1"]
    assert [r.source_id for r in svc.search_sources(RECS, q="shelter")] == ["s3"]


def test_exact_filters():
    assert [r.source_id for r in svc.search_sources(RECS, data_classification="Current")] == ["s2"]
    assert [r.source_id for r in svc.search_sources(RECS, verification_status="simulated")] == ["s3"]


def test_invalid_filter_lists_permitted():
    with pytest.raises(svc.InvalidFilterError) as e:
        svc.search_sources(RECS, data_classification="bogus")
    assert "Historical" in str(e.value)


@given(st.text(max_size=10))
def test_results_match_query(q):
    needle = q.strip().casefold()
    for r in svc.search_sources(RECS, q=q):
        assert needle in r.organization.casefold() or needle in r.title.casefold()


def test_require_source_record():
    assert svc.require_source_record({"source_id": "s1"}, "rule") == "s1"
    with pytest.raises(svc.MissingSourceRecordError):
        svc.require_source_record({"source_id": " "}, "zone")
    with pytest.raises(svc.MissingSourceRecordError):
        svc.reject_unsourced([{"source_id": "a"}, {}], "shelter")


def test_endpoint():
    c = TestClient(app)
    r = c.get("/api/sources")
    assert r.status_code == 200 and isinstance(r.json(), list)
    bad = c.get("/api/sources", params={"verification_status": "nope"})
    assert bad.status_code == 400 and "verified" in bad.json()["error"]["message"]
    bad = c.get("/api/sources", params={"data_classification": "nope"})
    assert bad.status_code == 400 and "Static" in bad.json()["error"]["message"]
