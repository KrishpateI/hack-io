"""Official source review tests (Req 9.1-9.5, 9.7). Recorded fixtures only; no live network."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from scripts import review_official_sources as ros  # noqa: E402
from app.models.provenance import SourceRecord  # noqa: E402
from app.services.sources import load_catalog  # noqa: E402

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
REQUIRED_URLS = {
    "OEMC-CHICAGO": "https://www.chicago.gov/city/en/depts/oem.html",
    "UIC-EHSO-BUILDING-EAP": "https://ehso.uic.edu/fire-safety/building-specific-emergency-action-plans/",
    "UIC-READY": "https://ready.uic.edu/",
    "READY-ILLINOIS": "https://ready.illinois.gov/",
    "COOK-COUNTY-HMP": "https://www.cookcountyemergencymanagement.org/hmp",
}


def page(title: str, body: str) -> bytes:
    return (f"<html><head><title>{title}</title><script>var x='tornado'</script></head>"
            f"<body><nav>menu flood</nav><main>{body}</main></body></html>").encode()


FIXTURES: dict[str, bytes] = {
    REQUIRED_URLS["OEMC-CHICAGO"]: page("OEMC", "Alerts and <a href='/x'>Severe Weather</a> flood notices"),
    REQUIRED_URLS["UIC-EHSO-BUILDING-EAP"]: page(
        "UIC EAP", "Emergency Action Plan evacuation drawings, storm shelters and fire extinguisher locations"),
    REQUIRED_URLS["UIC-READY"]: page("UIC Ready", "Preparedness and alert resources"),
    REQUIRED_URLS["READY-ILLINOIS"]: page("Ready Illinois", "Plan, prepare, alerts"),
    REQUIRED_URLS["COOK-COUNTY-HMP"]: page("HMP", "Hazard Mitigation Plan for Cook County"),
    "https://ready.illinois.gov/hazards/tornadoes.html": page(
        "Tornadoes", "During a Tornado Warning go to an interior room. Stay away from windows. "
                     "Do not try to outrun a tornado in your car."),
    "https://ready.illinois.gov/hazards/floods.html": page("Floods", "Flash Flood Warning. Six inches of moving water can knock you down. "
                                                                  "Do not try to drive over a flooded road."),
    "https://ready.illinois.gov/disclaimer.html": page("Disclaimer", "State disclaimer."),
    "https://www.chicago.gov/city/en/general/disclaimer.html": page("Disclaimer", "City disclaimer."),
    "https://www.chicago.gov/city/en/depts/oem/provdrs/alertchicago.html": page("EP", "Emergency preparedness."),
    "https://www.chicago.gov/city/en/depts/oem/supp_info/how_to_prepare_yourfamilyforanemergency.html": page(
        "Prepare", "Meet somewhere if you have to evacuate. Be ready for at least 3 days. "
                   "Pick two separate escape routes and an out-of-area contact."),
}


def make_fetcher(down: set[str] | None = None, calls: list[str] | None = None,
                 fixtures: dict[str, bytes] | None = None):
    down = down or set()
    pages = FIXTURES if fixtures is None else fixtures

    def fetch(url: str, timeout: float) -> bytes:
        if calls is not None:
            calls.append((url, timeout))
        if url in down:
            raise TimeoutError("timed out")
        if url not in pages:
            raise OSError("HTTP Error 404")
        return pages[url]

    return fetch


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {"catalog_path": tmp_path / "catalog.json", "guidance_path": tmp_path / "guidance.json",
            "fallbacks_path": tmp_path / "fallbacks.json"}


def run(paths, fetcher, **kw):
    return ros.run(fetcher, catalog_path=paths["catalog_path"], guidance_path=paths["guidance_path"],
                   fallbacks_path=paths["fallbacks_path"], now=NOW, **kw)


def test_records_created_for_five_required_urls(paths):
    run(paths, make_fetcher())
    records = {r.source_id: r for r in load_catalog(paths["catalog_path"])}
    assert {r.source_id: r.url for r in records.values()} == REQUIRED_URLS
    assert all(r.check_status == "ok" and r.is_official_source for r in records.values())


def test_uic_reuse_determination_excludes_sensitive_content(paths):
    run(paths, make_fetcher())
    records = {r.source_id: r for r in load_catalog(paths["catalog_path"])}
    for sid in ("UIC-EHSO-BUILDING-EAP", "UIC-READY"):
        rd = records[sid].reuse_determination
        assert rd is not None and rd.approved_content
        text = " ".join(rd.excluded_content).lower()
        for term in ("access codes", "security procedures", "restricted-area", "floor plans"):
            assert term in text
    guidance = json.loads(paths["guidance_path"].read_text(encoding="utf-8"))
    assert guidance["uic"]["real_floor_plan_reuse_confirmed"] is False


def test_cook_hmp_is_long_term_mitigation(paths):
    run(paths, make_fetcher())
    rec = {r.source_id: r for r in load_catalog(paths["catalog_path"])}["COOK-COUNTY-HMP"]
    assert ros.LONG_TERM_MITIGATION in rec.usage_restrictions
    guidance = json.loads(paths["guidance_path"].read_text(encoding="utf-8"))
    assert guidance["cook_county_hmp"]["live_incident_boundary_allowed"] is False


def test_pages_are_actually_read_and_script_text_ignored(paths):
    result = run(paths, make_fetcher())
    insp = {i["source_id"]: i for i in result["inspections"]}
    assert insp["UIC-EHSO-BUILDING-EAP"]["topics_found"].count("building_plans") == 1
    # 'tornado' appears only inside <script> in every fixture page, so it must be ignored.
    assert "tornado" not in insp["OEMC-CHICAGO"]["topics_found"]
    assert insp["OEMC-CHICAGO"]["page_title"] == "OEMC"
    assert len(insp["OEMC-CHICAGO"]["content_sha256"]) == 64


def test_guidance_only_where_found_with_source_and_timestamp(paths):
    result = run(paths, make_fetcher())
    ids = {g["guidance_id"] for g in result["guidance"]["guidance"]}
    assert {"ready-illinois-tornado", "ready-illinois-flood", "oemc-family-preparedness"} <= ids
    # Earthquake/fire/shelter pages are absent from the fixtures: recorded as not found, not invented.
    assert "ready-illinois-earthquake" not in ids
    missing = {n["topic"] for n in result["guidance"]["not_found"]}
    assert {"ready-illinois-earthquake", "ready-illinois-fire-high-rise"} <= missing
    for g in result["guidance"]["guidance"]:
        assert g["source_url"].startswith("https://") and g["retrieved_at"] == NOW.isoformat()
    fb = json.loads(paths["fallbacks_path"].read_text(encoding="utf-8"))
    assert "official_sources:guidance:ready-illinois-earthquake" in fb


def test_guidance_requires_expected_phrases(paths):
    broken = dict(FIXTURES)  # private copy; shared FIXTURES is never mutated
    broken["https://ready.illinois.gov/hazards/tornadoes.html"] = page("Tornadoes", "Nothing useful here")
    result = run(paths, make_fetcher(fixtures=broken))
    nf = {n["topic"]: n for n in result["guidance"]["not_found"]}
    assert nf["ready-illinois-tornado"]["reason"] == "expected content not found on page"


def test_oemc_without_evacuation_procedure_is_recorded_not_invented(paths):
    result = run(paths, make_fetcher())
    evac = result["guidance"]["oemc_public_evacuation_procedures"]
    assert evac["status"] == "not_found" and evac["pages_read"] >= 1
    assert evac["incidental_mentions"]
    fb = json.loads(paths["fallbacks_path"].read_text(encoding="utf-8"))
    assert "official_sources:oemc_public_evacuation_procedures" in fb


def test_unreachable_url_recorded_and_prior_record_kept(paths):
    run(paths, make_fetcher())
    before = {r.source_id: r for r in load_catalog(paths["catalog_path"])}["UIC-READY"]
    later = datetime(2026, 2, 1, tzinfo=timezone.utc)
    calls: list = []
    down = {REQUIRED_URLS["UIC-READY"]}
    ros.run(make_fetcher(down, calls), catalog_path=paths["catalog_path"],
            guidance_path=paths["guidance_path"], fallbacks_path=paths["fallbacks_path"], now=later)
    after = {r.source_id: r for r in load_catalog(paths["catalog_path"])}["UIC-READY"]
    assert after.check_status == "unreachable" and after.checked_at == later
    assert after.retrieved_at == before.retrieved_at and after.verification_status == before.verification_status
    assert after.reuse_determination == before.reuse_determination
    assert all(t == 60 for _, t in calls)
    fb = json.loads(paths["fallbacks_path"].read_text(encoding="utf-8"))
    assert fb["official_sources:UIC-READY"]["status"] == "unreachable"


def test_unreachable_without_prior_record_still_recorded(paths):
    down = {REQUIRED_URLS["READY-ILLINOIS"]}
    run(paths, make_fetcher(down))
    rec = {r.source_id: r for r in load_catalog(paths["catalog_path"])}["READY-ILLINOIS"]
    assert rec.check_status == "unreachable" and rec.checked_at == NOW and rec.verification_status == "unknown"


def test_catalog_merge_preserves_other_records_and_is_idempotent(paths):
    other = SourceRecord(
        source_id="CBD-PLAN-2007", organization="City of Chicago", url="https://example.org/cbd.pdf",
        title="CBD plan", retrieved_at=NOW, geographic_applicability="Chicago CBD",
        data_classification="Historical", verification_status="verified", is_official_source=True)
    paths["catalog_path"].write_text(
        json.dumps({"sources": [json.loads(other.model_dump_json())]}), encoding="utf-8")
    run(paths, make_fetcher())
    run(paths, make_fetcher())
    ids = [r.source_id for r in load_catalog(paths["catalog_path"])]
    assert sorted(ids) == sorted(["CBD-PLAN-2007", *REQUIRED_URLS])


def test_fallbacks_preserve_other_keys_and_replace_own(paths):
    paths["fallbacks_path"].write_text(json.dumps(
        {"download:x": {"a": 1}, "official_sources:stale": {"b": 2}}), encoding="utf-8")
    run(paths, make_fetcher())
    fb = json.loads(paths["fallbacks_path"].read_text(encoding="utf-8"))
    assert fb["download:x"] == {"a": 1}
    assert "official_sources:stale" not in fb


def test_official_links_target_required_urls(paths):
    result = run(paths, make_fetcher())
    links = {l["label"]: l["url"] for l in result["guidance"]["official_links"]}
    assert links == {"Chicago OEMC": REQUIRED_URLS["OEMC-CHICAGO"],
                     "Ready Illinois": REQUIRED_URLS["READY-ILLINOIS"],
                     "UIC Ready": REQUIRED_URLS["UIC-READY"]}
