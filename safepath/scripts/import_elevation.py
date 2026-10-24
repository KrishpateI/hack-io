"""Sample USGS 3DEP terrain elevation at every graph Node coordinate (Req 4.1-4.6, 7.1).

Library + CLI.  ``scripts/build_graph.py`` imports :func:`sample_elevations` /
:func:`assign_edge_elevations`; the CLI pre-samples every unique segment endpoint from
``data/processed/streets/segments.jsonl.gz`` so the build step needs no network.

Endpoint (verified by calling it): the USGS National Map 3DEPElevation ImageServer
``https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples``
with ``geometryType=esriGeometryMultipoint`` (WGS84 lon/lat, batched), ``returnFirstValueOnly=true``
and ``f=json``.  The response is ``{"samples": [{"locationId": n, "value": "180.70", "resolution": 1,
"rasterId": ...}]}``; ``value`` is meters, NAVD 88 (3DEP), ``resolution`` is the source cell size in
meters.  Points without data are missing from ``samples`` or carry a non-numeric / sentinel value
and become ``Unknown``.  Never zero, never interpolated.

Rules
-----
* Elevation is stored in meters with ``datum = "NAVD88"``.
* Network failures (after 3 attempts through ``pipeline.fetch``) leave the batch's points ``Unknown``
  and are NOT cached.  Three consecutive failed batches stop sampling (polite to the API); all
  remaining points stay ``Unknown`` and a fallback warning is written.
* Edge confidence (:func:`assign_edge_elevations`): ``Unknown`` if either end is Unknown; ``Low`` for
  bridge / tunnel / elevated / lower-level / underground edges (terrain is not the deck or tunnel
  level); otherwise ``High`` when the infrastructure level is explicitly ``surface`` and both samples
  come from <= 1 m data, ``Medium`` otherwise (coarser data or surface level not stated by a source).
* Disk cache ``data/raw/elevation/cache.json`` (git-ignored) keyed by coordinate rounded to 6 decimals.

Outputs: ``data/processed/elevation/node_elevations.jsonl.gz``, an ``elevation`` section merged into
``data/processed/summary_report.json`` (counts, Unknown count, p5/p95), and ``elevation:`` entries
in ``data/processed/fallbacks.json`` (other keys are preserved).

Run (from ``safepath``)::

    backend\\.venv\\Scripts\\python.exe scripts\\import_elevation.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pipeline.fetch import Fetcher, fetch_with_retries, urllib_fetcher  # noqa: E402

UNKNOWN = "Unknown"
DATUM = "NAVD88"
SOURCE = "USGS 3DEP (3DEPElevation ImageServer getSamples)"
ENDPOINT = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
KEY_DECIMALS = 6
BATCH_SIZE = 100
REQUEST_PAUSE_SECONDS = 0.25
MAX_CONSECUTIVE_FAILURES = 3
VALID_RANGE_M = (-100.0, 1000.0)  # anything outside is a nodata sentinel, not Chicago terrain
HIGH_RESOLUTION_M = 1.0
FALLBACK_PREFIX = "elevation:"
ELEVATED_LEVELS = {"bridge", "tunnel", "elevated", "lower-level", "underground"}

Point = tuple[float, float]  # (lon, lat)


@dataclass(frozen=True)
class ElevationSample:
    meters: float | str  # float or "Unknown"
    source: str = SOURCE
    datum: str = DATUM
    resolution_m: float | str = UNKNOWN

    @property
    def known(self) -> bool:
        return isinstance(self.meters, (int, float))

    def to_json(self) -> dict[str, Any]:
        return {"elevation_m": self.meters, "datum": self.datum, "source": self.source,
                "resolution_m": self.resolution_m}


UNKNOWN_SAMPLE = ElevationSample(UNKNOWN)


def key_for(lon: float, lat: float) -> Point:
    return (round(lon, KEY_DECIMALS), round(lat, KEY_DECIMALS))


def _cache_key(p: Point) -> str:
    return f"{p[0]:.{KEY_DECIMALS}f},{p[1]:.{KEY_DECIMALS}f}"


# -- cache -----------------------------------------------------------------------------------
def load_cache(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _sample_from_cache(entry: Any) -> ElevationSample:
    m, res = entry
    return ElevationSample(UNKNOWN if m is None else float(m), resolution_m=UNKNOWN if res is None else res)


# -- request / parse ---------------------------------------------------------------------------
def build_url(points: list[Point]) -> str:
    geometry = json.dumps({"points": [[x, y] for x, y in points], "spatialReference": {"wkid": 4326}},
                          separators=(",", ":"))
    query = urllib.parse.urlencode({"geometryType": "esriGeometryMultipoint", "geometry": geometry,
                                    "returnFirstValueOnly": "true", "f": "json"})
    return f"{ENDPOINT}?{query}"


def parse_samples(body: bytes, n_points: int) -> list[tuple[float | None, float | None]]:
    """Return (meters|None, resolution|None) per input index; raises ValueError on a bad payload."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("samples"), list):
        raise ValueError(f"unexpected 3DEP response: {str(payload)[:200]}")
    out: list[tuple[float | None, float | None]] = [(None, None)] * n_points
    for s in payload["samples"]:
        try:
            idx = int(s["locationId"])
            value = float(s["value"])
        except (KeyError, TypeError, ValueError):
            continue  # nodata ("NoData", empty, missing) stays Unknown
        if not (0 <= idx < n_points) or not math.isfinite(value) or not (VALID_RANGE_M[0] <= value <= VALID_RANGE_M[1]):
            continue
        try:
            res = float(s.get("resolution"))
        except (TypeError, ValueError):
            res = None
        out[idx] = (value, res)
    return out


def sample_elevations(
    points: Iterable[Point],
    *,
    fetcher: Fetcher = urllib_fetcher,
    cache_path: Path | None = None,
    batch_size: int = BATCH_SIZE,
    pause: float = REQUEST_PAUSE_SECONDS,
    deadline: float | None = None,
    stats: dict[str, Any] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[Point, ElevationSample]:
    """Sample elevation for each (lon, lat); result keyed by the rounded point (:func:`key_for`).

    Uses/extends the disk cache.  Failed batches and points past ``deadline`` (monotonic seconds)
    are ``Unknown`` and uncached.  ``stats`` (if given) receives request/failure counters.
    """
    st = stats if stats is not None else {}
    st.update(requests=0, failed_requests=0, aborted=False, abort_reason=None, cache_hits=0, errors=[])
    cache = load_cache(cache_path)
    result: dict[Point, ElevationSample] = {}
    todo: list[Point] = []
    for lon, lat in dict.fromkeys(key_for(*p) for p in points):
        entry = cache.get(_cache_key((lon, lat)))
        if entry is not None:
            result[(lon, lat)] = _sample_from_cache(entry)
            st["cache_hits"] += 1
        else:
            todo.append((lon, lat))

    consecutive = 0
    done = 0
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    for n, batch in enumerate(batches):
        if st["aborted"]:
            break
        if deadline is not None and time.monotonic() > deadline:
            st["aborted"], st["abort_reason"] = True, "runtime cap reached"
            break
        if n and pause:
            time.sleep(pause)
        st["requests"] += 1
        outcome = fetch_with_retries(fetcher, build_url(batch))
        values = None
        if outcome.ok:
            try:
                values = parse_samples(outcome.data or b"", len(batch))
            except ValueError as exc:
                outcome.errors.append(str(exc))
        if values is None:
            st["failed_requests"] += 1
            st["errors"].extend(outcome.errors[-1:])
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                st["aborted"], st["abort_reason"] = True, f"{consecutive} consecutive failed requests"
            continue
        consecutive = 0
        for p, (m, res) in zip(batch, values):
            cache[_cache_key(p)] = [m, res]
            result[p] = _sample_from_cache([m, res])
        done += len(batch)
        if progress:
            progress(done, len(todo))
        if n % 20 == 19:
            save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    for p in todo:
        result.setdefault(p, UNKNOWN_SAMPLE)
    return result


# -- edges -------------------------------------------------------------------------------------
def _is_elevated_or_lower(segment: dict[str, Any]) -> bool:
    if segment.get("infrastructure_level") in ELEVATED_LEVELS:
        return True
    if segment.get("bridge_flag") is True or segment.get("tunnel_flag") is True:
        return True
    layer = segment.get("layer")
    return isinstance(layer, (int, float)) and not isinstance(layer, bool) and layer < 0


def assign_edge_elevations(segment: dict[str, Any], start: ElevationSample, end: ElevationSample) -> dict[str, Any]:
    """Return ``elevation_start``, ``elevation_end`` (meters or "Unknown") and ``elevation_confidence``."""
    if not (start.known and end.known):
        conf = UNKNOWN
    elif _is_elevated_or_lower(segment):
        conf = "Low"
    else:
        res = [r for r in (start.resolution_m, end.resolution_m) if isinstance(r, (int, float))]
        fine = len(res) == 2 and max(res) <= HIGH_RESOLUTION_M
        conf = "High" if fine and segment.get("infrastructure_level") == "surface" else "Medium"
    return {"elevation_start": start.meters, "elevation_end": end.meters, "elevation_confidence": conf}


def percentiles(samples: Iterable[ElevationSample], qs: tuple[float, ...] = (5, 95)) -> dict[str, float | str]:
    """Linear-interpolated percentiles of the known elevations ("Unknown" when there are none)."""
    vals = sorted(s.meters for s in samples if s.known)
    out: dict[str, float | str] = {}
    for q in qs:
        if not vals:
            out[f"p{q:g}"] = UNKNOWN
            continue
        pos = (len(vals) - 1) * q / 100
        lo, hi = math.floor(pos), math.ceil(pos)
        out[f"p{q:g}"] = round(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo), 3)
    return out


# -- segments / outputs ------------------------------------------------------------------------
def segment_endpoints(segment: dict[str, Any]) -> tuple[Point, Point] | None:
    """First and last coordinate of a segment's WKT LINESTRING (lon, lat)."""
    geom = segment.get("geometry")
    if not isinstance(geom, str) or "(" not in geom:
        return None
    try:
        body = geom[geom.index("(") + 1: geom.rindex(")")].replace("(", "").replace(")", "")
        pairs = [c.split() for c in body.split(",") if c.strip()]
        first, last = pairs[0], pairs[-1]
        return key_for(float(first[0]), float(first[1])), key_for(float(last[0]), float(last[1]))
    except (ValueError, IndexError):
        return None


def read_segments(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_node_elevations(path: Path, samples: dict[Point, ElevationSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for (lon, lat), s in sorted(samples.items()):
            fh.write(json.dumps({"lon": lon, "lat": lat, **s.to_json()}, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def merge_summary_report(path: Path, section: dict[str, Any]) -> None:
    report = _read_json(path, {})
    report["elevation"] = section
    _atomic_write_json(path, report)


def merge_fallbacks(path: Path, entries: dict[str, Any]) -> None:
    """Replace only ``elevation:`` keys; preserve every other key."""
    assert all(k.startswith(FALLBACK_PREFIX) for k in entries)
    data = _read_json(path, {})
    for k in [k for k in data if k.startswith(FALLBACK_PREFIX)]:
        del data[k]
    data.update(entries)
    _atomic_write_json(path, data)


LIMITATION = ("USGS 3DEP is bare-earth terrain (NAVD 88). It does not represent bridge decks, tunnels or "
              "underground levels, and higher terrain does not guarantee flood safety.")


def run(
    *,
    segments_path: Path,
    processed_dir: Path,
    cache_path: Path | None,
    fetcher: Fetcher = urllib_fetcher,
    max_seconds: float | None = 900,
    pause: float = REQUEST_PAUSE_SECONDS,
    batch_size: int = BATCH_SIZE,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    segments = read_segments(segments_path)
    ends = [segment_endpoints(s) for s in segments]
    points = [p for e in ends if e for p in e]
    stats: dict[str, Any] = {}
    deadline = time.monotonic() + max_seconds if max_seconds else None
    samples = sample_elevations(points, fetcher=fetcher, cache_path=cache_path, batch_size=batch_size,
                                pause=pause, deadline=deadline, stats=stats, progress=progress)
    write_node_elevations(processed_dir / "elevation" / "node_elevations.jsonl.gz", samples)

    conf_counts: dict[str, int] = {}
    for seg, e in zip(segments, ends):
        conf = (UNKNOWN if e is None else
                assign_edge_elevations(seg, samples[e[0]], samples[e[1]])["elevation_confidence"])
        conf_counts[conf] = conf_counts.get(conf, 0) + 1
    unknown = sum(1 for s in samples.values() if not s.known)
    pct = percentiles(samples.values())
    section = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE, "endpoint": ENDPOINT, "datum": DATUM, "units": "meters",
        "node_count": len(samples), "known_count": len(samples) - unknown, "unknown_count": unknown,
        "edge_count": len(segments), "edge_confidence_counts": dict(sorted(conf_counts.items())),
        "elevation_percentiles_m": pct, "requests": stats["requests"],
        "failed_requests": stats["failed_requests"], "cache_hits": stats["cache_hits"],
        "aborted": stats["aborted"], "abort_reason": stats["abort_reason"],
        "errors": stats["errors"][:5], "limitation": LIMITATION,
    }
    merge_summary_report(processed_dir / "summary_report.json", section)

    fb: dict[str, Any] = {}
    now = section["generated_at"]
    if samples and unknown == len(samples):
        fb[f"{FALLBACK_PREFIX}3dep_unreachable"] = {
            "warning": "USGS 3DEP unavailable: all elevations recorded as Unknown (never zero); "
                       "edge elevation_confidence is Unknown.", "errors": stats["errors"][:5],
            "retrieved_at": now, "requirements": ["4.5", "7.5"]}
    elif unknown:
        fb[f"{FALLBACK_PREFIX}partial_coverage"] = {
            "warning": f"{unknown} of {len(samples)} node elevations are Unknown (no 3DEP data or "
                       f"request failures).", "abort_reason": stats["abort_reason"],
            "retrieved_at": now, "requirements": ["4.5", "7.5"]}
    fb[f"{FALLBACK_PREFIX}limitation"] = {"warning": LIMITATION, "requirements": ["4.6"]}
    merge_fallbacks(processed_dir / "fallbacks.json", fb)
    return section


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--segments", type=Path, default=ROOT / "data" / "processed" / "streets" / "segments.jsonl.gz")
    p.add_argument("--processed-dir", type=Path, default=ROOT / "data" / "processed")
    p.add_argument("--cache", type=Path, default=ROOT / "data" / "raw" / "elevation" / "cache.json")
    p.add_argument("--max-seconds", type=float, default=900)
    args = p.parse_args(argv)
    if not args.segments.exists():
        print(f"missing {args.segments}; run scripts/process_streets.py first", file=sys.stderr)
        return 1
    t0 = time.monotonic()
    last = [0.0]

    def progress(done: int, total: int) -> None:
        if time.monotonic() - last[0] > 15:
            last[0] = time.monotonic()
            print(f"  sampled {done}/{total}", flush=True)

    s = run(segments_path=args.segments, processed_dir=args.processed_dir, cache_path=args.cache,
            max_seconds=args.max_seconds, progress=progress)
    print(f"nodes={s['node_count']} known={s['known_count']} unknown={s['unknown_count']} "
          f"requests={s['requests']} failed={s['failed_requests']} cache_hits={s['cache_hits']} "
          f"p5/p95={s['elevation_percentiles_m']} edges={s['edge_confidence_counts']} "
          f"aborted={s['aborted']} ({time.monotonic() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
