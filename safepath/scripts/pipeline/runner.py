"""Fixed-stage Data_Pipeline runner.

Stages (Req 7.2), always in this order:
check_availability, save_raw, reproject, clip, clean_geometry, normalize,
build_nodes_edges, save_outputs.

Behavior:
* fetch: 3 attempts x 60 s each (7.4); on exhaustion reuse the latest previous raw copy with a
  warning carrying its retrieval timestamp, else record the dataset as missing (7.5).
* a stage failure for one dataset is recorded as an error, its partial outputs are removed, and
  the remaining datasets continue (7.6).
* fatal only when no street source exists in any form (fresh or previous copy) (7.7). Acquisition
  runs for every dataset first and the fatal check happens before any processed output or Graph
  is touched, so the previously saved Graph stays unchanged.
* summary_report.json lists per dataset: feature count, URL, timestamp, acquisition status,
  warnings (7.3). A Source_Record is created for each retrieved dataset (3.7).
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .fetch import FETCH_ATTEMPTS, FETCH_TIMEOUT_SECONDS, Fetcher, fetch_with_retries

try:
    from app.models.provenance import DataClassification, SourceRecord
except ImportError:  # running from scripts/ without the backend installed
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.models.provenance import DataClassification, SourceRecord

STAGES: tuple[str, ...] = (
    "check_availability",
    "save_raw",
    "reproject",
    "clip",
    "clean_geometry",
    "normalize",
    "build_nodes_edges",
    "save_outputs",
)

TARGET_CRS = "EPSG:26916"
ACQ_FRESH = "freshly downloaded"
ACQ_REUSED = "reused previous copy"
ACQ_MISSING = "missing"

_PROCESSING_STAGES = STAGES[2:7]


def osm_usage_restrictions() -> list[str]:
    return [
        "License: Open Data Commons Open Database License (ODbL) 1.0",
        "Attribution required: © OpenStreetMap contributors",
    ]


@dataclass
class StageContext:
    spec: "DatasetSpec"
    crs: str
    stage: str


StageHandler = Callable[[Any, StageContext], Any]


@dataclass
class DatasetSpec:
    name: str
    url: str
    organization: str
    title: str
    is_street_source: bool = False
    data_classification: DataClassification = DataClassification.STATIC
    is_official_source: bool = False
    geographic_applicability: str = "Chicago_Extraction_Area"
    usage_restrictions: list[str] = field(default_factory=list)
    # Optional per-stage handlers keyed by stage name; missing stages are identity.
    handlers: dict[str, StageHandler] = field(default_factory=dict)
    # Serializes the final data for save_outputs; default is JSON.
    serialize: Callable[[Any], bytes] | None = None
    count_features: Callable[[Any], int] | None = None
    file_extension: str = "bin"

    def __post_init__(self) -> None:
        unknown = set(self.handlers) - set(_PROCESSING_STAGES)
        if unknown:
            raise ValueError(f"unknown or non-overridable stages: {sorted(unknown)}")


@dataclass
class _Acquired:
    status: str
    data: bytes | None
    retrieved_at: datetime | None
    warnings: list[str]


@dataclass
class PipelineResult:
    ok: bool
    exit_code: int
    report: dict[str, Any]
    report_path: Path | None = None


def _default_count(data: Any) -> int:
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return len(data["features"])
    if isinstance(data, (list, tuple, set)):
        return len(data)
    return 0


def _default_serialize(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return json.dumps(data, sort_keys=True).encode("utf-8")


class PipelineRunner:
    def __init__(
        self,
        datasets: list[DatasetSpec],
        *,
        raw_dir: Path,
        processed_dir: Path,
        fetcher: Fetcher,
        clock: Callable[[], datetime] | None = None,
        attempts: int = FETCH_ATTEMPTS,
        timeout: float = FETCH_TIMEOUT_SECONDS,
        report_dir: Path | None = None,
    ) -> None:
        names = [d.name for d in datasets]
        if len(set(names)) != len(names):
            raise ValueError("dataset names must be unique")
        self.datasets = datasets
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.report_dir = Path(report_dir) if report_dir else self.processed_dir
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.attempts = attempts
        self.timeout = timeout

    # -- acquisition (check_availability + save_raw) ------------------------------------------
    def _previous_copy(self, spec: DatasetSpec) -> tuple[bytes, datetime] | None:
        meta_path = self.raw_dir / spec.name / "latest.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            data = (self.raw_dir / spec.name / meta["file"]).read_bytes()
            return data, datetime.fromisoformat(meta["retrieved_at"])
        except (OSError, ValueError, KeyError):
            return None

    def _acquire(self, spec: DatasetSpec) -> _Acquired:
        previous = self._previous_copy(spec)  # read before any new copy is saved
        outcome = fetch_with_retries(
            self.fetcher, spec.url, attempts=self.attempts, timeout=self.timeout
        )
        if outcome.ok:
            return _Acquired(ACQ_FRESH, outcome.data, self.clock(), [])
        failure = f"fetch failed after {outcome.attempts} attempts: " + "; ".join(outcome.errors)
        if previous is not None:
            data, ts = previous
            return _Acquired(
                ACQ_REUSED,
                data,
                ts,
                [f"{failure}. Reused previous copy retrieved at {ts.isoformat()}"],
            )
        return _Acquired(ACQ_MISSING, None, None, [f"{failure}. No previous copy exists"])

    def _save_raw(self, spec: DatasetSpec, acq: _Acquired) -> None:
        folder = self.raw_dir / spec.name
        folder.mkdir(parents=True, exist_ok=True)
        assert acq.data is not None and acq.retrieved_at is not None
        stamp = acq.retrieved_at.strftime("%Y%m%dT%H%M%S%fZ")
        fname = f"{stamp}.{spec.file_extension}"
        (folder / fname).write_bytes(acq.data)
        meta = {"file": fname, "retrieved_at": acq.retrieved_at.isoformat(), "url": spec.url}
        tmp = folder / "latest.json.tmp"
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        tmp.replace(folder / "latest.json")

    # -- processing ---------------------------------------------------------------------------
    def _save_outputs(self, spec: DatasetSpec, data: Any) -> None:
        final = self.processed_dir / spec.name
        staging = self.processed_dir / f".{spec.name}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            (staging / f"data.{spec.file_extension if spec.serialize else 'json'}").write_bytes(
                (spec.serialize or _default_serialize)(data)
            )
            shutil.rmtree(final, ignore_errors=True)
            staging.replace(final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _process(self, spec: DatasetSpec, raw: bytes, entry: dict[str, Any]) -> None:
        data: Any = raw
        entry["feature_count"] = 0
        for stage in _PROCESSING_STAGES:
            handler = spec.handlers.get(stage)
            try:
                if handler:
                    data = handler(data, StageContext(spec, TARGET_CRS, stage))
                entry["feature_count"] = (spec.count_features or _default_count)(data)
            except Exception as exc:  # noqa: BLE001 - per-stage isolation
                self._fail(spec, entry, stage, exc)
                return
        try:
            self._save_outputs(spec, data)
        except Exception as exc:  # noqa: BLE001
            self._fail(spec, entry, "save_outputs", exc)
            return
        entry["processed"] = True

    def _fail(self, spec: DatasetSpec, entry: dict[str, Any], stage: str, exc: Exception) -> None:
        entry["errors"].append({"dataset": spec.name, "stage": stage, "error": f"{type(exc).__name__}: {exc}"})
        entry["failed_stage"] = stage
        entry["processed"] = False
        # Exclude partial outputs of this dataset from the processed data (7.6).
        shutil.rmtree(self.processed_dir / spec.name, ignore_errors=True)
        shutil.rmtree(self.processed_dir / f".{spec.name}.partial", ignore_errors=True)

    def _source_record(self, spec: DatasetSpec, acq: _Acquired) -> SourceRecord:
        assert acq.retrieved_at is not None
        return SourceRecord(
            source_id=f"src-{spec.name}",
            organization=spec.organization,
            url=spec.url,
            title=spec.title,
            retrieved_at=acq.retrieved_at,
            geographic_applicability=spec.geographic_applicability,
            data_classification=spec.data_classification,
            verification_status="verified" if spec.is_official_source else "unverified",
            usage_restrictions=list(spec.usage_restrictions),
            is_official_source=spec.is_official_source,
            check_status="ok",
            checked_at=self.clock(),
        )

    # -- orchestration ------------------------------------------------------------------------
    def run(self) -> PipelineResult:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

        entries: dict[str, dict[str, Any]] = {}
        acquired: dict[str, _Acquired] = {}
        for spec in self.datasets:
            acq = self._acquire(spec)
            acquired[spec.name] = acq
            entries[spec.name] = {
                "dataset": spec.name,
                "source_url": spec.url,
                "retrieved_at": acq.retrieved_at.isoformat() if acq.retrieved_at else None,
                "acquisition_status": acq.status,
                "feature_count": 0,
                "warnings": list(acq.warnings),
                "errors": [],
                "processed": False,
                "is_street_source": spec.is_street_source,
            }

        has_street = any(
            s.is_street_source and acquired[s.name].status != ACQ_MISSING for s in self.datasets
        )
        fatal = not has_street
        if fatal:
            # Nothing is processed or written to the Graph; saved Graph stays unchanged (7.7).
            return self._finish(entries, fatal=True, records=[])

        records: list[SourceRecord] = []
        for spec in self.datasets:
            acq, entry = acquired[spec.name], entries[spec.name]
            if acq.status == ACQ_MISSING:
                continue
            records.append(self._source_record(spec, acq))
            try:
                if acq.status == ACQ_FRESH:
                    self._save_raw(spec, acq)
            except Exception as exc:  # noqa: BLE001
                self._fail(spec, entry, "save_raw", exc)
                continue
            assert acq.data is not None
            self._process(spec, acq.data, entry)

        return self._finish(entries, fatal=False, records=records)

    def _finish(
        self, entries: dict[str, dict[str, Any]], *, fatal: bool, records: list[SourceRecord]
    ) -> PipelineResult:
        report = {
            "generated_at": self.clock().isoformat(),
            "crs": TARGET_CRS,
            "stages": list(STAGES),
            "status": "failed" if fatal else "completed",
            "fatal_reason": (
                "No street source (Chicago Data Portal centerlines or OpenStreetMap) is "
                "available from a fresh download or a previous copy; saved Graph left unchanged"
                if fatal
                else None
            ),
            "datasets": list(entries.values()),
        }
        path = self.report_dir / "summary_report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if records:
            (self.processed_dir / "source_records.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in records], indent=2),
                encoding="utf-8",
            )
        return PipelineResult(ok=not fatal, exit_code=1 if fatal else 0, report=report, report_path=path)
