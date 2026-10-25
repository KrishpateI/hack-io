"""Shared constants/helpers for SafePath data scripts."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DEMO = ROOT / "data" / "demo"
SOURCES = ROOT / "data" / "sources"

# Initial data-extraction bounding box (NOT an official evacuation boundary).
BBOX = {"north": 41.91, "south": 41.85, "west": -87.68, "east": -87.60}

USER_AGENT = "SafePath-HackOHIO-2026/0.1 (research prototype; contact: team@safepath.invalid)"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for d in (RAW, PROCESSED, DEMO, SOURCES):
        d.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)


def load_manifest() -> dict:
    return read_json(RAW / "manifest.json", {"datasets": {}})


def record_manifest(key: str, **info) -> None:
    m = load_manifest()
    m["datasets"][key] = {**m["datasets"].get(key, {}), **info}
    m["updated_at"] = now_iso()
    write_json(RAW / "manifest.json", m, indent=2)


def http_get(url: str, *, params=None, data=None, timeout=180, retries=3, stream=False):
    """GET/POST with retries. Returns requests.Response or raises last error."""
    import requests

    last = None
    for attempt in range(retries):
        try:
            if data is not None:
                r = requests.post(url, data=data, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            else:
                r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout, stream=stream)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
        except Exception as e:  # network errors
            last = e
        time.sleep(2 + attempt * 3)
    raise last  # type: ignore[misc]
