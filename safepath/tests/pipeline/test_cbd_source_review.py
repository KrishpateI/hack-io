"""Task 4.6: CBD extraction and source review unit tests (Req 8.4, 8.5, 8.7, 8.8, 9.3, 9.7).

Complements test_import_emergency_plan.py and test_review_official_sources.py. Recorded fixtures
only; no live network.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from scripts import import_emergency_plan as m  # noqa: E402
from scripts import review_official_sources as ros  # noqa: E402
from app.services.sources import load_catalog  # noqa: E402

from tests.pipeline.test_review_official_sources import (  # noqa: E402
    FIXTURES, REQUIRED_URLS, make_fetcher, page)

FIXTURE = json.loads((SAFEPATH / "tests/fixtures/cbd_plan/pages_excerpt.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Agency-role page laid out as pypdf extracts the plan's role pages (p. 9 in the real PDF):
# the "Chicago Police Department**" line starts the roles section, sub-headings are level names.
ROLE_PAGE = "\n".join([
    "City of Chicago", "Central Business District Evacuation Plan", "05-16-07", "9",
    "Chicago Police Department**",
    "Evacuation Level I",
    "§ Maintain civil order during the evacuation",
    "§ Provide traffic control during evacuation operations and throughout the CBD",
    "Evacuation Level II",
    "§ Provide area, perimeter, and facility security for the evacuated area",
    "Chicago Fire Department",
    "Evacuation Level I",
    "§ Investigate perceived hazmat threats and determine safe evacuation routes, as necessary",
])


def fixture_pages(extra: dict[int, str] | None = None) -> list[str]:
    pages = [""] * 18
    for k, v in FIXTURE["pages"].items():
        pages[int(k) - 1] = v
    for k, v in (extra or {}).items():
        pages[k - 1] = v
    return pages


def _run_plan(tmp_path: Path, fetcher, pages=None):
    out = tmp_path / "out"
    result = m.run(
        fetcher=fetcher,
        pdf_reader=lambda data: pages if pages is not None else fixture_pages(),
        now=NOW,
        local_pdf=tmp_path / "raw" / "cbd.pdf",
        output_path=out / "cbd_plan_2007.json",
        catalog_path=out / "catalog.json",
        fallbacks_path=tmp_path / "fallbacks.json",
        doc_path=tmp_path / "doc.md",
    )
    return result, out


# ---------------------------------------------------------------- CBD page/section capture (8.4, 8.5)
def test_section_heading_changes_within_a_page():
    records, _ = m.extract_topics(fixture_pages())
    # page 8: activation text sits under "Activation Protocols", EAS under "Emergency Public Information"
    activation = next(r for r in records if r["excerpt"].startswith("All critical City departments"))
    eas = next(r for r in records if r["excerpt"].startswith("Emergency Alert System (EAS)"))
    assert (activation["page"], activation["section_heading"]) == (8, "Activation Protocols")
    assert (eas["page"], eas["section_heading"]) == (8, "Emergency Public Information")
    assert all(isinstance(r["page"], int) and r["section_heading"] for r in records)


def test_multiline_needle_is_located_across_line_breaks():
    records, _ = m.extract_topics(fixture_pages())
    iema = next(r for r in records if r["excerpt"].startswith("If necessary, the Illinois Emergency"))
    assert iema["page"] == 8 and iema["topic"] == "activation_authority"


def test_agency_role_headings_captured():
    records, status = m.extract_topics(fixture_pages({9: ROLE_PAGE}))
    police = [r for r in records if r["topic"] == "role_police" and r["page"] == 9]
    assert {r["section_heading"] for r in police} == {
        "Chicago Police Department / Evacuation Level I",
        "Chicago Police Department / Evacuation Level II",
    }
    fire = next(r for r in records if r["topic"] == "role_fire")
    assert fire["section_heading"] == "Chicago Fire Department / Evacuation Level I"
    assert status["role_fire"]["status"] == "found"


def test_record_ids_unique_and_status_lists_match_records():
    records, status = m.extract_topics(fixture_pages({9: ROLE_PAGE}))
    ids = [r["record_id"] for r in records]
    assert len(ids) == len(set(ids))
    for topic, t in status.items():
        assert t["record_ids"] == [r["record_id"] for r in records if r["topic"] == topic]


# ---------------------------------------------------------------- not-found topics (8.4)
def test_every_listed_topic_has_a_status_and_absent_ones_are_not_found():
    _, status = m.extract_topics(fixture_pages())
    assert set(status) == {t["topic"] for t in m.TOPICS}
    for topic in ("assembly_transportation_center_locations", "temporary_shelter_locations",
                  "role_fire", "cta_metra_coordination"):
        assert status[topic]["status"] == m.NOT_FOUND and status[topic]["reason"]


def test_boundary_not_inferred_when_sentence_absent():
    pages = fixture_pages()
    pages[1] = ""  # drop page 2 (the boundary sentence)
    records, status = m.extract_topics(pages)
    assert status["cbd_boundaries"]["status"] == m.NOT_FOUND
    assert m.cbd_boundary_record(records) is None


# ---------------------------------------------------------------- unavailable appendices (8.7, 8.8)
def test_run_records_appendix_searches_unpopulated(tmp_path):
    _run_plan(tmp_path, lambda url, t: b"%PDF-fake")
    output = json.loads((tmp_path / "out" / "cbd_plan_2007.json").read_text(encoding="utf-8"))
    assert len(output["searches"]) == len(m.APPENDIX_SEARCHES)
    for s in output["searches"]:
        assert s["search_date"] == m.SEARCH_DATE and s["result"] == "not located"
        assert s["unavailable"] is True and s["url"] is None and s["content"] is None
    assert any("newer version" in s["item"].lower() for s in output["searches"])
    assert any("appendices" in n.lower() for n in output["not_extracted"])
    catalog = {s["source_id"]: s for s in json.loads((tmp_path / "out" / "catalog.json").read_text())["sources"]}
    for s in output["searches"]:
        rec = catalog[s["source_id"]]
        assert rec["check_status"] == "not_located" and m.SEARCH_DATE in rec["page_or_section"]
    doc = (tmp_path / "doc.md").read_text(encoding="utf-8")
    assert doc.count("Content left unpopulated") == len(m.APPENDIX_SEARCHES)


def test_unavailable_plan_keeps_prior_extraction_untouched(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    prior = out / "cbd_plan_2007.json"
    prior.write_text('{"prior": true}', encoding="utf-8")

    def down(url, t):
        raise TimeoutError("timed out")

    result, _ = _run_plan(tmp_path, down)
    assert result == {"acquisition": "missing", "records": 0, "unavailable": True}
    assert json.loads(prior.read_text(encoding="utf-8")) == {"prior": True}
    catalog = json.loads((out / "catalog.json").read_text())["sources"]
    assert sum(s["check_status"] == "not_located" for s in catalog) == len(m.APPENDIX_SEARCHES)


# ---------------------------------------------------------------- UIC exclusions (9.3)
SECRET_CODE = "Door keypad access code 4471#"
FLOOR_PLAN_URL = "https://ehso.uic.edu/wp-content/uploads/sec-floor-plan-SEO-3.pdf"
RESTRICTED = "Restricted area: steam tunnel entrance behind loading dock B"


def _uic_fixtures() -> dict[str, bytes]:
    fx = dict(FIXTURES)
    fx[REQUIRED_URLS["UIC-EHSO-BUILDING-EAP"]] = page(
        "UIC EAP",
        f"Emergency Action Plan evacuation drawings. {SECRET_CODE}. {RESTRICTED}. "
        f"<a href='{FLOOR_PLAN_URL}'>SEO floor plan</a>")
    return fx


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {"catalog_path": tmp_path / "catalog.json", "guidance_path": tmp_path / "guidance.json",
            "fallbacks_path": tmp_path / "fallbacks.json"}


def test_uic_sensitive_page_content_never_written(tmp_path):
    p = _paths(tmp_path)
    result = ros.run(make_fetcher(fixtures=_uic_fixtures()), now=NOW, **p)
    written = "".join(path.read_text(encoding="utf-8") for path in p.values())
    for leaked in ("4471", "keypad", FLOOR_PLAN_URL, "steam tunnel", "loading dock"):
        assert leaked not in written
    insp = next(i for i in result["inspections"] if i["source_id"] == "UIC-EHSO-BUILDING-EAP")
    assert insp["status"] == "ok" and "building_plans" in insp["topics_found"]
    guidance = json.loads(p["guidance_path"].read_text(encoding="utf-8"))
    assert guidance["uic"]["building_emergency_module_plan"] == "fictional"
    assert not [g for g in guidance["guidance"] if g["source_id"].startswith("UIC")]


# ---------------------------------------------------------------- unreachable URLs (9.7)
def test_unreachable_after_retries_records_attempts_and_errors(tmp_path):
    p = _paths(tmp_path)
    calls: list = []
    url = REQUIRED_URLS["COOK-COUNTY-HMP"]
    result = ros.run(make_fetcher({url}, calls), now=NOW, **p)
    insp = next(i for i in result["inspections"] if i["source_id"] == "COOK-COUNTY-HMP")
    assert insp["status"] == "unreachable" and insp["attempts"] == 3 and insp["errors"]
    assert [t for u, t in calls if u == url] == [60, 60, 60]
    rec = {r.source_id: r for r in load_catalog(p["catalog_path"])}["COOK-COUNTY-HMP"]
    assert rec.check_status == "unreachable" and rec.checked_at == NOW
    # other sources are unaffected
    assert all(r.check_status == "ok" for r in load_catalog(p["catalog_path"]) if r.source_id != "COOK-COUNTY-HMP")


def test_prior_guidance_retained_when_its_page_becomes_unreachable(tmp_path):
    p = _paths(tmp_path)
    ros.run(make_fetcher(), now=NOW, **p)
    later = datetime(2026, 3, 1, tzinfo=timezone.utc)
    tornado = "https://ready.illinois.gov/hazards/tornadoes.html"
    result = ros.run(make_fetcher({tornado}), now=later, **p)
    kept = next(g for g in result["guidance"]["guidance"] if g["guidance_id"] == "ready-illinois-tornado")
    assert kept["retrieved_at"] == NOW.isoformat()  # original capture, not re-stamped
    nf = {n["topic"]: n for n in result["guidance"]["not_found"]}
    assert nf["ready-illinois-tornado"]["reason"] == "page unreachable"
