"""Import the 2007 Chicago Central Business District (CBD) Evacuation Plan (Req 8.1-8.8, 7.1).

What it does
------------
1. Downloads the CBD_Plan PDF (3 attempts x 60 s via the shared fetch layer).  On failure it reuses
   the previous local copy, or records the plan as unavailable; either way the run continues and the
   fallback is written to ``data/processed/fallbacks.json`` under the ``emergency_plan:`` prefix.
2. Extracts per-page text with ``pypdf`` (optional extra ``pdf``) and builds structured records.
   Every record is a SHORT VERBATIM EXCERPT located in the page text (whitespace-normalized) together
   with the PDF page number and section heading.  A topic whose excerpts are not found in the PDF text
   is recorded as ``not found``; nothing is inferred or invented.
3. All records carry ``cbd_derived=true``, ``active=false`` and ``is_official_notice=false``
   (Req 8.9: never evaluated as active Official_Notices) and a Historical classification.
4. Writes ``data/sources/cbd_plan_2007.json``, merges Source_Records (plan + search records) into
   ``data/sources/catalog.json`` (read-merge-write keyed by ``source_id``, atomic replace) and writes
   ``docs/cbd-plan-extraction.md``.

Local-copy determination (Req 8.2): the PDF is a public City of Chicago document with no copyright,
license or reuse statement in its text, and no explicit reuse licence was found on chicago.gov.  The
file is therefore kept only as a git-ignored cache under ``data/raw/cbd_plan/`` (not committed, not
redistributed); the repository stores short attributed excerpts with page references.

Run (from the ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\import_emergency_plan.py
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from app.models.provenance import (  # noqa: E402
    DataClassification,
    ReuseDetermination,
    SourceRecord,
)
from pipeline.fetch import Fetcher, fetch_with_retries, urllib_fetcher  # noqa: E402

PLAN_URL = "https://webapps1.chicago.gov/ChicagoAlertWeb/resources/pdf/CBD_Evac_Plan_final_30-5-07.pdf"
PLAN_SOURCE_ID = "cbd_plan_2007"
PLAN_TITLE = "City of Chicago Central Business District Evacuation Plan (2007)"
PLAN_ORG = "City of Chicago Office of Emergency Management & Communications (OEMC)"
PLAN_DATE = date(2007, 5, 16)  # "05-16-07" in every page header; cover says "Additions: 29 May 07"
HISTORICAL_LABEL = "Historical reference (2007), not a current evacuation order"
FALLBACK_PREFIX = "emergency_plan:"
FALLBACK_KEY = FALLBACK_PREFIX + PLAN_SOURCE_ID
NOT_FOUND = "not found"

RAW_DIR = ROOT / "data" / "raw" / "cbd_plan"
LOCAL_PDF = RAW_DIR / "cbd_evac_plan_2007.pdf"
OUTPUT_PATH = ROOT / "data" / "sources" / "cbd_plan_2007.json"
CATALOG_PATH = ROOT / "data" / "sources" / "catalog.json"
FALLBACKS_PATH = ROOT / "data" / "processed" / "fallbacks.json"
DOC_PATH = ROOT / "docs" / "cbd-plan-extraction.md"

LOCAL_COPY_DETERMINATION = (
    "No copyright, licence or reuse statement appears in the PDF text, and no explicit reuse licence "
    "was found for it on chicago.gov (checked via web search). Storage permission is therefore NOT "
    "confirmed: the PDF is kept only as a git-ignored local cache (data/raw/cbd_plan/) for extraction "
    "and is never committed or redistributed. Only short attributed excerpts with page references are "
    "stored under data/sources/."
)

# --------------------------------------------------------------------------------------------
# Search records for appendices / newer versions (Req 8.7, 8.8).  Searches were run on this date
# with the web-search queries listed; none located a public copy.
# --------------------------------------------------------------------------------------------
SEARCH_DATE = "2026-10-24"
SEARCH_ORG_URL = "https://www.chicago.gov/city/en/depts/oem.html"
APPENDIX_SEARCHES: list[dict[str, str]] = [
    {
        "key": "appendix_cpd",
        "item": "CPD Central Business District Evacuation Deployment Plan (Appendix)",
        "query": 'Chicago OEMC Central Business District Evacuation Plan appendix CPD CTA IDOT deployment plan; '
        '"CTA Central Business District Evacuation Plan" OR "CPD Central Business District Evacuation Deployment Plan" Chicago',
    },
    {
        "key": "appendix_oemc",
        "item": "OEMC Central Business District Evacuation Deployment Plan (Appendix)",
        "query": 'Chicago OEMC Central Business District Evacuation Plan appendix CPD CTA IDOT deployment plan',
    },
    {
        "key": "appendix_cta",
        "item": "CTA Central Business District Evacuation Plan (Appendix)",
        "query": '"CTA Central Business District Evacuation Plan" OR "CPD Central Business District Evacuation Deployment Plan" Chicago',
    },
    {
        "key": "appendix_idot",
        "item": "State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System (IDOT Appendix)",
        "query": '"State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System" IDOT',
    },
    {
        "key": "newer_version",
        "item": "Newer version of the Chicago CBD Evacuation Plan (after 2007)",
        "query": 'chicago.gov OEMC "Central Business District Evacuation Plan" newer version',
    },
]
SEARCH_RESULT = (
    "not located: searches returned only the 2007 plan itself (plus unrelated chicago.gov and IDOT pages); "
    "no public copy of this item was found"
)

# --------------------------------------------------------------------------------------------
# Topic table.  Each needle is a verbatim string expected in the PDF text (whitespace-normalized).
# kind: description | condition | event_example | statement.
# A needle that is not located is dropped; a topic with no located needle is recorded as not found.
# --------------------------------------------------------------------------------------------
Needle = tuple[str, str]

TOPICS: list[dict[str, Any]] = [
    {
        "topic": "evacuation_level_I",
        "title": "Evacuation Level I and activation conditions",
        "needles": [
            ("description", "A Level I evacuation is similar to a rush-hour egress. There is no immediate danger."),
            ("description", "Level I requires local resources already in place throughout the CBD, with an increase in CTA and Metra services."),
            ("condition", "No casualties or direct threat to life"),
            ("condition", "No incident site or direct threat to life"),
            ("condition", "No public panic"),
            ("condition", "No public announcement of an emergency"),
            ("event_example", "CBD Power Outage"),
            ("event_example", "Perceived threat to Chicago"),
        ],
    },
    {
        "topic": "evacuation_level_II",
        "title": "Evacuation Level II and activation conditions",
        "needles": [
            ("description", "A Level II evacuation requires additional resources from outside the CBD, and requires establishing hot zone and cold zone perimeters."),
            ("description", "At this level, the Joint Operations Center will be activated."),
            ("condition", "Existent, identifiable, containable threat to life/casualties (not chemical or biological)"),
            ("condition", "Limited Casualties"),
            ("condition", "Clear Incident Perimeter"),
            ("condition", "Public confusion/Imperfect Information"),
            ("condition", "Limited self-evacuation"),
            ("event_example", "Single building catastrophic event"),
            ("event_example", "Major gas leak or fire"),
            ("event_example", "Large fire or explosion (accidental or intentional)"),
        ],
    },
    {
        "topic": "evacuation_level_III",
        "title": "Evacuation Level III and activation conditions",
        "needles": [
            ("description", "A Level III evacuation will require extensive support from outside agencies and resources."),
            ("description", "Events necessitating a Level III evacuation will result in a large number of spontaneous evacuees."),
            ("description", "Transportation and mass care resources will be activated as well as local, regional and state law enforcement and first responder assets."),
            ("condition", "Secondary incident after any level I or II event"),
            ("condition", "Severe damage"),
            ("condition", "General, widespread confusion"),
            ("condition", "No transportation (personal or public) movement in the CBD"),
            ("condition", "Limited ingress/egress for emergency vehicles"),
            ("condition", "High number of casualties"),
            ("condition", "CBRN or hazmat"),
            ("condition", "Extensive need for temporary sheltering"),
            ("condition", "Extensive spontaneous evacuation"),
            ("event_example", "September 11, 2001 terrorist attacks"),
            ("event_example", "Oklahoma City bombing"),
            ("event_example", "Detection of CBRN agents in the CBD"),
            ("event_example", "Multiple vehicle bombs"),
            ("event_example", "Large HazMat of unknown origin"),
        ],
    },
    {
        "topic": "shelter_in_place_vs_evacuation_criteria",
        "title": "Shelter-in-place versus evacuation criteria",
        # The plan mentions shelter-in-place but states no criteria for choosing it over evacuation.
        # The mentions are kept as related_excerpts only; the topic itself is recorded as not found.
        "criteria_topic": True,
        "needles": [
            ("related_mention", "Notify people outside the immediate danger area to shelter-in-place and/or other appropriate actions to take until notified by City agencies."),
            ("related_mention", "In case of a major emergency that will require evacuation or sheltering-in-place, instructions will be provided to callers through the telephone bank."),
        ],
    },
    {
        "topic": "activation_authority",
        "title": "Activation and evacuation authority",
        "needles": [
            ("statement", "The Mayor, through OEMC, initiates activation of this plan."),
            ("statement", "The Mayor of the City of Chicago may recommend an evacuation, rather than require one."),
            ("statement", "provides the Governor of Illinois the emergency powers to:"),
            ("statement", "Recommend the evacuation of all or part of the population from any stricken or threatened area within the State;"),
            ("statement", "If necessary, the Illinois Emergency Management Agency (IEMA) will be notified by the OEMC of the activation"),
        ],
    },
    {
        "topic": "cbd_boundaries",
        "title": "CBD boundaries",
        "needles": [
            ("statement", "the boundaries of which are Division Street on the north, Roosevelt Road on the south, Lake Michigan on the east, and Halsted Street on the west."),
        ],
    },
    {
        "topic": "hot_cold_zones",
        "title": "Hot and cold zone definitions",
        "needles": [
            ("statement", "The hot zone will be used for safety and security during an ongoing event, and the cold zone will designate a broader access control area to insulate the hot zone perimeter."),
            ("statement", "A large outer perimeter will be established, as well as a fluid inner perimeter."),
            ("statement", "Establish hot zone and cold zone perimeters, modifying perimeters as necessary"),
        ],
    },
    {
        "topic": "transportation_coordination",
        "title": "Transportation coordination",
        "needles": [
            ("statement", "Deployment and diversion of transportation assets will be based upon time of day, type of event, and location."),
            ("statement", "In coordination with the CTA Central Business District Evacuation Plan, ACTs are located so that available transportation assets can be directed toward these locations to further assist evacuees."),
            ("statement", "Coordinate and modify participating department and agency response activities and incident perimeters, evacuation area and routes, and transportation system modifications"),
        ],
    },
    {
        "topic": "road_and_expressway_closure_procedures",
        "title": "Road and expressway closure procedures",
        "needles": [
            ("statement", "The outer perimeter may include closure or redirection of area expressways, and main arterial streets."),
            ("statement", "Deploy assets as available or instructed to close downtown expressway ramps"),
            ("statement", "Deploy assets as available for closure of expressway ramps, per the State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System"),
            ("statement", "Initiate and maintain expressway/highway closures and express lane reversals as described in the State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System (IDOT Appendix), as necessary"),
            ("statement", "Utilize or re-designate existing CBD personnel to assist with street closures, or towing, as necessary"),
            ("statement", "Deploy barricades for traffic/crowd control"),
        ],
    },
    {
        "topic": "cta_metra_coordination",
        "title": "CTA and Metra coordination",
        "needles": [
            ("statement", "Coordinate alterations in schedule with Metra, OEMC, and CPD"),
            ("statement", "Initiate Level 3 of the CTA Central Business District Emergency Evacuation Plan"),
            ("statement", "Continue alternate train boarding and bus re-routes as necessary, if downtown stations are inoperable or streets are impassable"),
            ("statement", "Initiate Level 1 Evacuation of the Metra Emergency Response Plan to System Incidents"),
            ("statement", "Initiate Level 2 Evacuation of the Metra Emergency Response Plan to System Incidents"),
            ("statement", "Continue alternate boarding if downtown stations are inoperable"),
        ],
    },
    {
        "topic": "assembly_transportation_centers",
        "title": "Assembly and Transportation Centers (ATC)",
        "needles": [
            ("statement", "Assembly & Transportation Centers (ATC) may be activated by the OEMC"),
            ("statement", 'An ATC is an area or building designated to serve as a "way station" for evacuees.'),
            ("statement", "It is used to organize and link up pedestrian evacuees with public transportation assets, to provide basic first aid and water, to perform limited decontamination, and to determine if evacuees need overnight shelter."),
            ("statement", "CPD and other law enforcement personnel will provide security at these facilities."),
        ],
    },
    {
        "topic": "assembly_transportation_center_locations",
        "title": "Specific ATC locations",
        "needles": [],  # the plan text names no ATC locations
    },
    {
        "topic": "temporary_shelters",
        "title": "Temporary shelters",
        "needles": [
            ("statement", "Depending on the emergency, many evacuees may not be able to return to their homes quickly after an evacuation of the CBD."),
            ("statement", "Existing shelters in and around the City of Chicago (hotels, motels or community centers previously designated by the Chicago Department of Human Services, the American Red Cross of Greater Chicago, the Salvation Army and other agencies) will be activated to accommodate these evacuees."),
            ("statement", "Evaluate overnight shelter needs and initiate shelter activation as necessary"),
        ],
    },
    {
        "topic": "temporary_shelter_locations",
        "title": "Specific temporary shelter locations",
        "needles": [],  # the plan text names no shelter addresses
    },
    {
        "topic": "communication_procedures",
        "title": "Communication procedures",
        "needles": [
            ("statement", "All critical City departments and sister agencies will be notified via the OEMC Operations Center, supported by 311, when the decision to activate the Plan is made."),
            ("statement", "The OEMC is the coordinating agency for all emergency public information and is responsible for the development and dissemination of a single message through the Joint Information Center (JIC)."),
            ("statement", "Emergency Alert System (EAS): This system will send a message to all EAS subscriber TV and radio networks in the Chicago market."),
            ("statement", "Emergency Telephone Notification System (ETNS): This system will send a recorded message to telephones in a geographically specified area or to a pre-programmed list of contacts."),
            ("statement", "Emergency Information Telephone Bank (877-745-INFO)"),
            ("statement", "Emergency Alert Radio Network"),
        ],
    },
    {
        "topic": "role_oemc",
        "title": "Agency role: OEMC",
        "needles": [
            ("statement", "Coordinating Agency: Office of Emergency Management & Communications"),
            ("statement", "OEMC is responsible for maintaining this plan"),
            ("statement", "Monitor the incident and the potential threat to the area"),
            ("statement", "Through the Operations Center (OC), notify all affected city, state, and federal agencies"),
            ("statement", "Activate and operate the Joint Operations Center, if necessary"),
        ],
    },
    {
        "topic": "role_police",
        "title": "Agency role: Chicago Police Department",
        "needles": [
            ("statement", "Primary Agency: Chicago Police Department"),
            ("statement", "Maintain civil order during the evacuation"),
            ("statement", "Provide traffic control during evacuation operations and throughout the CBD"),
            ("statement", "Provide area, perimeter, and facility security for the evacuated area"),
        ],
    },
    {
        "topic": "role_fire",
        "title": "Agency role: Chicago Fire Department",
        "needles": [
            ("statement", "Investigate perceived hazmat threats and determine safe evacuation routes, as necessary"),
            ("statement", "Establish a multi-purpose aid station(s) along evacuation route(s) as needed"),
            ("statement", "Provide medical services at ATCs as needed"),
        ],
    },
    {
        "topic": "role_transportation",
        "title": "Agency role: transportation agencies (CDOT, IDOT)",
        "needles": [
            ("statement", "Assist with street closures as necessary"),
            ("statement", "Ensure coordination with the OEMC and CPD throughout event"),
            ("statement", "Initiate full activation of the State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System"),
        ],
    },
]

# Section headings recognised as whole lines of page text (before the per-agency role pages).
TOP_HEADINGS = (
    "Participating Agencies",
    "Purpose",
    "Evacuation Authority",
    "Evacuation Objectives",
    "Assembly & Transportation Centers",
    "Sheltering",
    "Evacuation Levels",
    "Activation Protocols",
    "Emergency Public Information",
    "Plan Appendices",
)
ROLES_START_LINE = "Chicago Police Department**"
AGENCY_HEADINGS = frozenset({
    "Chicago Police Department",
    "Chicago Office of Emergency Management and Communications",
    "Chicago Fire Department",
    "Chicago Office of the Mayor / Mayor¶s Press Office",
    "Chicago Department of Streets & Sanitation",
    "Chicago Department of Transportation",
    "Chicago Department of Fleet Management",
    "Chicago Department on Aging",
    "Chicago Mayor¶s Office for People with Disabilities",
    "Chicago Department of Human Services",
    "Chicago Transit Authority",
    "Metra",
    "Illinois Department of Transportation",
    "Illinois State Police",
    "Illinois Emergency Management Agency",
    "Cook County Sheriff¶s Police",
    "Cook County Emergency Management Agency",
    "American Red Cross",
    "The Salvation Army",
})

PdfPageReader = Callable[[bytes], "list[str]"]


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------------------------
# Extraction core (pure; unit-tested with a recorded fixture)
# --------------------------------------------------------------------------------------------
def read_pdf_pages(data: bytes) -> list[str]:
    """Per-page text using pypdf (optional extra ``pdf``)."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "pypdf is required: pip install -e \"backend[pdf]\" (or pip install pypdf==6.1.1)"
        ) from exc
    reader = PdfReader(io.BytesIO(data))
    return [(page.extract_text() or "") for page in reader.pages]


def index_pages(pages: list[str]) -> list[dict[str, Any]]:
    """Normalize each page and record section-heading state at every line offset."""
    roles_started = False
    major = ""
    sub = ""
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(pages, start=1):
        parts: list[str] = []
        marks: list[tuple[int, str]] = []  # (offset in normalized text, heading at that offset)
        offset = 0
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if s == ROLES_START_LINE:
                roles_started = True
            clean = s.rstrip("*").strip()
            is_bullet = s[:1] in "§·"
            if s in TOP_HEADINGS:
                major, sub = s, ""
            elif roles_started and not is_bullet and clean in AGENCY_HEADINGS:
                major, sub = clean, ""
            elif s.startswith("Evacuation Level") and len(s) < 40:
                sub = s
            heading = major + (f" / {sub}" if sub and major else sub)
            norm_line = normalize_ws(s)
            marks.append((offset, heading))
            parts.append(norm_line)
            offset += len(norm_line) + 1
        out.append({"page": i, "text": " ".join(parts), "marks": marks})
    return out


def heading_at(page: dict[str, Any], index: int) -> str:
    heading = ""
    for off, h in page["marks"]:
        if off <= index:
            heading = h
        else:
            break
    return heading


def locate(pages_idx: list[dict[str, Any]], needle: str) -> tuple[int, str] | None:
    """First (page, heading) where ``needle`` occurs verbatim in a page's normalized text."""
    n = normalize_ws(needle)
    for p in pages_idx:
        pos = p["text"].find(n)
        if pos >= 0:
            return p["page"], heading_at(p, pos)
    return None


def extract_topics(pages: list[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return (records, topic_status).  Records are verbatim excerpts with page and heading."""
    pages_idx = index_pages(pages)
    records: list[dict[str, Any]] = []
    status: dict[str, dict[str, Any]] = {}
    for t in TOPICS:
        found: list[dict[str, Any]] = []
        related: list[dict[str, Any]] = []
        for kind, needle in t["needles"]:
            hit = locate(pages_idx, needle)
            if hit is None:
                continue
            page, heading = hit
            rec = {
                "record_id": f"{PLAN_SOURCE_ID}:{t['topic']}:{len(found) + len(related) + 1:02d}",
                "topic": t["topic"],
                "kind": kind,
                "excerpt": normalize_ws(needle),
                "excerpt_type": "verbatim",
                "page": page,
                "section_heading": heading or None,
                **_derived_flags(),
            }
            (related if t.get("criteria_topic") else found).append(rec)
        if t.get("criteria_topic"):
            # Criteria are not stated in the plan: topic is `not found`; mentions are context only.
            status[t["topic"]] = {
                "title": t["title"],
                "status": NOT_FOUND,
                "reason": "The plan names shelter-in-place but states no criteria for choosing it over evacuation.",
                "related_excerpts": related,
                "record_ids": [],
            }
            continue
        records.extend(found)
        status[t["topic"]] = {
            "title": t["title"],
            "status": "found" if found else NOT_FOUND,
            "record_ids": [r["record_id"] for r in found],
        }
        if not found:
            status[t["topic"]]["reason"] = "No matching text located in the plan PDF."
    return records, status


def _derived_flags() -> dict[str, Any]:
    return {
        "source_id": PLAN_SOURCE_ID,
        "cbd_derived": True,
        "active": False,
        "is_official_notice": False,
        "data_classification": DataClassification.HISTORICAL.value,
        "label": HISTORICAL_LABEL,
    }


def cbd_boundary_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Approximate CBD boundary (Req 8.6), only when the plan's boundary sentence was located."""
    src = next((r for r in records if r["topic"] == "cbd_boundaries"), None)
    if src is None:
        return None
    return {
        "name": "Chicago CBD (approximate)",
        "approximate": True,
        "north": "Division Street",
        "south": "Roosevelt Road",
        "east": "Lake Michigan",
        "west": "Halsted Street",
        "approximation_note": "Boundary as described in prose by the 2007 plan; no coordinates or map are given, so the area is approximate.",
        "page": src["page"],
        "section_heading": src["section_heading"],
        "excerpt_record_id": src["record_id"],
        **_derived_flags(),
    }


# --------------------------------------------------------------------------------------------
# Source records
# --------------------------------------------------------------------------------------------
def plan_source_record(now: datetime, *, available: bool, check_status: str) -> SourceRecord:
    return SourceRecord(
        source_id=PLAN_SOURCE_ID,
        organization=PLAN_ORG,
        url=PLAN_URL,
        title=PLAN_TITLE,
        published_at=PLAN_DATE,
        retrieved_at=now,
        geographic_applicability="Chicago Central Business District (approx. Division St to Roosevelt Rd, Halsted St to Lake Michigan)",
        data_classification=DataClassification.HISTORICAL,
        verification_status="unverified",
        page_or_section="Pages 1-18 (see per-record page and section_heading)",
        usage_restrictions=[
            HISTORICAL_LABEL,
            "cbd_derived: never evaluated as an active Official_Notice",
            "Local copy storage permission not confirmed; PDF kept only as git-ignored cache (data/raw/cbd_plan/)",
            "Plan date 05-16-07 (cover: Additions 29 May 07); contact details and procedures may be out of date",
        ],
        is_official_source=True,
        reuse_determination=ReuseDetermination(
            approved_content=["Short attributed excerpts with PDF page and section heading"],
            excluded_content=["Redistribution of the full PDF or its pages in the repository"],
            determined_at=now,
            basis=LOCAL_COPY_DETERMINATION,
        ),
        check_status="ok" if available else "unreachable",
        checked_at=now,
    )


def search_source_records(now: datetime) -> list[SourceRecord]:
    day = datetime.fromisoformat(SEARCH_DATE + "T00:00:00+00:00")
    out: list[SourceRecord] = []
    for s in APPENDIX_SEARCHES:
        out.append(
            SourceRecord(
                source_id=f"{PLAN_SOURCE_ID}_search_{s['key']}",
                organization=PLAN_ORG,
                url=SEARCH_ORG_URL,
                title=f"Search record: {s['item']}",
                published_at=None,
                retrieved_at=now,
                geographic_applicability="Chicago Central Business District",
                data_classification=DataClassification.HISTORICAL,
                verification_status="unknown",
                page_or_section=f"Searched {SEARCH_DATE}: {SEARCH_RESULT}",
                usage_restrictions=[
                    "Search record only; item unavailable, content left unpopulated (not inferred)",
                    f"Query: {s['query']}",
                ],
                is_official_source=False,
                check_status="not_located",
                checked_at=day,
            )
        )
    return out


def search_entries() -> list[dict[str, Any]]:
    return [
        {
            "source_id": f"{PLAN_SOURCE_ID}_search_{s['key']}",
            "item": s["item"],
            "search_date": SEARCH_DATE,
            "query": s["query"],
            "result": "not located",
            "result_detail": SEARCH_RESULT,
            "url": None,
            "content": None,
            "unavailable": True,
        }
        for s in APPENDIX_SEARCHES
    ]


# --------------------------------------------------------------------------------------------
# Safe read-merge-write helpers
# --------------------------------------------------------------------------------------------
def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def merge_catalog(path: Path, records: list[SourceRecord]) -> None:
    """Read-merge-write keyed by source_id; re-reads right before the atomic replace."""
    new = {r.source_id: r.model_dump(mode="json") for r in records}
    current = _read_json(path, {"sources": []})
    existing = current.get("sources", []) if isinstance(current, dict) else list(current)
    merged = [s for s in existing if s.get("source_id") not in new]
    merged.extend(new[k] for k in sorted(new))
    atomic_write_json(path, {"sources": merged})


def merge_fallbacks(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Read-merge-write: only keys under ``emergency_plan:`` are replaced; others untouched."""
    assert all(k.startswith(FALLBACK_PREFIX) for k in entries)
    current = _read_json(path, {})
    if not isinstance(current, dict):
        current = {}
    current.update(entries)
    atomic_write_json(path, current)


# --------------------------------------------------------------------------------------------
# Main workflow
# --------------------------------------------------------------------------------------------
def run(
    *,
    fetcher: Fetcher = urllib_fetcher,
    pdf_reader: PdfPageReader = read_pdf_pages,
    now: datetime | None = None,
    local_pdf: Path = LOCAL_PDF,
    output_path: Path = OUTPUT_PATH,
    catalog_path: Path = CATALOG_PATH,
    fallbacks_path: Path = FALLBACKS_PATH,
    doc_path: Path = DOC_PATH,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    outcome = fetch_with_retries(fetcher, PLAN_URL)
    pdf: bytes | None = None
    acquisition: str
    if outcome.ok:
        pdf = outcome.data
        acquisition = "freshly downloaded"
        local_pdf.parent.mkdir(parents=True, exist_ok=True)
        local_pdf.write_bytes(pdf)  # git-ignored cache; see LOCAL_COPY_DETERMINATION
    elif local_pdf.exists():
        pdf = local_pdf.read_bytes()
        acquisition = "reused previous copy"
    else:
        acquisition = "missing"

    fallback: dict[str, Any] = {
        "dataset": PLAN_SOURCE_ID,
        "acquisition_status": acquisition,
        "source_url": PLAN_URL,
        "retrieved_at": now.isoformat(),
        "errors": outcome.errors,
        "requirements": ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "7.1"],
        "reproduce": "backend\\.venv\\Scripts\\python.exe scripts\\import_emergency_plan.py",
        "limitation": (
            "Historical (2007) plan used for planning concepts only; never an active Official_Notice. "
            "Appendices (CPD, OEMC, CTA, IDOT) and any newer plan version were not located publicly; "
            "their content is left unpopulated."
        ),
    }
    if acquisition == "reused previous copy":
        fallback["fallback"] = "Download failed; reused the previous local copy of the PDF."
    elif acquisition == "missing":
        fallback["fallback"] = "PDF unreachable and no previous copy; plan recorded as unavailable, no records extracted."

    if pdf is None:
        # Unavailable: record it, keep any prior extraction output untouched, continue.
        merge_catalog(catalog_path, [plan_source_record(now, available=False, check_status="unreachable"), *search_source_records(now)])
        merge_fallbacks(fallbacks_path, {FALLBACK_KEY: fallback})
        return {"acquisition": acquisition, "records": 0, "unavailable": True}

    pages = pdf_reader(pdf)
    records, status = extract_topics(pages)
    boundary = cbd_boundary_record(records)
    output = {
        "schema": "safepath.cbd_plan.v1",
        "source_id": PLAN_SOURCE_ID,
        "label": HISTORICAL_LABEL,
        "classification": DataClassification.HISTORICAL.value,
        "plan": {
            "title": PLAN_TITLE,
            "url": PLAN_URL,
            "plan_date": PLAN_DATE.isoformat(),
            "plan_date_note": 'Page headers read "05-16-07"; the cover reads "Additions: 29 May 07".',
            "pages": len(pages),
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "acquisition": acquisition,
            "retrieved_at": now.isoformat(),
            "local_copy": {
                "stored": True,
                "path": "data/raw/cbd_plan/cbd_evac_plan_2007.pdf (git-ignored cache)",
                "determination": LOCAL_COPY_DETERMINATION,
            },
        },
        "cbd_derived": True,
        "active": False,
        "is_official_notice": False,
        "topics": status,
        "cbd_boundary": boundary,
        "records": records,
        "searches": search_entries(),
        "not_extracted": [
            "Per-agency participation matrices for Levels I-III (Immediate Response / Event +2 Hours / Event +8 Hours): the dot columns are graphics and do not extract as text, so which agency participates at which time is NOT recorded.",
            "Boundary geometry: the plan gives street names only; no coordinates are inferred.",
            "Contents of the four appendices (not in the PDF, not publicly located).",
        ],
    }
    atomic_write_json(output_path, output)
    merge_catalog(catalog_path, [plan_source_record(now, available=True, check_status="ok"), *search_source_records(now)])
    fallback["record_count"] = len(records)
    merge_fallbacks(fallbacks_path, {FALLBACK_KEY: fallback})
    write_summary_doc(doc_path, output)
    return {"acquisition": acquisition, "records": len(records), "unavailable": False, "topics": status}


def write_summary_doc(path: Path, output: dict[str, Any]) -> None:
    topics = output["topics"]
    found = [t for t in topics.values() if t["status"] == "found"]
    lines = [
        "# CBD Evacuation Plan (2007) extraction summary",
        "",
        f"> {HISTORICAL_LABEL}",
        "",
        f"Source: {output['plan']['url']}",
        f"Plan date: {output['plan']['plan_date']} ({output['plan']['plan_date_note']}). Pages: {output['plan']['pages']}.",
        f"Acquisition: {output['plan']['acquisition']}. Classification: Historical. All records are `cbd_derived=true`, `active=false`, never an Official_Notice.",
        "",
        "Local copy: " + LOCAL_COPY_DETERMINATION,
        "",
        f"Records: {len(output['records'])} short verbatim excerpts, each with PDF page and section heading, in `data/sources/cbd_plan_2007.json`.",
        "",
        "## Topics",
        "",
        "| Topic | Status | Records | Pages |",
        "|---|---|---|---|",
    ]
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for r in output["records"]:
        by_topic.setdefault(r["topic"], []).append(r)
    for key, t in topics.items():
        recs = by_topic.get(key, [])
        pages = ", ".join(str(p) for p in sorted({r["page"] for r in recs})) or "-"
        lines.append(f"| {t['title']} | {t['status']} | {len(recs)} | {pages} |")
    lines += ["", f"{len(found)} of {len(topics)} topics have extracted text.", "", "## Not found / not extracted", ""]
    for key, t in topics.items():
        if t["status"] == NOT_FOUND:
            lines.append(f"- **{t['title']}**: not found. {t.get('reason', '')}")
    for item in output["not_extracted"]:
        lines.append(f"- {item}")
    b = output.get("cbd_boundary")
    if b:
        lines += [
            "",
            "## CBD boundary (approximate)",
            "",
            f"{b['north']} (north), {b['south']} (south), {b['east']} (east), {b['west']} (west), p. {b['page']}, section {b['section_heading']}. Labeled approximate; no coordinates.",
        ]
    lines += ["", "## Appendix and newer-version search records", ""]
    for s in output["searches"]:
        lines.append(f"- {s['item']}: searched {s['search_date']}, {s['result']}. Content left unpopulated.")
    lines += [
        "",
        "Notes: text is quoted as extracted by pypdf (a few characters such as apostrophes extract as symbols). "
        "The plan misspells ATC as \"ACTs\" once (p. 4). Phone numbers and procedures are 2007 values and may be out of date.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k != "topics"}, indent=2))
    for key, t in result.get("topics", {}).items():
        print(f"  {t['status']:9s} {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
