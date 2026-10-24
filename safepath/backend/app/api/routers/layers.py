"""GET /api/layers/{name}: public, read-only GeoJSON layers of the loaded graph artifact (Req 34.3).

The 13 MB buildings layer is served gzip-compressed with an ETag derived from the graph version
and a public Cache-Control, and can be reduced with ``bbox`` (west,south,east,north) and
``simplify`` (coordinate precision ~1 m, only the properties the map styles use).
"""
from __future__ import annotations

import gzip
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from app.routing.runtime import get_graph

router = APIRouter(prefix="/api", tags=["layers"])

LAYER_NAMES = (
    "buildings",
    "cta_stations",
    "simulated_station",
    "extraction_boundary",
    "indoor_building",
)
CACHE_CONTROL = "public, max-age=3600"
# Properties kept by ``simplify`` on buildings (what the map styling and popups need).
_BUILDING_KEEP = (
    "id", "name", "building_type", "building_height_class", "height_m", "height_source",
    "levels", "levels_source", "classification",
)
_COORD_DIGITS = 5


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(p) for p in raw.split(","))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north numbers") from exc
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90) or w > e or s > n:
        raise HTTPException(status_code=400, detail="bbox out of range or inverted")
    return w, s, e, n


def _walk(coords: Any):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from _walk(c)


def _round(coords: Any) -> Any:
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], _COORD_DIGITS), round(coords[1], _COORD_DIGITS)]
    return [_round(c) for c in coords]


def _intersects(geometry: dict | None, bbox: tuple[float, float, float, float]) -> bool:
    """Feature bounding box overlaps ``bbox`` (cheap, conservative)."""
    if not geometry or "coordinates" not in geometry:
        return False
    pts = list(_walk(geometry["coordinates"]))
    if not pts:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, s, e, n = bbox
    return not (max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n)


def _reduce(fc: dict, bbox: tuple[float, float, float, float] | None, simplify: bool, keep: tuple[str, ...] | None) -> dict:
    out = {k: v for k, v in fc.items() if k != "features"}
    feats = []
    for f in fc.get("features", []):
        geom = f.get("geometry")
        if bbox is not None and not _intersects(geom, bbox):
            continue
        if simplify:
            props = f.get("properties") or {}
            if keep is not None:
                props = {k: props[k] for k in keep if k in props}
            geom = {**geom, "coordinates": _round(geom["coordinates"])} if geom else geom
            f = {"type": "Feature", "properties": props, "geometry": geom}
        feats.append(f)
    out["features"] = feats
    return out


def _cache(request: Request) -> dict[tuple, tuple[bytes, bytes]]:
    store = getattr(request.app.state, "layer_cache", None)
    if store is None:
        store = request.app.state.layer_cache = {}
    return store


@router.get("/layers/{name}")
def get_layer(
    name: str,
    request: Request,
    bbox: str | None = None,
    simplify: bool = False,
) -> Response:
    """Return one GeoJSON layer of the loaded graph (public, read-only)."""
    if name not in LAYER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown layer; available: {', '.join(LAYER_NAMES)}")
    graph = get_graph(request.app)
    layer = (graph.layers or {}).get(name)
    if layer is None:
        raise HTTPException(status_code=503, detail=f"layer {name} is not available in the loaded graph")
    box = _parse_bbox(bbox) if bbox else None
    version = graph.version or "unversioned"
    key = (version, name, box, bool(simplify))
    cache = _cache(request)
    if key not in cache:
        data = _reduce(layer, box, simplify, _BUILDING_KEEP if name == "buildings" else None)
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(cache) > 32:
            cache.clear()
        cache[key] = (raw, gzip.compress(raw, compresslevel=6, mtime=0))
    raw, gz = cache[key]
    etag_src = f"{version}:{name}:{box}:{int(bool(simplify))}"
    etag = '"' + format(_stable_hash(etag_src), "x") + '"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL, "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(gz, media_type="application/geo+json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(raw, media_type="application/geo+json", headers=headers)


def _stable_hash(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
