"""Extract a self-hosted PMTiles basemap for the Chicago_Extraction_Area (task 14.3, Req 41.1, 3.8, design D8).

Uses the Protomaps ``pmtiles extract`` CLI against a Protomaps daily planet build.  The CLI reads only the
byte ranges it needs (HTTP range requests), so the ~140 GB planet file is NOT downloaded.  Public OSM raster
tile servers are never contacted (OSM tile policy forbids bulk prefetching).

Steps: (1) check the CLI (``--tools-dir`` / PATH; optionally download the pinned release with SHA-256 check)
and the source build (HTTP HEAD), (2) build the command (``--dry-run`` prints it and stops), (3) run it,
(4) record the source in ``data/sources/catalog.json`` (read-merge-write by ``source_id``) and a
``tiles:`` entry in ``data/processed/fallbacks.json`` (other keys preserved).

Reproduce (PowerShell, from safepath/)::

    backend\\.venv\\Scripts\\python.exe scripts\\extract_pmtiles.py --dry-run
    backend\\.venv\\Scripts\\python.exe scripts\\extract_pmtiles.py --download-cli
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Chicago_Extraction_Area (Req 3.8): N 41.91, S 41.85, W -87.68, E -87.60.
BBOX = {"north": 41.91, "south": 41.85, "west": -87.68, "east": -87.60}
MIN_ZOOM = 12
MAX_ZOOM = 16
SOURCE_ID = "PROTOMAPS-BASEMAP-CHICAGO"
FALLBACK_KEY = "tiles:chicago_basemap"
FALLBACK_PREFIX = "tiles:"
BUILDS_BASE_URL = "https://build.protomaps.com"
BUILDS_PAGE = "https://maps.protomaps.com/builds/"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "tiles" / "chicago.pmtiles"
DEFAULT_TOOLS_DIR = ROOT / "tools" / "bin"

# Pinned CLI release (go-pmtiles); SHA-256 digests published by the GitHub release API.
CLI_VERSION = "1.31.2"
CLI_ASSETS = {
    ("Windows", "x86_64"): (
        f"go-pmtiles_{CLI_VERSION}_Windows_x86_64.zip",
        "a658baa4d7e55020aef6ca17bd9ff9faa1582671266b36f58c52db0ac8e785a1",
    ),
    ("Windows", "arm64"): (
        f"go-pmtiles_{CLI_VERSION}_Windows_arm64.zip",
        "8780a17453c63af757917a694cbbb50b943db89cc3f1b07e6fd62c1ff8e6963b",
    ),
}
CLI_RELEASE_URL = f"https://github.com/protomaps/go-pmtiles/releases/download/v{CLI_VERSION}/"

LIMITATION = (
    "If PMTiles extraction fails or the file is absent, the app falls back to light-use OpenStreetMap raster tiles "
    "in development builds only, or to a blank basemap (no third-party tile requests) in production. OSM tiles "
    "are never bulk-prefetched. Protomaps vector tiles top out at zoom 15; zoom 16 is over-zoomed by the client."
)


def bbox_arg(bbox: dict[str, float] = BBOX) -> str:
    """``--bbox=MIN_LON,MIN_LAT,MAX_LON,MAX_LAT`` value."""
    return f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}"


def build_source_url(build_date: str) -> str:
    if len(build_date) != 8 or not build_date.isdigit():
        raise ValueError("build date must be YYYYMMDD")
    return f"{BUILDS_BASE_URL}/{build_date}.pmtiles"


def build_extract_command(cli: str, source: str, output: str, bbox: dict[str, float] = BBOX,
                          minzoom: int = MIN_ZOOM, maxzoom: int = MAX_ZOOM) -> list[str]:
    if not (0 <= minzoom <= maxzoom <= 22):
        raise ValueError("require 0 <= minzoom <= maxzoom <= 22")
    if not (bbox["south"] < bbox["north"] and bbox["west"] < bbox["east"]):
        raise ValueError("invalid bbox")
    return [cli, "extract", source, output, f"--bbox={bbox_arg(bbox)}",
            f"--minzoom={minzoom}", f"--maxzoom={maxzoom}", "--download-threads=4"]


def format_command(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def find_cli(tools_dir: Path) -> str | None:
    exe = "pmtiles.exe" if os.name == "nt" else "pmtiles"
    local = tools_dir / exe
    if local.is_file():
        return str(local)
    return shutil.which("pmtiles")


def download_cli(tools_dir: Path) -> str:
    """Download the pinned CLI zip, verify SHA-256 and unpack into the git-ignored tools dir."""
    key = (platform.system(), platform.machine().replace("AMD64", "x86_64").replace("ARM64", "arm64"))
    if key not in CLI_ASSETS:
        raise RuntimeError(f"no pinned pmtiles CLI asset for {key}; install it manually (see docs/data-sources.md)")
    name, sha = CLI_ASSETS[key]
    with urllib.request.urlopen(CLI_RELEASE_URL + name, timeout=120) as r:  # noqa: S310 - pinned https URL
        blob = r.read()
    got = hashlib.sha256(blob).hexdigest()
    if got != sha:
        raise RuntimeError(f"checksum mismatch for {name}: expected {sha}, got {got}")
    tools_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extract("pmtiles.exe", tools_dir)
    return str(tools_dir / "pmtiles.exe")


def head_ok(url: str, timeout: float = 30) -> bool:
    try:
        # The Protomaps CDN answers 403 to the default Python user agent.
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "safepath-extract/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001 - availability probe
        return False


def latest_build_date(today: datetime | None = None, lookback: int = 7, probe=head_ok) -> str | None:
    """Newest YYYYMMDD with a published build (the bucket retains the past week)."""
    today = today or datetime.now(timezone.utc)
    for i in range(lookback + 1):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        if probe(build_source_url(d)):
            return d
    return None


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=isinstance(data, dict) and path.name != "catalog.json",
                              ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_record(build_date: str | None, succeeded: bool, output: Path, size: int | None) -> dict[str, Any]:
    return {
        "source_id": SOURCE_ID,
        "organization": "Protomaps (basemap build) / OpenStreetMap contributors (data)",
        "url": BUILDS_PAGE,
        "title": "Protomaps Basemap daily build, extracted to the Chicago_Extraction_Area",
        "published_at": f"{build_date[:4]}-{build_date[4:6]}-{build_date[6:]}" if build_date else None,
        "retrieved_at": _now(),
        "geographic_applicability": f"Chicago bbox W {BBOX['west']}, S {BBOX['south']}, E {BBOX['east']}, N {BBOX['north']}",
        "data_classification": "Current",
        "is_official_source": False,
        "verification_status": "verified" if succeeded else "unknown",
        "page_or_section": "https://docs.protomaps.com/basemaps/downloads",
        # SourceRecord forbids extra fields; build_date/output_bytes live in fallbacks.json.
        "usage_restrictions": [
            "Derived from OpenStreetMap data under the Open Database License (ODbL) 1.0; "
            "Protomaps distributes the basemap as a Produced Work: attribution to OpenStreetMap is required",
            "Attribution text shown on the map: © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)",
            "Basemap is context only, not an official evacuation boundary or route authority",
        ],
    }


def merge_catalog(path: Path, record: dict[str, Any]) -> None:
    data = _read_json(path, {"sources": []})
    sources = [s for s in data.get("sources", []) if s.get("source_id") != record["source_id"]]
    sources.append(record)
    data["sources"] = sources
    _atomic_write_json(path, data)


def merge_fallbacks(path: Path, entries: dict[str, Any]) -> None:
    assert all(k.startswith(FALLBACK_PREFIX) for k in entries)
    data = _read_json(path, {})
    for k in [k for k in data if k.startswith(FALLBACK_PREFIX)]:
        del data[k]
    data.update(entries)
    _atomic_write_json(path, data)


def fallback_entry(status: str, errors: list[str], build_date: str | None, size: int | None) -> dict[str, Any]:
    return {
        "acquisition_status": status,
        "dataset": "protomaps_basemap_chicago",
        "errors": errors,
        "build_date": build_date,
        "output_bytes": size,
        "limitation": LIMITATION,
        "reproduce": "backend\\.venv\\Scripts\\python.exe scripts\\extract_pmtiles.py --download-cli",
        "requirements": ["41.1", "3.8"],
        "retrieved_at": _now(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="print the exact command and exit (no writes)")
    p.add_argument("--download-cli", action="store_true", help="download the pinned pmtiles CLI into --tools-dir")
    p.add_argument("--tools-dir", type=Path, default=DEFAULT_TOOLS_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--build-date", help="Protomaps build YYYYMMDD (default: newest in the past week)")
    p.add_argument("--source", help="override source archive (URL or local path)")
    p.add_argument("--minzoom", type=int, default=MIN_ZOOM)
    p.add_argument("--maxzoom", type=int, default=MAX_ZOOM)
    p.add_argument("--catalog", type=Path, default=ROOT / "data" / "sources" / "catalog.json")
    p.add_argument("--fallbacks", type=Path, default=ROOT / "data" / "processed" / "fallbacks.json")
    a = p.parse_args(argv)

    cli = find_cli(a.tools_dir)
    if cli is None and a.download_cli and not a.dry_run:
        try:
            cli = download_cli(a.tools_dir)
        except Exception as e:  # noqa: BLE001
            print(f"CLI download failed: {e}", file=sys.stderr)
    cli_display = cli or str(a.tools_dir / "pmtiles.exe")

    build_date = a.build_date
    if a.source:
        source = a.source
    else:
        if not build_date and not a.dry_run:
            build_date = latest_build_date()
        elif not build_date:
            build_date = datetime.now(timezone.utc).strftime("%Y%m%d")  # dry-run: placeholder, no network
        source = build_source_url(build_date) if build_date else ""

    cmd = build_extract_command(cli_display, source or "<no-build-found>", str(a.output), BBOX, a.minzoom, a.maxzoom)
    print("Command:", format_command(cmd))
    if a.dry_run:
        print(f"CLI available: {'yes (' + cli + ')' if cli else 'no (run with --download-cli)'}")
        return 0

    errors: list[str] = []
    if cli is None:
        errors.append("pmtiles CLI not found; rerun with --download-cli or install go-pmtiles " + CLI_VERSION)
    if not source:
        errors.append("no Protomaps build found in the past 7 days (network or URL change)")
    if not errors:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.output.with_suffix(".pmtiles.part")
        run_cmd = build_extract_command(cli, source, str(tmp), BBOX, a.minzoom, a.maxzoom)
        t0 = time.time()
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        print(res.stdout[-2000:])
        if res.returncode != 0:
            errors.append(f"pmtiles extract exit {res.returncode}: {(res.stderr or res.stdout)[-500:]}")
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(a.output)
            print(f"Extracted in {time.time() - t0:.1f}s")

    ok = not errors and a.output.is_file()
    size = a.output.stat().st_size if ok else None
    if ok:
        print(f"Wrote {a.output} ({size} bytes)")
        if size > 50 * 1024 * 1024:
            print("Warning: file > 50 MB; it must stay git-ignored (scripts/check_large_files.py).")
    else:
        for e in errors:
            print("FAILED:", e, file=sys.stderr)
    merge_catalog(a.catalog, source_record(build_date if source else None, ok, a.output, size))
    merge_fallbacks(a.fallbacks, {FALLBACK_KEY: fallback_entry(
        "freshly extracted" if ok else "unavailable", errors, build_date, size)})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
