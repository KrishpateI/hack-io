"""Geographic utilities: region bounds, coordinate classification, clipping,
haversine distance and building height classes."""
from __future__ import annotations

import math
from enum import Enum
from typing import Any, Iterable, Sequence

NORTH = 41.91
SOUTH = 41.85
WEST = -87.68
EAST = -87.60
BBOX = (WEST, SOUTH, EAST, NORTH)  # (min_lon, min_lat, max_lon, max_lat)

EARTH_RADIUS_M = 6_371_008.8

LOW_RISE = "Low-rise"
MID_RISE = "Mid-rise"
HIGH_RISE = "High-rise"
UNKNOWN = "Unknown"


class CoordinateStatus(str, Enum):
    INVALID = "invalid"
    OUTSIDE_REGION = "outside-region"
    INSIDE = "inside"


def _as_number(value: Any) -> float | None:
    """Return a finite float for real numbers; None for bool/str/None/NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def classify_coordinate(lat: Any, lon: Any) -> CoordinateStatus:
    """Classify a lat/lon pair. Boundary points are inside."""
    la, lo = _as_number(lat), _as_number(lon)
    if la is None or lo is None or not -90 <= la <= 90 or not -180 <= lo <= 180:
        return CoordinateStatus.INVALID
    if SOUTH <= la <= NORTH and WEST <= lo <= EAST:
        return CoordinateStatus.INSIDE
    return CoordinateStatus.OUTSIDE_REGION


def classify_endpoints(origin: Sequence[Any], destination: Sequence[Any]) -> dict:
    """Classify origin and destination ``(lat, lon)`` pairs.

    Returns ``{"status", "failed"}`` where status is the worst classification
    (invalid > outside-region > inside) and ``failed`` is "origin",
    "destination", "both" or None when both are inside.
    """
    def one(p: Any) -> CoordinateStatus:
        try:
            lat, lon = p[0], p[1]
        except (TypeError, IndexError, KeyError):
            return CoordinateStatus.INVALID
        return classify_coordinate(lat, lon)

    o, d = one(origin), one(destination)
    if o == d == CoordinateStatus.INSIDE:
        return {"status": CoordinateStatus.INSIDE, "failed": None}
    for bad in (CoordinateStatus.INVALID, CoordinateStatus.OUTSIDE_REGION):
        bo, bd = o == bad, d == bad
        if bo or bd:
            # Report the worst class; identify the endpoints with that class.
            if bo and bd:
                failed = "both"
            elif bo:
                failed = "origin"
            else:
                failed = "destination"
            # An endpoint with a different non-inside class is also failing.
            if (o != CoordinateStatus.INSIDE) and (d != CoordinateStatus.INSIDE):
                failed = "both"
            return {"status": bad, "failed": failed}
    raise AssertionError("unreachable")  # pragma: no cover


def in_bbox(lon: float, lat: float) -> bool:
    return WEST <= lon <= EAST and SOUTH <= lat <= NORTH


def clip_point(lon: float, lat: float) -> tuple[float, float] | None:
    return (lon, lat) if in_bbox(lon, lat) else None


def _clip_segment(p0, p1):
    """Liang-Barsky clip of a segment to BBOX; None if fully outside."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - WEST), (dx, EAST - x0), (-dy, y0 - SOUTH), (dy, NORTH - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    def pt(t):
        # Clamp so float error never leaves a vertex outside the bounds.
        return (min(max(x0 + t * dx, WEST), EAST), min(max(y0 + t * dy, SOUTH), NORTH))
    return pt(t0), pt(t1)


def clip_linestring(coords: Iterable[Sequence[float]]) -> list[list[tuple[float, float]]]:
    """Clip a (lon, lat) polyline to the bbox; returns zero or more parts."""
    pts = [(float(c[0]), float(c[1])) for c in coords]
    parts: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    if len(pts) == 1:
        c = clip_point(*pts[0])
        return [[c]] if c else []
    for a, b in zip(pts, pts[1:]):
        seg = _clip_segment(a, b)
        if seg is None:
            if cur:
                parts.append(cur)
                cur = []
            continue
        s, e = seg
        if cur and cur[-1] == s:
            cur.append(e)
        else:
            if cur:
                parts.append(cur)
            cur = [s, e]
        if e != b:  # exited the box
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)
    return parts


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _positive(value: Any) -> float | None:
    n = _as_number(value)
    return n if n is not None and n > 0 else None


def building_height_class(height: Any = None, floors: Any = None) -> str:
    """Low-rise/Mid-rise/High-rise from height (m), else floors, else Unknown.

    Zero, negative and non-numeric values are treated as absent.
    """
    h = _positive(height)
    if h is not None:
        return LOW_RISE if h < 12 else MID_RISE if h < 35 else HIGH_RISE
    f = _positive(floors)
    if f is not None:
        return LOW_RISE if f < 4 else MID_RISE if f < 12 else HIGH_RISE
    return UNKNOWN
