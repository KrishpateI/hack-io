"""Source_Catalog service: load, search and filter SourceRecords (Req 10.1, 10.2, 10.7)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, get_args

from app.models.provenance import DataClassification, SourceRecord, VerificationStatus

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "sources" / "catalog.json"

PERMITTED_CLASSIFICATIONS: tuple[str, ...] = tuple(c.value for c in DataClassification)
PERMITTED_VERIFICATION_STATUSES: tuple[str, ...] = tuple(get_args(VerificationStatus))

# Item kinds that must each carry a Source_Record (Req 10.1).
SOURCED_KINDS: tuple[str, ...] = (
    "rule", "zone", "closure", "shelter", "destination", "notice", "dataset",
)


class InvalidFilterError(ValueError):
    """Raised for an unsupported filter value; message lists the permitted values."""

    def __init__(self, field: str, value: str, permitted: Iterable[str]) -> None:
        self.field = field
        self.value = value
        self.permitted = tuple(permitted)
        super().__init__(
            f"Invalid {field} {value!r}; permitted values: {', '.join(self.permitted)}"
        )


class MissingSourceRecordError(ValueError):
    """Raised when a rule/zone/closure/shelter/destination/notice/dataset lacks a Source_Record."""


def load_catalog(path: Path | None = None) -> list[SourceRecord]:
    p = path or DEFAULT_CATALOG_PATH
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    raw = data.get("sources", []) if isinstance(data, dict) else data
    return [SourceRecord.model_validate(r) for r in raw]


def search_sources(
    records: Iterable[SourceRecord],
    q: str | None = None,
    data_classification: str | None = None,
    verification_status: str | None = None,
) -> list[SourceRecord]:
    """Case-insensitive substring search on organization/title plus exact filters."""
    if data_classification is not None and data_classification not in PERMITTED_CLASSIFICATIONS:
        raise InvalidFilterError("data_classification", data_classification, PERMITTED_CLASSIFICATIONS)
    if verification_status is not None and verification_status not in PERMITTED_VERIFICATION_STATUSES:
        raise InvalidFilterError(
            "verification_status", verification_status, PERMITTED_VERIFICATION_STATUSES
        )
    needle = q.strip().casefold() if q else ""
    out: list[SourceRecord] = []
    for r in records:
        if needle and needle not in r.organization.casefold() and needle not in r.title.casefold():
            continue
        if data_classification is not None and r.data_classification.value != data_classification:
            continue
        if verification_status is not None and r.verification_status != verification_status:
            continue
        out.append(r)
    return out


def require_source_record(item: Any, kind: str = "item") -> SourceRecord | str:
    """Return the item's source reference or raise if absent.

    Accepts a mapping or object exposing ``source_record`` (SourceRecord/dict)
    or ``source_id`` (non-empty string).
    """
    for attr in ("source_record", "source_id"):
        val = item.get(attr) if isinstance(item, dict) else getattr(item, attr, None)
        if val is None:
            continue
        if isinstance(val, str):
            if val.strip():
                return val
            continue
        if isinstance(val, SourceRecord):
            return val
        if isinstance(val, dict):
            return SourceRecord.model_validate(val)
    raise MissingSourceRecordError(f"{kind} rejected: no Source_Record attached")


def reject_unsourced(items: Iterable[Any], kind: str = "item") -> list[Any]:
    """Return items unchanged if all carry a Source_Record, else raise."""
    items = list(items)
    for it in items:
        require_source_record(it, kind)
    return items
