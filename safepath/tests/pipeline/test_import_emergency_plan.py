"""import_emergency_plan tests (Req 8.1-8.8, 7.1). No network; uses a recorded text fixture."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from scripts import import_emergency_plan as m  # noqa: E402

FIXTURE = json.loads((SAFEPATH / "tests/fixtures/cbd_plan/pages_excerpt.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fixture_pages() -> list[str]:
    """18-slot page list; only the recorded pages 2, 5 and 8 carry text."""
    pages = [""] * 18
    for k, v in FIXTURE["pages"].items():
        pages[int(k) - 1] = v
    return pages


def test_extracts_verbatim_with_page_and_heading():
    records, status = m.extract_topics(fixture_pages())
    boundary = next(r for r in records if r["topic"] == "cbd_boundaries")
    assert boundary["page"] == 2 and boundary["section_heading"] == "Purpose"
    assert "Division Street on the north" in boundary["excerpt"]
    zones = [r for r in records if r["topic"] == "hot_cold_zones"]
    assert zones and zones[0]["page"] == 5
    # the fixture omits page 4 ("Evacuation Levels" heading), so only the sub-heading is asserted
    assert zones[0]["section_heading"].endswith("Evacuation Level II")
    auth = next(r for r in records if r["topic"] == "activation_authority")
    assert auth["page"] == 8 and auth["section_heading"] == "Activation Protocols"
    # every excerpt is traceable to the page text it cites
    idx = {p["page"]: p["text"] for p in m.index_pages(fixture_pages())}
    assert all(r["excerpt"] in idx[r["page"]] for r in records)


def test_missing_topics_are_not_found_and_nothing_is_invented():
    records, status = m.extract_topics(fixture_pages())
    assert status["evacuation_level_III"]["status"] == m.NOT_FOUND  # page 6-7 not in fixture
    assert status["evacuation_level_III"]["record_ids"] == []
    assert not [r for r in records if r["topic"] == "evacuation_level_III"]
    assert status["temporary_shelter_locations"]["status"] == m.NOT_FOUND
    empty_records, empty_status = m.extract_topics([""] * 18)
    assert empty_records == [] and all(t["status"] == m.NOT_FOUND for t in empty_status.values())


def test_all_records_are_cbd_derived_historical_and_inactive():
    records, _ = m.extract_topics(fixture_pages())
    assert records
    for r in records:
        assert r["cbd_derived"] is True and r["active"] is False
        assert r["is_official_notice"] is False
        assert r["data_classification"] == "Historical"
    boundary = m.cbd_boundary_record(records)
    assert boundary and boundary["approximate"] is True
    assert (boundary["north"], boundary["south"], boundary["east"], boundary["west"]) == (
        "Division Street", "Roosevelt Road", "Lake Michigan", "Halsted Street")


def test_shelter_in_place_criteria_not_found_with_related_mentions_only():
    _, status = m.extract_topics(fixture_pages())
    t = status["shelter_in_place_vs_evacuation_criteria"]
    assert t["status"] == m.NOT_FOUND and t["record_ids"] == []


def test_search_records_not_located_and_unpopulated():
    recs = m.search_source_records(NOW)
    assert len(recs) == 5
    assert all(r.check_status == "not_located" and r.checked_at for r in recs)
    assert all(e["result"] == "not located" and e["content"] is None for e in m.search_entries())


def _run(tmp_path, fetcher, pages=None, **kw):
    local = tmp_path / "raw" / "cbd.pdf"
    return m.run(
        fetcher=fetcher,
        pdf_reader=lambda data: pages if pages is not None else fixture_pages(),
        now=NOW,
        local_pdf=local,
        output_path=tmp_path / "out" / "cbd_plan_2007.json",
        catalog_path=tmp_path / "out" / "catalog.json",
        fallbacks_path=tmp_path / "fallbacks.json",
        doc_path=tmp_path / "doc.md",
        **kw,
    ), local


def test_run_merges_catalog_and_fallbacks_without_clobbering(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "catalog.json").write_text(json.dumps({"sources": [{"source_id": "other"}]}))
    (tmp_path / "fallbacks.json").write_text(json.dumps({"download:x": {"a": 1}}))
    result, local = _run(tmp_path, lambda url, t: b"%PDF-fake")
    assert result["acquisition"] == "freshly downloaded" and local.exists()
    catalog = json.loads((tmp_path / "out" / "catalog.json").read_text())["sources"]
    ids = {s["source_id"] for s in catalog}
    assert {"other", m.PLAN_SOURCE_ID} <= ids and len(ids) == 7
    plan = next(s for s in catalog if s["source_id"] == m.PLAN_SOURCE_ID)
    assert plan["data_classification"] == "Historical" and plan["published_at"] == "2007-05-16"
    assert plan["reuse_determination"]["basis"]
    fb = json.loads((tmp_path / "fallbacks.json").read_text())
    assert fb["download:x"] == {"a": 1} and m.FALLBACK_KEY in fb
    # idempotent: re-running does not duplicate catalog entries
    _run(tmp_path, lambda url, t: b"%PDF-fake")
    assert len(json.loads((tmp_path / "out" / "catalog.json").read_text())["sources"]) == 7


def test_unreachable_reuses_previous_copy_then_records_unavailable(tmp_path):
    def boom(url, t):
        raise OSError("down")

    local = tmp_path / "raw" / "cbd.pdf"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"%PDF-old")
    result, _ = _run(tmp_path, boom)
    assert result["acquisition"] == "reused previous copy" and result["records"] > 0
    local.unlink()
    result, _ = _run(tmp_path, boom)
    assert result["unavailable"] is True
    fb = json.loads((tmp_path / "fallbacks.json").read_text())[m.FALLBACK_KEY]
    assert fb["acquisition_status"] == "missing" and fb["errors"]
    plan = next(s for s in json.loads((tmp_path / "out" / "catalog.json").read_text())["sources"]
                if s["source_id"] == m.PLAN_SOURCE_ID)
    assert plan["check_status"] == "unreachable"
