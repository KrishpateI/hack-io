"""Artifact smoke test (Req 2.2, 3.4, 3.5, 7.3, 7.8): manifest checksums, Unknown handling,
and a routable snap target near every required neighborhood reference point."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from app.api.routers.regions import NEIGHBORHOODS  # noqa: E402
from app.routing.graph import OK, Graph  # noqa: E402
from app.services.graph_artifact import ArtifactError, read_artifact  # noqa: E402
from scripts import make_fixture_graph as mfg  # noqa: E402

REQUIRED = {"Loop", "West Loop", "Riverwalk", "Clark/Lake", "Millennium Park", "DePaul Loop", "UIC",
            "UIC-downtown corridor"}
SNAP_TOLERANCE_M = 600.0  # fixture nodes sit on a coarse grid; reference points are approximate


@pytest.fixture(scope="module")
def artifact():
    return read_artifact(mfg.FIXTURE_ROOT)


def _version_dir() -> Path:
    latest = json.loads((mfg.FIXTURE_ROOT / "graphs" / "latest.json").read_text(encoding="utf-8"))
    return mfg.FIXTURE_ROOT / "graphs" / latest["version"]


def test_manifest_checksums_match_files_on_disk():
    vdir = _version_dir()
    manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"], "manifest must list checksums"
    for rel, digest in manifest["sha256"].items():
        assert hashlib.sha256((vdir / rel).read_bytes()).hexdigest() == digest, rel


def test_tampered_file_fails_checksum(tmp_path):
    mfg.make_fixture(tmp_path, None)
    vdir = next(p for p in (tmp_path / "graphs").iterdir() if p.is_dir())
    edges = vdir / "edges.jsonl.gz"
    edges.write_bytes(gzip.compress(b"tampered\n"))
    with pytest.raises(ArtifactError, match="checksum"):
        read_artifact(tmp_path)


def test_manifest_counts_and_unknown_never_defaulted(artifact):
    g, manifest, _ = artifact
    assert manifest.counts["nodes"] == len(g.nodes) and manifest.counts["edges"] == len(g.edges)
    for n in g.nodes:  # elevation is a number or the literal Unknown; never a placeholder zero
        assert n.elevation_m == "Unknown" or n.elevation_m != 0
    unknown_nodes = [n for n in g.nodes if n.elevation_m == "Unknown"]
    assert unknown_nodes and all(n.elevation_conf == "Unknown" for n in unknown_nodes)
    for e in g.edges:
        if e.elevation_start == "Unknown" or e.elevation_end == "Unknown":
            assert e.elevation_confidence in ("Unknown", "Low")


def test_every_required_neighborhood_has_a_routable_node(artifact):
    g, _, _ = artifact
    ids = {n.node_id for n in g.nodes}
    assert set(mfg.NEIGHBORHOOD_LABELS.values()) == REQUIRED
    for node_id, label in mfg.NEIGHBORHOOD_LABELS.items():
        assert node_id in ids, label
        touching = [e for e in g.edges if node_id in (e.from_node, e.to_node)]
        assert any("walking" in e.travel_mode for e in touching), f"{label} has no walkable edge"


def test_neighborhood_reference_points_snap_to_walkable_edges():
    g = Graph.load(mfg.FIXTURE_ROOT)
    assert g.status == OK
    assert {h["name"] for h in NEIGHBORHOODS} >= REQUIRED - {"UIC-downtown corridor"}
    for h in NEIGHBORHOODS:
        snap = g.snap(h["lat"], h["lon"])
        assert snap is not None, h["name"]
        assert snap.distance_m < SNAP_TOLERANCE_M, (h["name"], snap.distance_m)
