"""Property tests for Data_Pipeline source resolution (Req 3.5, 7.4, 7.5, 7.6, 7.7). No network."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from scripts import process_streets as ps  # noqa: E402
from scripts.pipeline import ACQ_FRESH, ACQ_MISSING, ACQ_REUSED, DatasetSpec, PipelineRunner  # noqa: E402

PBT = settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))

NAMES = ("cdp", "osm", "gtfs")
STREET = {"cdp": True, "osm": True, "gtfs": False}
PREV_STAMP = {n: datetime(2024, 5, 1, 10 + i, 30, 15, tzinfo=timezone.utc) for i, n in enumerate(NAMES)}


def _clock():
    ticks = iter(range(1, 100_000))
    return lambda: datetime(2025, 1, 1, 0, 0, next(ticks) % 60, tzinfo=timezone.utc)


def _fetcher(fresh: dict[str, bool]):
    def fetch(url: str, timeout: float) -> bytes:
        name = url.rsplit("/", 1)[1]
        if not fresh[name]:
            raise OSError(f"{name} unavailable")
        return b"[1,2,3]"

    return fetch


def _boom(data, ctx):
    raise RuntimeError("injected stage failure")


def _specs(stage_fail: dict[str, bool]) -> list[DatasetSpec]:
    return [
        DatasetSpec(
            name=n, url=f"https://x/{n}", organization="org", title=n, is_street_source=STREET[n],
            handlers={"reproject": lambda d, c: json.loads(d), **({"clean_geometry": _boom} if stage_fail[n] else {})},
        )
        for n in NAMES
    ]


def _seed_previous(root: Path, name: str) -> None:
    folder = root / "raw" / name
    folder.mkdir(parents=True)
    (folder / "prev.bin").write_bytes(b"[7,8]")
    (folder / "latest.json").write_text(
        json.dumps({"file": "prev.bin", "retrieved_at": PREV_STAMP[name].isoformat(), "url": f"https://x/{name}"})
    )


def _run(scenario) -> tuple[dict, int, Path, str]:
    fresh, prev, stage_fail = scenario
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for n in NAMES:
            if prev[n]:
                _seed_previous(root, n)
            (root / "processed" / n).mkdir(parents=True)
            (root / "processed" / n / "data.json").write_text("old")
        graph = root / "processed" / "graph"
        graph.mkdir(parents=True)
        (graph / "latest.json").write_text('{"version": "v1"}')
        result = PipelineRunner(
            _specs(stage_fail), raw_dir=root / "raw", processed_dir=root / "processed",
            fetcher=_fetcher(fresh), clock=_clock(),
        ).run()
        # Snapshot filesystem facts before the temp dir disappears.
        facts = {n: (root / "processed" / n).exists() for n in NAMES}
        graph_text = (graph / "latest.json").read_text()
        result.report["_dirs"] = facts
        return result.report, result.exit_code, graph, graph_text


flags = st.fixed_dictionaries({n: st.booleans() for n in NAMES})


# Feature: safepath-evacuation-navigation, Property 42: Pipeline source resolution is deterministic
# **Validates: Requirements 3.5, 7.4, 7.5, 7.6, 7.7**
@PBT
@given(fresh=flags, prev=flags, stage_fail=flags)
def test_pipeline_source_resolution_is_deterministic(fresh, prev, stage_fail):
    scenario = (fresh, prev, stage_fail)
    report, code, _, graph_text = _run(scenario)
    report2, code2, _, _ = _run(scenario)
    assert (report, code) == (report2, code2)  # same inputs -> same report

    street_available = any(STREET[n] and (fresh[n] or prev[n]) for n in NAMES)
    assert code == (0 if street_available else 1)
    assert report["status"] == ("completed" if street_available else "failed")
    assert graph_text == '{"version": "v1"}'  # saved Graph never altered

    entries = {e["dataset"]: e for e in report["datasets"]}
    assert set(entries) == set(NAMES)  # the run covers every dataset
    for n in NAMES:
        e = entries[n]
        if fresh[n]:
            assert e["acquisition_status"] == ACQ_FRESH and e["warnings"] == []
        elif prev[n]:
            assert e["acquisition_status"] == ACQ_REUSED
            assert e["retrieved_at"] == PREV_STAMP[n].isoformat()
            assert any(PREV_STAMP[n].isoformat() in w for w in e["warnings"])
        else:
            assert e["acquisition_status"] == ACQ_MISSING and e["warnings"] and not e["processed"]

        acquired = e["acquisition_status"] != ACQ_MISSING
        if not street_available:
            assert not e["processed"] and e["errors"] == []
        elif acquired and stage_fail[n]:
            assert e["errors"] and e["errors"][0]["dataset"] == n and e["errors"][0]["stage"] == "clean_geometry"
            assert e["failed_stage"] == "clean_geometry" and not e["processed"]
            assert report["_dirs"][n] is False  # partial outputs removed
        elif acquired:
            assert e["processed"] is True and e["errors"] == []


# Feature: safepath-evacuation-navigation, Property 42: Pipeline source resolution is deterministic
# **Validates: Requirements 3.5**
_word = st.text(alphabet="ABCDEFGHJKLMNPQRSTUVWXYZ", min_size=3, max_size=8)
_dir = st.sampled_from(["B", "F", "T"])
_osm_ow = st.sampled_from(["yes", "no", "-1"])
_CDP_OW = {"B": "no", "F": "forward", "T": "reverse"}
_OSM_OW = {"yes": "forward", "no": "no", "-1": "reverse"}


@PBT
@given(cdp_name=_word, osm_name=_word, cdp_dir=_dir, osm_ow=_osm_ow)
def test_conflicting_attributes_keep_documented_precedence_source(cdp_name, osm_name, cdp_dir, osm_ow):
    line = LineString([(-87.65, 41.88), (-87.65, 41.8805)])
    cdp = ps.Item(ps.CDP_DATASET, "1", line, {"street_nam": cdp_name, "dir_travel": cdp_dir})
    osm = ps.Item(ps.OSM_STREETS, "9", line, {"name": osm_name, "highway": "residential", "oneway": osm_ow})
    builder, _ = ps.merge_streets([cdp], [osm], [], [])
    seg = next(s for s in builder.segments if s["sources"] == [ps.CDP_DATASET, ps.OSM_STREETS])

    names_conflict = ps.normalize_name(cdp_name) != ps.normalize_name(osm_name)
    cdp_ow, osm_ow_v = _CDP_OW[cdp_dir], _OSM_OW[osm_ow]
    ow_conflict = (cdp_ow == "no") != (osm_ow_v == "no")

    # Name: CDP precedence. One-way: OSM precedence.
    assert seg["name"] == cdp_name and seg["attribute_sources"]["name"] == "cdp"
    assert seg["oneway"] == osm_ow_v and seg["attribute_sources"]["oneway"] == "osm"
    recorded = {c["attribute"]: c for c in seg["conflicts"]}
    assert set(recorded) == {a for a, flag in (("name", names_conflict), ("oneway", ow_conflict)) if flag}
    if names_conflict:
        assert recorded["name"]["retained_source"] == ps.CDP_DATASET
    if ow_conflict:
        assert recorded["oneway"]["retained_source"] == ps.OSM_STREETS
    assert len(seg["warnings"]) == len(recorded)
    assert all(any(a in w and "conflict" in w for w in seg["warnings"]) for a in recorded)
