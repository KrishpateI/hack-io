"""S3 Graph source: download ``graphs/<version>/`` into a local directory (Req 30.7, D1).

Layout in the data bucket (identical to the local ``data/processed`` artifact)::

    <prefix>/latest.json                 {"version": "v20260101-abc123"}
    <prefix>/<version>/manifest.json     sha256 + counts for every file
    <prefix>/<version>/nodes.jsonl.gz ... edges.jsonl.gz, layers/*.geojson

``download_graph`` fetches ``latest.json``, then the manifest, then each file, verifying every
sha256 as it arrives, and writes ``<dest>/graphs/<version>/...`` followed by
``<dest>/graphs/latest.json`` (so ``Graph.load(dest)`` works unchanged). If the newest version
cannot be downloaded or fails its checksums, older versions are tried (newest first).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANIFEST_FILE = "manifest.json"
LATEST_FILE = "latest.json"


class GraphDownloadError(Exception):
    """No graph version could be downloaded and verified."""


def _get(client: Any, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _safe_rel(rel: str) -> str:
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ".." in rel.split("/"):
        raise GraphDownloadError(f"unsafe file name in manifest: {rel!r}")
    return rel


def _list_versions(client: Any, bucket: str, prefix: str) -> list[str]:
    versions: list[str] = []
    token: str | None = None
    while True:
        kw: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/", "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for cp in resp.get("CommonPrefixes", []):
            name = cp["Prefix"][len(prefix) + 1:].strip("/")
            if name:
                versions.append(name)
        token = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
        if not token:
            break
    return sorted(versions, reverse=True)


def _download_version(client: Any, bucket: str, prefix: str, version: str, dest: Path) -> None:
    target = dest / "graphs" / version
    if target.exists():
        shutil.rmtree(target)
    tmp = dest / "graphs" / f".{version}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        manifest_raw = _get(client, bucket, f"{prefix}/{version}/{MANIFEST_FILE}")
        manifest = json.loads(manifest_raw)
        tmp.mkdir(parents=True)
        for rel, expected in sorted(manifest["sha256"].items()):
            data = _get(client, bucket, f"{prefix}/{version}/{_safe_rel(rel)}")
            if hashlib.sha256(data).hexdigest() != expected:
                raise GraphDownloadError(f"checksum mismatch for {version}/{rel}")
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (tmp / MANIFEST_FILE).write_bytes(manifest_raw)
        os.replace(tmp, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def download_graph(client: Any, bucket: str, dest: Path | str, *, prefix: str = "graphs",
                   version: str | None = None) -> str:
    """Download the requested (default: latest) version; returns the version actually fetched."""
    dest = Path(dest)
    prefix = prefix.strip("/")
    (dest / "graphs").mkdir(parents=True, exist_ok=True)
    candidates: list[str] = []
    try:
        candidates.append(version or json.loads(_get(client, bucket, f"{prefix}/{LATEST_FILE}"))["version"])
    except Exception as exc:
        log.warning("graph latest.json unreadable: %s", type(exc).__name__)
    try:
        candidates += [v for v in _list_versions(client, bucket, prefix) if v not in candidates]
    except Exception as exc:
        log.warning("could not list graph versions: %s", type(exc).__name__)

    errors: list[str] = []
    for v in candidates:
        try:
            _download_version(client, bucket, prefix, v, dest)
        except Exception as exc:
            errors.append(f"{v}: {type(exc).__name__}: {exc}")
            log.warning("graph version %s not usable: %s", v, exc)
            continue
        latest = dest / "graphs" / LATEST_FILE
        tmp = latest.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": v}), encoding="utf-8")
        os.replace(tmp, latest)
        return v
    raise GraphDownloadError("no graph version could be downloaded: " + "; ".join(errors or ["none found"]))


def load_graph_from_s3(env: dict[str, str] | Any, client: Any = None) -> dict[str, Any]:
    """Cold-start helper: returns ``{"root", "version", "seconds"}`` for the downloaded graph."""
    bucket = (env.get("SAFEPATH_GRAPH_BUCKET") or "").strip()
    prefix = (env.get("SAFEPATH_GRAPH_PREFIX") or "graphs").strip("/") or "graphs"
    dest = Path(env.get("SAFEPATH_GRAPH_CACHE_DIR") or "/tmp/safepath-graph")
    if client is None:
        from app.adapters.aws.session import make_client

        client = make_client("s3", env)
    started = time.perf_counter()
    version = download_graph(client, bucket, dest, prefix=prefix)
    seconds = time.perf_counter() - started
    log.info("graph %s downloaded from s3://%s/%s in %.2fs", version, bucket, prefix, seconds)
    return {"root": dest, "version": version, "seconds": seconds}
