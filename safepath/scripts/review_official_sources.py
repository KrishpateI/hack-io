"""Official source review (Req 9.1, 9.2, 9.3, 9.4, 9.5, 9.7).

Fetches and reads the five required official pages (OEMC, UIC building-specific emergency action
plans, UIC Ready, Ready Illinois, Cook County HMP) plus the specific guidance sub-pages that SafePath
links to, and writes:

* Source_Records into ``data/sources/catalog.json`` (read-merge-write keyed by ``source_id``; atomic
  replace; the file is re-read immediately before writing because other scripts add records too).
  An unreachable page (no response within 60 s on all attempts) is recorded with ``check_status =
  "unreachable"`` and the check date, and the most recent existing record for that source is kept.
* ``data/sources/official_guidance.json``: official information links, short attributable
  preparedness notes (tornado, flood, earthquake, fire, shelter/evacuation basics) and per-page
  inspection results. A note is emitted only when its required phrases were actually found in the
  fetched page; otherwise the topic is recorded under ``not_found``. Nothing is invented and pages
  are never copied wholesale (notes are short paraphrases with the source URL).
* ``data/processed/fallbacks.json`` entries under the ``official_sources:`` key prefix (other keys
  are preserved).

Run (from the repository's ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\review_official_sources.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from app.models.provenance import (  # noqa: E402
    DataClassification,
    ReuseDetermination,
    SourceRecord,
)
from pipeline.fetch import Fetcher, fetch_with_retries, urllib_fetcher  # noqa: E402

FALLBACK_PREFIX = "official_sources:"
LONG_TERM_MITIGATION = "long_term_mitigation_planning"
REQUIREMENTS = ["9.1", "9.2", "9.3", "9.4", "9.5", "9.7"]

# Content that must never enter SafePath data from UIC sources (Req 9.3).
UIC_EXCLUDED = [
    "Building access codes",
    "Security procedures",
    "Restricted-area locations",
    "Floor plans and evacuation drawings without a public-reuse determination "
    "(the drawings linked from UIC pages have not been reviewed for reuse and are not copied)",
    "Names and contact details of building representatives and evacuation monitors",
]
UIC_BASIS = (
    "Pages are public but state no content reuse license (footer: copyright The Board of Trustees "
    "of the University of Illinois). SafePath therefore links to the pages and keeps only short "
    "attributed descriptions. No real UIC floor plan is confirmed publicly reusable, so the "
    "Building_Emergency_Module uses a fictional indoor floor plan (Req 9.4)."
)


class SourceSpec:
    def __init__(self, source_id: str, organization: str, url: str, title: str,
                 classification: DataClassification, geographic: str, page_or_section: str,
                 restrictions: list[str], reuse: ReuseDetermination | None = None) -> None:
        self.source_id = source_id
        self.organization = organization
        self.url = url
        self.title = title
        self.classification = classification
        self.geographic = geographic
        self.page_or_section = page_or_section
        self.restrictions = restrictions
        self.reuse = reuse


def source_specs(now: datetime) -> list[SourceSpec]:
    uic_restrictions = [
        "No reuse license stated on the page; link and short attributed notes only",
        "Excludes access codes, security procedures, restricted areas and unreviewed floor plans",
    ]
    return [
        SourceSpec(
            "OEMC-CHICAGO", "City of Chicago Office of Emergency Management and Communications",
            "https://www.chicago.gov/city/en/depts/oem.html",
            "Emergency Management & Communications (OEMC)", DataClassification.CURRENT,
            "City of Chicago", "Department home page",
            ["City website disclaimer: content provided as-is, no guarantee of accuracy; no reuse "
             "license located, so SafePath links and keeps short attributed notes only",
             "Not a current evacuation order"],
        ),
        SourceSpec(
            "UIC-EHSO-BUILDING-EAP", "University of Illinois Chicago Environmental Health and Safety",
            "https://ehso.uic.edu/fire-safety/building-specific-emergency-action-plans/",
            "Building Specific Emergency Action Plans", DataClassification.STATIC,
            "University of Illinois Chicago campus buildings", "Building-specific EAP index",
            uic_restrictions,
            ReuseDetermination(
                approved_content=[
                    "Link to the public building-specific emergency action plan page",
                    "Short attributed description of the resources the page offers (plans, "
                    "evacuation drawings categories, contacts form)",
                ],
                excluded_content=UIC_EXCLUDED, determined_at=now, basis=UIC_BASIS),
        ),
        SourceSpec(
            "UIC-READY", "University of Illinois Chicago Office of Preparedness and Response",
            "https://ready.uic.edu/", "UIC Ready: Office of Preparedness and Response",
            DataClassification.CURRENT, "University of Illinois Chicago campus", "Site home page",
            uic_restrictions,
            ReuseDetermination(
                approved_content=[
                    "Link to UIC Ready and its public emergency guideline pages",
                    "Short attributed description of the preparedness resources the site offers",
                ],
                excluded_content=UIC_EXCLUDED, determined_at=now, basis=UIC_BASIS),
        ),
        SourceSpec(
            "READY-ILLINOIS", "Illinois Emergency Management Agency and Office of Homeland Security",
            "https://ready.illinois.gov/", "Ready Illinois", DataClassification.STATIC,
            "State of Illinois", "Site home page",
            ["State disclaimer: accuracy, completeness and timeliness not guaranteed; no reuse "
             "license located, so SafePath links and keeps short attributed notes only"],
        ),
        SourceSpec(
            "COOK-COUNTY-HMP", "Cook County Emergency Management and Regional Security",
            "https://www.cookcountyemergencymanagement.org/hmp",
            "Cook County Multi-Jurisdictional Hazard Mitigation Plan", DataClassification.STATIC,
            "Cook County, Illinois (long-term mitigation planning areas)", "Hazard Mitigation Plan page",
            [LONG_TERM_MITIGATION,
             "Plan areas are long-term mitigation planning areas, not live Incident boundaries "
             "(Req 9.5)"],
        ),
    ]


# Guidance topics: each is emitted only if every required phrase is present in the fetched page.
GUIDANCE_TOPICS: list[dict[str, Any]] = [
    {
        "id": "ready-illinois-tornado", "hazard": "tornado", "source_id": "READY-ILLINOIS",
        "title": "Tornado preparedness and shelter basics",
        "url": "https://ready.illinois.gov/hazards/tornadoes.html",
        "require": ["Tornado Warning", "interior room", "Stay away from windows", "outrun a tornado"],
        "notes": [
            "Pick a safe zone at home and work in advance, preferably a basement or small interior "
            "room or hallway.",
            "During a tornado warning, go to the safe zone, stay away from windows and do not open "
            "them.",
            "Do not try to outrun a tornado in a car; leave the vehicle and seek sturdy shelter.",
            "Keep a battery-powered NOAA Weather Radio for updates.",
        ],
    },
    {
        "id": "ready-illinois-flood", "hazard": "flood", "source_id": "READY-ILLINOIS",
        "title": "Flood safety basics", "url": "https://ready.illinois.gov/hazards/floods.html",
        "require": ["Flash Flood Warning", "Six inches of moving water", "drive over a flooded road"],
        "notes": [
            "Move to higher ground and stay away from flooded areas, especially underpasses, "
            "canals and drainage channels.",
            "Do not walk through moving water; six inches can knock a person off their feet.",
            "Do not drive over a flooded road; if a vehicle stalls, abandon it.",
        ],
    },
    {
        "id": "ready-illinois-earthquake", "hazard": "earthquake", "source_id": "READY-ILLINOIS",
        "title": "Earthquake: Drop, Cover, Hold", "url": "https://ready.illinois.gov/hazards/earthquake.html",
        "require": ["Drop", "Cover", "Hold", "overpasses or bridges"],
        "notes": [
            "Indoors: drop to the floor, take cover under sturdy furniture and hold on until the "
            "shaking stops.",
            "Outdoors: move to an open area away from trees, buildings, walls and power lines.",
            "If driving, stop safely and do not stop under overpasses or bridges.",
        ],
    },
    {
        "id": "ready-illinois-fire-high-rise", "hazard": "fire", "source_id": "READY-ILLINOIS",
        "title": "High-rise fire safety basics", "url": "https://ready.illinois.gov/hazards/hirise.html",
        "require": ["Learn your building", "9-1-1", "warm to the touch", "STAY OUT"],
        "notes": [
            "Learn your building's evacuation plan and the sound of its fire alarm.",
            "Call 9-1-1 early; do not assume someone else already has.",
            "If a door is warm, do not open it; if there is smoke in the hallway, return to the room.",
            "Once outside, stay out until the fire department says it is safe.",
        ],
    },
    {
        "id": "ready-illinois-shelter-evacuate", "hazard": "shelter_or_evacuation",
        "source_id": "READY-ILLINOIS", "title": "Staying put (shelter-in-place) versus evacuating",
        "url": "https://ready.illinois.gov/plan/stayput.html",
        "require": ["Shelter-In-Place", "told to evacuate by government officials", "authorized routes"],
        "notes": [
            "If officials tell you to shelter in place or to evacuate, follow the instruction "
            "immediately.",
            "Shelter in place means taking immediate shelter indoors when going outside is more "
            "dangerous.",
            "When evacuating, use authorized routes and take your Go Bag.",
        ],
    },
    {
        "id": "oemc-family-preparedness", "hazard": "general", "source_id": "OEMC-CHICAGO",
        "title": "How to prepare your family for an emergency",
        "url": "https://www.chicago.gov/city/en/depts/oem/supp_info/how_to_prepare_yourfamilyforanemergency.html",
        "require": ["at least 3 days", "two separate escape routes", "out-of-area contact"],
        "notes": [
            "Be ready to care for your household for at least 3 days; city resources may be limited.",
            "Choose two meeting places and identify two separate escape routes from home.",
            "Pick an out-of-area contact person and build an Emergency Supply Kit and Go Bag.",
        ],
    },
    {
        "id": "oemc-notify-chicago-alerts", "hazard": "alerts", "source_id": "OEMC-CHICAGO",
        "title": "OEMC alerts: Notify Chicago",
        "url": "https://www.chicago.gov/city/en/depts/oem/supp_info/oemc-alerts--current-threats-and-public-information.html",
        "require": ["NotifyChicago", "CHILAKE"],
        "notes": [
            "NotifyChicago sends text or email alerts for severe weather emergencies, hazardous "
            "materials and traffic impacts.",
            "OEMC also lists a CHILAKE text service for lakefront weather notices.",
        ],
    },
]

# Topics checked for on the five required pages; recorded under not_found if missing.
INSPECTION_KEYWORDS: dict[str, list[str]] = {
    "tornado": ["tornado"],
    "flood": ["flood"],
    "earthquake": ["earthquake"],
    "fire": ["fire"],
    "shelter": ["shelter"],
    "evacuation": ["evacuat"],
    "building_plans": ["emergency action plan", "evacuation drawing", "floor plan"],
    "hazard_mitigation": ["hazard mitigation"],
    "public_alerts": ["alert"],
}

# OEMC pages searched for a current public evacuation procedure.
OEMC_EVAC_PAGES = [
    "https://www.chicago.gov/city/en/depts/oem.html",
    "https://www.chicago.gov/city/en/depts/oem/provdrs/alertchicago.html",
    "https://www.chicago.gov/city/en/depts/oem/supp_info/oemc-alerts--current-threats-and-public-information.html",
    "https://www.chicago.gov/city/en/depts/oem/supp_info/how_to_prepare_yourfamilyforanemergency.html",
]
OEMC_TERMS_PAGE = "https://www.chicago.gov/city/en/general/disclaimer.html"
READY_IL_TERMS_PAGE = "https://ready.illinois.gov/disclaimer.html"


# -- page reading ---------------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._label = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            self.links.append((" ".join("".join(self._label).split()), self._href))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._label.append(data)
        if not self._skip:
            self.parts.append(data)


def read_page(raw: bytes, base_url: str) -> dict[str, Any]:
    """Decode and reduce a page to title, plain text and absolute links."""
    html = raw.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(html)
    return {
        "title": " ".join(parser.title.split()),
        "text": " ".join(" ".join(parser.parts).split()),
        "links": [(label, urljoin(base_url, href)) for label, href in parser.links],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def has_phrases(text: str, phrases: list[str]) -> list[str]:
    """Return the phrases missing from ``text`` (case-insensitive)."""
    low = text.casefold()
    return [p for p in phrases if p.casefold() not in low]


def detect_topics(text: str) -> tuple[list[str], list[str]]:
    low = text.casefold()
    found, missing = [], []
    for topic, needles in INSPECTION_KEYWORDS.items():
        (found if any(n in low for n in needles) else missing).append(topic)
    return found, missing


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# -- json read-merge-write ------------------------------------------------------------------------
def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge_catalog(path: Path, records: list[SourceRecord]) -> list[dict[str, Any]]:
    """Merge records into the catalog keyed by ``source_id``; re-reads the file just before writing."""
    current = _read_json(path, {"sources": []})
    sources = current.get("sources", []) if isinstance(current, dict) else list(current)
    by_id = {s["source_id"]: s for s in sources}
    for rec in records:
        by_id[rec.source_id] = json.loads(rec.model_dump_json(exclude_none=False))
    merged = list(by_id.values())
    atomic_write_json(path, {**(current if isinstance(current, dict) else {}), "sources": merged})
    return merged


def merge_fallbacks(path: Path, entries: dict[str, Any]) -> dict[str, Any]:
    """Replace this script's ``official_sources:`` keys; preserve every other key."""
    existing = _read_json(path, {})
    merged = {k: v for k, v in existing.items() if not k.startswith(FALLBACK_PREFIX)}
    merged.update(entries)
    atomic_write_json(path, dict(sorted(merged.items())))
    return merged


# -- review ---------------------------------------------------------------------------------------
class Reviewer:
    def __init__(self, fetcher: Fetcher, now: datetime | None = None, attempts: int = 3,
                 timeout: float = 60) -> None:
        self.fetcher = fetcher
        self.now = now or now_utc()
        self.attempts = attempts
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, url: str) -> dict[str, Any]:
        """Fetch + read a page once. Returns {ok, page?, errors, attempts}."""
        if url not in self._cache:
            out = fetch_with_retries(self.fetcher, url, attempts=self.attempts, timeout=self.timeout)
            entry: dict[str, Any] = {"ok": out.ok, "errors": out.errors, "attempts": out.attempts,
                                     "checked_at": _iso(now_utc() if self.now is None else self.now)}
            if out.ok and out.data is not None:
                entry["page"] = read_page(out.data, url)
            self._cache[url] = entry
        return self._cache[url]

    def build_record(self, spec: SourceSpec, prior: dict[str, Any] | None) -> tuple[SourceRecord, dict[str, Any]]:
        res = self.get(spec.url)
        inspection: dict[str, Any] = {
            "source_id": spec.source_id, "url": spec.url, "checked_at": _iso(self.now),
            "attempts": res["attempts"],
        }
        if not res["ok"]:
            inspection.update(status="unreachable", errors=res["errors"])
            if prior is not None:
                kept = SourceRecord.model_validate(prior)
                kept = kept.model_copy(update={"check_status": "unreachable", "checked_at": self.now})
                return kept, inspection
            rec = SourceRecord(
                source_id=spec.source_id, organization=spec.organization, url=spec.url,
                title=spec.title, retrieved_at=self.now, geographic_applicability=spec.geographic,
                data_classification=spec.classification, verification_status="unknown",
                page_or_section=spec.page_or_section, usage_restrictions=spec.restrictions,
                is_official_source=True, reuse_determination=spec.reuse,
                check_status="unreachable", checked_at=self.now)
            return rec, inspection
        page = res["page"]
        found, missing = detect_topics(page["text"])
        inspection.update(
            status="ok", page_title=page["title"], content_sha256=page["sha256"], bytes=page["bytes"],
            topics_found=found, topics_not_found=missing, link_count=len(page["links"]))
        rec = SourceRecord(
            source_id=spec.source_id, organization=spec.organization, url=spec.url,
            title=spec.title, retrieved_at=self.now, geographic_applicability=spec.geographic,
            data_classification=spec.classification, verification_status="verified",
            page_or_section=spec.page_or_section, usage_restrictions=spec.restrictions,
            is_official_source=True, reuse_determination=spec.reuse,
            check_status="ok", checked_at=self.now)
        return rec, inspection

    def guidance(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items: list[dict[str, Any]] = []
        not_found: list[dict[str, Any]] = []
        for topic in GUIDANCE_TOPICS:
            res = self.get(topic["url"])
            if not res["ok"]:
                not_found.append({"topic": topic["id"], "reason": "page unreachable",
                                  "checked_urls": [topic["url"]], "checked_at": _iso(self.now),
                                  "errors": res["errors"]})
                continue
            missing = has_phrases(res["page"]["text"], topic["require"])
            if missing:
                not_found.append({"topic": topic["id"], "reason": "expected content not found on page",
                                  "missing_phrases": missing, "checked_urls": [topic["url"]],
                                  "checked_at": _iso(self.now)})
                continue
            items.append({
                "guidance_id": topic["id"], "hazard": topic["hazard"], "title": topic["title"],
                "notes": topic["notes"], "source_id": topic["source_id"], "source_url": topic["url"],
                "retrieved_at": _iso(self.now), "data_classification": "Static",
                "attribution": "Paraphrased summary; see the source page for the full guidance.",
            })
        return items, not_found

    def oemc_evacuation_check(self) -> dict[str, Any]:
        """Look for a current public evacuation procedure on OEMC pages (never assume one exists)."""
        checked, reachable, hits, incidental = [], 0, [], []
        procedural = re.compile(r"evacuation\s+(plan|order|route|zone|procedure|level)s?", re.I)
        for url in OEMC_EVAC_PAGES:
            res = self.get(url)
            checked.append(url)
            if not res["ok"]:
                continue
            reachable += 1
            text = res["page"]["text"]
            for m in re.finditer(r"[^.]{0,80}\bevacuat\w*[^.]{0,80}", text, flags=re.I):
                item = {"url": url, "excerpt": " ".join(m.group(0).split())[:160]}
                (hits if procedural.search(m.group(0)) else incidental).append(item)
        finding = {
            "topic": "oemc-public-evacuation-procedures", "checked_urls": checked,
            "pages_read": reachable, "checked_at": _iso(self.now),
            "incidental_mentions": incidental[:5],
        }
        if reachable == 0:
            finding.update(status="unverified", reason="no OEMC page could be read")
        elif hits:
            finding.update(status="mentions_found", mentions=hits[:5],
                           reason="evacuation is mentioned; review manually, no procedure is adopted automatically")
        else:
            finding.update(status="not_found",
                           reason="no current public evacuation procedure found on the OEMC pages read "
                                  "(only generic family-planning mentions); SafePath links to OEMC and "
                                  "does not state any OEMC evacuation procedure")
        return finding

    def terms_of_use(self) -> list[dict[str, Any]]:
        out = []
        for source_id, url, summary in [
            ("OEMC-CHICAGO", OEMC_TERMS_PAGE,
             "City disclaimer: website content is provided as-is with no guarantee of accuracy or "
             "availability; third-party links are subject to their own terms. No content reuse "
             "license was found."),
            ("READY-ILLINOIS", READY_IL_TERMS_PAGE,
             "State disclaimer: information is for convenience; accuracy, completeness and "
             "timeliness are not guaranteed. No content reuse license was found."),
        ]:
            res = self.get(url)
            entry: dict[str, Any] = {"source_id": source_id, "url": url, "checked_at": _iso(self.now)}
            if res["ok"]:
                entry.update(status="ok", summary=summary)
            else:
                entry.update(status="unreachable", summary="Terms page could not be read; treat reuse as not permitted.",
                             errors=res["errors"])
            out.append(entry)
        return out


def official_links(specs: list[SourceSpec]) -> list[dict[str, str]]:
    """Official information links for the UI sidebar (Req 9.6): OEMC, Ready Illinois, UIC Ready."""
    wanted = {"OEMC-CHICAGO": "Chicago OEMC", "READY-ILLINOIS": "Ready Illinois", "UIC-READY": "UIC Ready"}
    return [{"id": s.source_id, "label": wanted[s.source_id], "url": s.url, "source_id": s.source_id}
            for s in specs if s.source_id in wanted]


def run(fetcher: Fetcher, *, catalog_path: Path, guidance_path: Path, fallbacks_path: Path,
        now: datetime | None = None, attempts: int = 3, timeout: float = 60) -> dict[str, Any]:
    rv = Reviewer(fetcher, now, attempts, timeout)
    specs = source_specs(rv.now)
    catalog_now = _read_json(catalog_path, {"sources": []})
    prior_by_id = {s["source_id"]: s for s in (catalog_now.get("sources", []) if isinstance(catalog_now, dict) else catalog_now)}

    records, inspections = [], []
    for spec in specs:
        rec, insp = rv.build_record(spec, prior_by_id.get(spec.source_id))
        records.append(rec)
        inspections.append(insp)

    guidance_items, not_found = rv.guidance()
    evac = rv.oemc_evacuation_check()
    terms = rv.terms_of_use()

    # Retain previously captured guidance whose page is currently unreadable.
    prior_guidance = _read_json(guidance_path, {})
    fresh_ids = {g["guidance_id"] for g in guidance_items}
    retained = [g for g in prior_guidance.get("guidance", []) if g.get("guidance_id") not in fresh_ids
                and any(n["topic"] == g["guidance_id"] and n["reason"] == "page unreachable" for n in not_found)]
    guidance_doc = {
        "generated_at": _iso(rv.now),
        "disclaimer": "Static preparedness guidance paraphrased from official pages. It is not a "
                      "current evacuation order. Always follow instructions from official responders.",
        "official_links": official_links(specs),
        "guidance": guidance_items + retained,
        "not_found": not_found,
        "oemc_public_evacuation_procedures": evac,
        "terms_of_use": terms,
        "uic": {"real_floor_plan_reuse_confirmed": False,
                "building_emergency_module_plan": "fictional",
                "excluded_content": UIC_EXCLUDED},
        "cook_county_hmp": {"classification": LONG_TERM_MITIGATION,
                            "live_incident_boundary_allowed": False},
        "page_inspections": inspections,
        "requirements": REQUIREMENTS,
    }
    atomic_write_json(guidance_path, guidance_doc)

    fallback_entries: dict[str, Any] = {}
    for insp in inspections:
        if insp["status"] == "unreachable":
            fallback_entries[f"{FALLBACK_PREFIX}{insp['source_id']}"] = {
                "source_id": insp["source_id"], "source_url": insp["url"], "status": "unreachable",
                "checked_at": insp["checked_at"], "errors": insp["errors"],
                "fallback": "Most recent existing Source_Record retained; no fresh content captured.",
                "requirements": ["9.7"]}
    for nf in not_found:
        fallback_entries[f"{FALLBACK_PREFIX}guidance:{nf['topic']}"] = {
            "status": "not_found", "reason": nf["reason"], "checked_urls": nf["checked_urls"],
            "checked_at": nf["checked_at"], "limitation": "No static guidance for this topic is shown.",
            "requirements": ["9.6"]}
    if evac["status"] != "mentions_found":
        fallback_entries[f"{FALLBACK_PREFIX}oemc_public_evacuation_procedures"] = {
            **evac, "limitation": "SafePath states no OEMC evacuation procedure; it links to OEMC only.",
            "requirements": ["9.1"]}
    for t in terms:
        if t["status"] != "ok":
            fallback_entries[f"{FALLBACK_PREFIX}terms:{t['source_id']}"] = {**t, "requirements": ["9.1"]}
    merge_fallbacks(fallbacks_path, fallback_entries)

    # Re-read and merge the catalog as the last step so concurrent additions are preserved.
    merge_catalog(catalog_path, records)
    return {"records": records, "inspections": inspections, "guidance": guidance_doc,
            "fallbacks": fallback_entries}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--catalog", type=Path, default=ROOT / "data" / "sources" / "catalog.json")
    p.add_argument("--guidance", type=Path, default=ROOT / "data" / "sources" / "official_guidance.json")
    p.add_argument("--fallbacks", type=Path, default=ROOT / "data" / "processed" / "fallbacks.json")
    args = p.parse_args(argv)
    result = run(urllib_fetcher, catalog_path=args.catalog, guidance_path=args.guidance,
                 fallbacks_path=args.fallbacks)
    for insp in result["inspections"]:
        print(f"{insp['source_id']}: {insp['status']}")
    print(f"guidance notes: {len(result['guidance']['guidance'])}, "
          f"not found: {len(result['guidance']['not_found'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
