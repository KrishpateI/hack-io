"""Versioned graph artifact writer/reader (Req 7.8, 12.1).

Layout under `<root>/graphs/`:
    v{YYYYMMDD}-{hash}/manifest.json, nodes.jsonl.gz, edges.jsonl.gz, layers/*.geojson
    latest.json   (updated last, atomically)

Versions are never overwritten.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.models.graph import Edge, Graph, Node

NODES_FILE = "nodes.jsonl.gz"
EDGES_FILE = "edges.jsonl.gz"
MANIFEST_FILE = "manifest.json"
LATEST_FILE = "latest.json"


class ArtifactError(Exception):
    pass


class Manifest(BaseModel):
    version: str
    built_at: datetime
    sha256: dict[str, str]
    counts: dict[str, int]
    crs: str = "EPSG:4326"
    elevation_percentiles: dict[str, float] = {}
    source_record_ids: list[str] = []


def _gz(lines: list[str]) -> bytes:
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:  # mtime=0: deterministic bytes
        f.write(raw)
    return buf.getvalue()


def _ungz_lines(data: bytes) -> list[str]:
    return [ln for ln in gzip.decompress(data).decode("utf-8").split("\n") if ln]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_artifact(
    root: Path,
    graph: Graph,
    layers: dict[str, dict] | None = None,
    *,
    built_at: datetime | None = None,
    elevation_percentiles: dict[str, float] | None = None,
    source_record_ids: list[str] | None = None,
    crs: str = "EPSG:4326",
) -> str:
    """Write a new version directory and then update latest.json. Returns the version id."""
    built_at = built_at or datetime.now(timezone.utc)
    layers = layers or {}
    files: dict[str, bytes] = {
        NODES_FILE: _gz([n.model_dump_json() for n in graph.nodes]),
        EDGES_FILE: _gz([e.model_dump_json() for e in graph.edges]),
    }
    for name, fc in layers.items():
        if "/" in name or "\\" in name or name.startswith("."):
            raise ArtifactError(f"invalid layer name {name!r}")
        files[f"layers/{name}.geojson"] = _json_bytes(fc)

    digests = {k: _sha(v) for k, v in sorted(files.items())}
    content_hash = _sha(_json_bytes(digests))[:6]
    version = f"v{built_at.astimezone(timezone.utc):%Y%m%d}-{content_hash}"

    graphs_dir = Path(root) / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)
    final_dir = graphs_dir / version
    if final_dir.exists():
        raise ArtifactError(f"version {version} already exists; refusing to overwrite")

    manifest = Manifest(
        version=version,
        built_at=built_at,
        sha256=digests,
        counts={"nodes": len(graph.nodes), "edges": len(graph.edges), "layers": len(layers)},
        crs=crs,
        elevation_percentiles=elevation_percentiles or {},
        source_record_ids=source_record_ids or [],
    )

    tmp = Path(tempfile.mkdtemp(prefix=f".{version}.tmp-", dir=graphs_dir))
    try:
        for rel, data in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (tmp / MANIFEST_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.rename(tmp, final_dir)  # fails if final_dir appeared meanwhile; never merges
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    _write_latest(graphs_dir, version)
    return version


def _write_latest(graphs_dir: Path, version: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".latest-", suffix=".tmp", dir=graphs_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": version}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, graphs_dir / LATEST_FILE)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_latest_version(root: Path) -> str:
    p = Path(root) / "graphs" / LATEST_FILE
    if not p.exists():
        raise ArtifactError("no latest.json")
    return json.loads(p.read_text(encoding="utf-8"))["version"]


def read_artifact(
    root: Path, version: str | None = None, *, verify: bool = True
) -> tuple[Graph, Manifest, dict[str, dict]]:
    """Read a version (default: latest), verifying sha256 of every file and counts."""
    version = version or read_latest_version(root)
    vdir = Path(root) / "graphs" / version
    mpath = vdir / MANIFEST_FILE
    if not mpath.exists():
        raise ArtifactError(f"version {version} not found")
    manifest = Manifest.model_validate_json(mpath.read_text(encoding="utf-8"))

    blobs: dict[str, bytes] = {}
    for rel, expected in manifest.sha256.items():
        p = vdir / rel
        if not p.exists():
            raise ArtifactError(f"missing file {rel}")
        data = p.read_bytes()
        if verify and _sha(data) != expected:
            raise ArtifactError(f"checksum mismatch for {rel}")
        blobs[rel] = data

    nodes = [Node.model_validate_json(ln) for ln in _ungz_lines(blobs[NODES_FILE])]
    edges = [Edge.model_validate_json(ln) for ln in _ungz_lines(blobs[EDGES_FILE])]
    if manifest.counts.get("nodes") != len(nodes) or manifest.counts.get("edges") != len(edges):
        raise ArtifactError("manifest counts do not match file contents")
    layers = {
        rel[len("layers/"):-len(".geojson")]: json.loads(data.decode("utf-8"))
        for rel, data in blobs.items()
        if rel.startswith("layers/")
    }
    return Graph(nodes=nodes, edges=edges), manifest, layers
