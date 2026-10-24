"""Property tests for Source_Catalog search and filter."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.api.routers.sources import router
from app.models.provenance import DataClassification, SourceRecord
from app.services import sources as svc

CLASSES = [c.value for c in DataClassification]
STATUSES = list(svc.PERMITTED_VERIFICATION_STATUSES)

# Mix of ASCII, case variants and non-ASCII letters (incl. casefold-sensitive ones).
words = st.text(
    alphabet=st.sampled_from(list("abcXYZ Uic-é É ß SS ı İ ǅ Ω ω 日本")),
    max_size=12,
)


@st.composite
def records(draw):
    n = draw(st.integers(0, 12))
    out = []
    for i in range(n):
        out.append(
            SourceRecord(
                source_id=f"s{i}",
                organization=draw(words),
                url="https://example.org/x",
                title=draw(words),
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                geographic_applicability="UIC",
                data_classification=draw(st.sampled_from(CLASSES)),
                verification_status=draw(st.sampled_from(STATUSES)),
                is_official_source=draw(st.booleans()),
            )
        )
    return out


def _vary_case(s: str, flip: bool) -> str:
    return s.upper() if flip else s.lower()


@st.composite
def scenarios(draw):
    recs = draw(records())
    # Queries: random text, or a substring of an existing field with case variation.
    if recs and draw(st.booleans()):
        r = draw(st.sampled_from(recs))
        src = draw(st.sampled_from([r.organization, r.title]))
        a = draw(st.integers(0, len(src)))
        b = draw(st.integers(a, len(src)))
        q = _vary_case(src[a:b], draw(st.booleans()))
    else:
        q = draw(words)
    q = draw(st.sampled_from([q, None])) if not q else q
    dc = draw(st.one_of(st.none(), st.sampled_from(CLASSES)))
    vs = draw(st.one_of(st.none(), st.sampled_from(STATUSES)))
    return recs, q, dc, vs


def _oracle(recs, q, dc, vs):
    needle = (q or "").strip().casefold()
    return [
        r
        for r in recs
        if (not needle or needle in r.organization.casefold() or needle in r.title.casefold())
        and (dc is None or r.data_classification.value == dc)
        and (vs is None or r.verification_status == vs)
    ]


# Feature: safepath-evacuation-navigation, Property 34: Source catalog search and filter
# **Validates: Requirements 10.2, 10.7**
@given(scenarios())
def test_search_matches_substring_and_filter_intersection(s):
    recs, q, dc, vs = s
    result = svc.search_sources(recs, q, dc, vs)
    assert result == _oracle(recs, q, dc, vs)
    # Stable order (subsequence of input) and idempotence.
    ids = [r.source_id for r in recs]
    assert [r.source_id for r in result] == [i for i in ids if i in {r.source_id for r in result}]
    assert svc.search_sources(result, q, dc, vs) == result
    # Intersection: combined == filter-by-each applied in sequence.
    step = svc.search_sources(svc.search_sources(svc.search_sources(recs, q), None, dc), None, None, vs)
    assert step == result


# Feature: safepath-evacuation-navigation, Property 34: Source catalog search and filter
# **Validates: Requirements 10.2, 10.7**
@given(records(), st.sampled_from([None, ""]))
def test_empty_query_returns_all_without_filters(recs, q):
    assert svc.search_sources(recs, q) == recs


# Feature: safepath-evacuation-navigation, Property 34: Source catalog search and filter
# **Validates: Requirements 10.2, 10.7**
@given(
    records(),
    st.text(max_size=12).filter(lambda t: t not in CLASSES),
    st.text(max_size=12).filter(lambda t: t not in STATUSES),
    st.booleans(),
)
def test_invalid_filter_values_rejected_with_permitted_list(recs, bad_dc, bad_vs, which):
    with pytest.raises(svc.InvalidFilterError) as ei:
        if which:
            svc.search_sources(recs, None, data_classification=bad_dc)
        else:
            svc.search_sources(recs, None, verification_status=bad_vs)
    msg = str(ei.value)
    permitted = CLASSES if which else STATUSES
    assert all(p in msg for p in permitted)


# Feature: safepath-evacuation-navigation, Property 34: Source catalog search and filter
# **Validates: Requirements 10.2, 10.7**
@given(scenarios(), st.text(max_size=8).filter(lambda t: t not in CLASSES))
def test_api_search_filter_and_400(s, bad):
    recs, q, dc, vs = s
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    original = svc.DEFAULT_CATALOG_PATH
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "catalog.json"
        path.write_text(
            json.dumps({"sources": [json.loads(r.model_dump_json()) for r in recs]}),
            encoding="utf-8",
        )
        svc.DEFAULT_CATALOG_PATH = path
        try:
            params = {k: v for k, v in
                      {"q": q, "data_classification": dc, "verification_status": vs}.items()
                      if v is not None}
            resp = client.get("/api/sources", params=params)
            assert resp.status_code == 200
            assert [r["source_id"] for r in resp.json()] == [
                r.source_id for r in _oracle(recs, q, dc, vs)
            ]
            bad_resp = client.get("/api/sources", params={"data_classification": bad})
            assert bad_resp.status_code == 400
            assert all(p in bad_resp.json()["detail"] for p in CLASSES)
        finally:
            svc.DEFAULT_CATALOG_PATH = original
