"""Merge Chicago Data Portal and OSM street segments into normalized segments (Req 3.3-3.6, 7.1).

Inputs (written by ``scripts/download_chicago_data.py``; ``data/processed/<dataset>/data.json``,
falling back to the latest raw copy under ``data/raw/<dataset>/``):

* ``cdp_centerlines``  Chicago Data Portal Street Center Lines (GeoJSON, lon/lat).
* ``osm_streets``      OSM vehicle ways (Overpass JSON, ``out geom``).
* ``osm_pedestrian``   OSM footway/pedestrian/path/steps/corridor ways.

Output: ``data/processed/streets/segments.jsonl.gz`` (one JSON object per line; git-ignored,
regenerate with this script) and a ``street_processing`` section merged into
``data/processed/summary_report.json`` (counts per source, matches, conflicts, Unknown counts per
attribute, warnings). Metric operations (lengths, buffers, matching) use EPSG:26916 (NAD83 / UTM
16N) computed with a built-in transverse Mercator, so the light stack (shapely, numpy) is enough.

Source precedence (also documented in ``docs/data-sources.md``)
---------------------------------------------------------------
* Geometry and street name: Chicago Data Portal (CDP) when a CDP segment exists, else OSM.
* Access / bridge / tunnel / layer / one-way / road class / wheelchair: OSM when it states a value.
  CDP has no bridge, tunnel, layer or pedestrian-access attributes, so those never come from CDP.
  One-way falls back to CDP ``dir_travel`` only when OSM does not state it.
* Road class: OSM ``highway`` value when matched or OSM-only; CDP-only segments keep
  ``cdp_class_<code>`` (the portal's numeric/letter class, not translated).
* A CDP segment is matched to the OSM way that best overlaps it (within 12 m, same normalized
  street name; if names disagree or are missing a >= 90 % overlap is required). Unmatched segments
  from either source are kept with that single source. Remaining parts of a matched OSM way that
  lie outside every matched CDP buffer are kept as OSM-only remainder segments.
* When both sources state a value and they disagree (street name, one-way vs two-way) the
  precedence above decides, the retained source is recorded in ``attribute_sources`` and a
  conflict (plus warning) is recorded on the segment and in the summary.

Normalization rules
-------------------
* Absent attributes are the string ``"Unknown"``; nothing is defaulted from road class or guessed.
* ``infrastructure_level`` is assigned only where a source identifies it: ``bridge``/``tunnel``
  from the bridge/tunnel tags, ``elevated`` for ``bridge=viaduct``, ``lower-level`` for a negative
  ``layer`` without a tunnel tag, ``surface`` for an explicit ``layer=0`` (or explicit
  ``bridge=no``/``tunnel=no`` with no non-zero layer). Everything else is ``Unknown``.
* ``river_crossing`` is ``True`` only when the segment crosses a CDP river line (class ``RIV``,
  which is itself excluded from the street segments); otherwise ``Unknown`` (no source says it does
  not cross, so it is never defaulted to ``False``).
* Pedestrian paths become walking segments. ``highway=steps`` is wheelchair ``inaccessible``;
  other paths keep wheelchair accessibility ``Unknown`` unless a ``wheelchair`` tag says otherwise.

Run (from the ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\process_streets.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import shapely
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.geo import clip_linestring  # noqa: E402

UNKNOWN = "Unknown"
TARGET_CRS = "EPSG:26916"
MATCH_BUFFER_M = 12.0
MATCH_MIN_OVERLAP = 0.6  # same normalized name
MATCH_MIN_OVERLAP_NO_NAME = 0.9  # names disagree or missing
REMAINDER_MIN_M = 10.0
MIN_SEGMENT_M = 0.5
CDP_DATASET = "cdp_centerlines"
OSM_STREETS = "osm_streets"
OSM_PEDESTRIAN = "osm_pedestrian"
DATASETS = (CDP_DATASET, OSM_STREETS, OSM_PEDESTRIAN)
ATTRIBUTES = (
    "name",
    "road_type",
    "oneway",
    "pedestrian_access",
    "bridge_flag",
    "tunnel_flag",
    "layer",
    "infrastructure_level",
    "river_crossing",
    "mobility_accessibility",
)


# -- projection (EPSG:4326 -> EPSG:26916, GRS80 transverse Mercator, central meridian -87) -------
_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_LON0 = math.radians(-87.0)
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16,
    13 * _N**2 / 48 - 3 * _N**3 / 5,
    61 * _N**3 / 240,
)


def to_utm16n(lon: np.ndarray | float, lat: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    """Project lon/lat degrees to EPSG:26916 easting/northing meters (Krueger series)."""
    phi = np.radians(np.asarray(lat, dtype=float))
    dlam = np.radians(np.asarray(lon, dtype=float)) - _LON0
    e = math.sqrt(_F * (2 - _F))
    t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
    xi_p = np.arctan2(t, np.cos(dlam))
    eta_p = np.arctanh(np.sin(dlam) / np.sqrt(1 + t**2))
    xi, eta = xi_p.copy(), eta_p.copy()
    for j, a in enumerate(_ALPHA, start=1):
        xi = xi + a * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta = eta + a * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    return 500000.0 + _K0 * _AA * eta, _K0 * _AA * xi


def project_geom(geom: BaseGeometry) -> BaseGeometry:
    def _fn(coords: np.ndarray) -> np.ndarray:
        x, y = to_utm16n(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, _fn)


# -- name normalization ------------------------------------------------------------------------
_DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
_TYPES = {
    "STREET": "ST", "ST": "ST", "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD",
    "BLVD": "BLVD", "ROAD": "RD", "RD": "RD", "DRIVE": "DR", "DR": "DR", "PLACE": "PL", "PL": "PL",
    "COURT": "CT", "CT": "CT", "PARKWAY": "PKWY", "PKWY": "PKWY", "HIGHWAY": "HWY", "HWY": "HWY",
    "LANE": "LN", "LN": "LN", "TERRACE": "TER", "TER": "TER", "WAY": "WAY", "PLAZA": "PLZ",
    "PLZ": "PLZ", "EXPRESSWAY": "EXPY", "EXPY": "EXPY", "SQUARE": "SQ", "SQ": "SQ",
    "CIRCLE": "CIR", "CIR": "CIR", "ALLEY": "ALY", "ALY": "ALY", "BRIDGE": "BRG", "BRG": "BRG",
}


def normalize_name(name: Any) -> str | None:
    """Core street name without direction and type tokens, upper-case, or None when absent."""
    if not isinstance(name, str):
        return None
    tokens = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND ")).split()
    core = [t for t in tokens if t not in _DIRECTIONS and t not in _TYPES]
    if not core:  # e.g. "North Avenue" is a name, not a direction + type
        core = [t for t in tokens if t not in _DIRECTIONS] or tokens
    return " ".join(core) or None


def cdp_display_name(props: dict[str, Any]) -> str | None:
    core = props.get("street_nam")
    if not isinstance(core, str) or not core.strip():
        return None
    parts = [props.get("pre_dir"), core, props.get("street_typ"), props.get("suf_dir")]
    return " ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())


# -- tag parsing -------------------------------------------------------------------------------
_YES = {"yes", "true", "1", "designated", "permissive", "official", "limited"}


def _bool_tag(value: Any) -> bool | str:
    """OSM bridge/tunnel-style tag: 'no' -> False, any other value -> True, absent -> Unknown."""
    if not isinstance(value, str) or not value.strip():
        return UNKNOWN
    return value.strip().lower() != "no"


def _layer(tags: dict[str, Any]) -> int | str:
    raw = tags.get("layer")
    try:
        return int(str(raw).strip().split(";")[0])
    except (TypeError, ValueError):
        return UNKNOWN


def _oneway_osm(tags: dict[str, Any]) -> str:
    value = str(tags.get("oneway", "")).strip().lower()
    if value in {"yes", "true", "1"}:
        return "forward"
    if value == "-1" or value == "reverse":
        return "reverse"
    if value in {"no", "false", "0"}:
        return "no"
    return UNKNOWN


def _oneway_cdp(props: dict[str, Any]) -> str:
    return {"B": "no", "F": "forward", "T": "reverse"}.get(str(props.get("dir_travel", "")).strip().upper(), UNKNOWN)


def _pedestrian_access(tags: dict[str, Any], highway: str, pedestrian_source: bool) -> bool | str:
    foot = str(tags.get("foot", "")).strip().lower()
    access = str(tags.get("access", "")).strip().lower()
    if foot in _YES:
        return True
    if foot == "no":
        return False
    if access in {"no", "private"}:
        return False
    if pedestrian_source and highway in {"footway", "pedestrian", "steps", "corridor"}:
        return True  # these highway classes are defined by OSM as for pedestrians
    return UNKNOWN


def _wheelchair(tags: dict[str, Any], highway: str) -> str:
    if highway == "steps":
        return "inaccessible"
    value = str(tags.get("wheelchair", "")).strip().lower()
    if value in {"yes", "designated"}:
        return "accessible"
    if value == "no":
        return "inaccessible"
    return UNKNOWN


def _infrastructure_level(tags: dict[str, Any], bridge: Any, tunnel: Any, layer: Any) -> tuple[str, str | None]:
    """(level, warning). Only source-identified levels; everything else Unknown."""
    if bridge is True and tunnel is True:
        return UNKNOWN, "both bridge and tunnel tags are set"
    if bridge is True:
        return ("elevated" if str(tags.get("bridge")).lower() == "viaduct" else "bridge"), None
    if tunnel is True:
        return "tunnel", None
    if isinstance(layer, int) and layer < 0:
        return "lower-level", None
    if layer == 0:
        return "surface", None
    if layer == UNKNOWN and bridge is False and tunnel is not True:
        return "surface", None
    if layer == UNKNOWN and tunnel is False and bridge is not True:
        return "surface", None
    return UNKNOWN, None


# -- segment construction ----------------------------------------------------------------------
def _wkt(geom: BaseGeometry) -> str:
    return shapely_wkt.dumps(geom, rounding_precision=7)


def _lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if hasattr(geom, "geoms"):
        return [part for g in geom.geoms for part in _lines(g)]
    return []


class Item:
    """One source line (a clipped CDP feature or OSM way part) in lon/lat and projected form."""

    def __init__(self, source: str, source_id: str, lonlat: BaseGeometry, props: dict[str, Any]) -> None:
        self.source = source
        self.source_id = source_id
        self.lonlat = lonlat
        self.props = props
        self.metric = project_geom(lonlat)
        self.length = float(self.metric.length)


def load_json(processed_dir: Path, raw_dir: Path, dataset: str) -> tuple[Any | None, str | None]:
    """(data, warning). Prefer processed output, else latest raw copy, else (None, warning)."""
    path = processed_dir / dataset / "data.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError):
        pass
    try:
        meta = json.loads((raw_dir / dataset / "latest.json").read_text(encoding="utf-8"))
        data = json.loads((raw_dir / dataset / meta["file"]).read_text(encoding="utf-8"))
        return data, f"{dataset}: processed output missing, used raw copy retrieved at {meta.get('retrieved_at')}"
    except (OSError, ValueError, KeyError):
        return None, f"{dataset}: source missing (no processed output and no raw copy)"


def cdp_items(fc: dict[str, Any]) -> tuple[list[Item], list[BaseGeometry], int]:
    """Street items, river (class RIV) metric lines, number of skipped features."""
    items: list[Item] = []
    rivers: list[BaseGeometry] = []
    skipped = 0
    seen: Counter[str] = Counter()
    for feat in fc.get("features", []):
        geom, props = feat.get("geometry"), feat.get("properties") or {}
        try:
            lines = [LineString(p) for p in (geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]])]
            lines = [ln for ln in lines if len(ln.coords) >= 2]
        except (TypeError, KeyError, ValueError):
            lines = []
        if not lines:
            skipped += 1
            continue
        merged = linemerge(MultiLineString(lines)) if len(lines) > 1 else lines[0]
        if str(props.get("class", "")).upper() == "RIV":
            rivers.append(project_geom(merged))
            continue
        sid = str(props.get("trans_id") or props.get("objectid") or f"idx{len(items)}")
        seen[sid] += 1
        if seen[sid] > 1:
            sid = f"{sid}.{seen[sid]}"
        items.append(Item(CDP_DATASET, sid, merged, props))
    return items, rivers, skipped


def osm_items(doc: dict[str, Any], source: str) -> list[Item]:
    items: list[Item] = []
    for el in doc.get("elements", []):
        if el.get("type", "way") != "way" or not el.get("geometry"):
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        parts = [p for p in clip_linestring(coords) if len(p) >= 2]
        for n, part in enumerate(parts):
            sid = str(el["id"]) if len(parts) == 1 else f"{el['id']}.{n + 1}"
            items.append(Item(source, sid, LineString(part), el.get("tags") or {}))
    return items


def _osm_attrs(tags: dict[str, Any], pedestrian_source: bool) -> dict[str, Any]:
    highway = str(tags.get("highway", ""))
    bridge, tunnel, layer = _bool_tag(tags.get("bridge")), _bool_tag(tags.get("tunnel")), _layer(tags)
    level, warning = _infrastructure_level(tags, bridge, tunnel, layer)
    return {
        "name": tags.get("name") if isinstance(tags.get("name"), str) and tags.get("name") else UNKNOWN,
        "road_type": highway or UNKNOWN,
        "oneway": _oneway_osm(tags),
        "pedestrian_access": _pedestrian_access(tags, highway, pedestrian_source),
        "bridge_flag": bridge,
        "tunnel_flag": tunnel,
        "layer": layer,
        "infrastructure_level": level,
        "mobility_accessibility": _wheelchair(tags, highway),
        "_warning": warning,
    }


def _travel_modes(source: str, attrs: dict[str, Any]) -> list[str]:
    if source == OSM_PEDESTRIAN:
        modes = ["walking"]
        if attrs["mobility_accessibility"] != "inaccessible":
            modes.append("wheelchair")
        return modes
    modes = ["driving"]
    if attrs["pedestrian_access"] is True:
        modes.append("walking")
        if attrs["mobility_accessibility"] != "inaccessible":
            modes.append("wheelchair")
    return modes


def _river_flag(metric: BaseGeometry, rivers: list[BaseGeometry], tree: STRtree | None) -> bool | str:
    if tree is None:
        return UNKNOWN
    for idx in tree.query(metric):
        if metric.crosses(rivers[int(idx)]):
            return True
    return UNKNOWN


class Builder:
    def __init__(self) -> None:
        self.segments: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.conflicts: list[dict[str, Any]] = []
        self.ids: set[str] = set()

    def add(
        self,
        seg_id: str,
        *,
        geom_item: Item,
        sources: list[str],
        source_ids: dict[str, str],
        attrs: dict[str, Any],
        attribute_sources: dict[str, str],
        rivers: list[BaseGeometry],
        river_tree: STRtree | None,
        travel_source: str,
        conflicts: list[dict[str, Any]],
    ) -> None:
        base = seg_id
        n = 1
        while seg_id in self.ids:
            n += 1
            seg_id = f"{base}~{n}"
        self.ids.add(seg_id)
        warnings = [w for w in [attrs.pop("_warning", None)] if w]
        for c in conflicts:
            warnings.append(
                f"{seg_id}: {c['attribute']} conflict (cdp={c['cdp']!r}, osm={c['osm']!r}); retained {c['retained_source']}"
            )
        self.conflicts.extend({"segment_id": seg_id, **c} for c in conflicts)
        for w in warnings:
            self.warnings.append(w if w.startswith(seg_id) else f"{seg_id}: {w}")
        record = {
            "segment_id": seg_id,
            "geometry": _wkt(geom_item.lonlat),
            "distance_meters": round(geom_item.length, 3),
            **{k: attrs[k] for k in ATTRIBUTES if k != "river_crossing"},
            "river_crossing": _river_flag(geom_item.metric, rivers, river_tree),
            "travel_mode": _travel_modes(travel_source, attrs),
            "sources": sources,
            "source_ids": source_ids,
            "retained_source": geom_item.source,
            "attribute_sources": attribute_sources,
            "conflicts": conflicts,
            "warnings": warnings,
            "crs": "EPSG:4326",
            "classification": "Static",
        }
        self.segments.append(record)


def match_cdp_to_osm(cdp: list[Item], osm: list[Item]) -> dict[int, tuple[int, bool, bool]]:
    """cdp index -> (osm index, name_agrees, name_missing). Best overlap wins, same name preferred."""
    if not osm:
        return {}
    tree = STRtree([o.metric for o in osm])
    buffers: dict[int, BaseGeometry] = {}
    result: dict[int, tuple[int, bool, bool]] = {}
    for ci, c in enumerate(cdp):
        if c.length < MIN_SEGMENT_M:
            continue
        cname = normalize_name(cdp_display_name(c.props))
        best: tuple[tuple[int, float], int, bool, bool] | None = None
        for oi in tree.query(c.metric.buffer(MATCH_BUFFER_M)):
            oi = int(oi)
            if oi not in buffers:
                buffers[oi] = osm[oi].metric.buffer(MATCH_BUFFER_M)
            overlap = c.metric.intersection(buffers[oi]).length / c.length
            oname = normalize_name(osm[oi].props.get("name"))
            missing = cname is None or oname is None
            agree = not missing and cname == oname
            need = MATCH_MIN_OVERLAP if agree else MATCH_MIN_OVERLAP_NO_NAME
            if overlap < need:
                continue
            key = (1 if agree else 0, overlap)
            if best is None or key > best[0]:
                best = (key, oi, agree, missing)
        if best:
            result[ci] = (best[1], best[2], best[3])
    return result


def merge_streets(
    cdp: list[Item], osm_street: list[Item], osm_ped: list[Item], rivers: list[BaseGeometry]
) -> tuple[Builder, dict[str, Any]]:
    builder = Builder()
    river_tree = STRtree(rivers) if rivers else None
    matches = match_cdp_to_osm(cdp, osm_street)
    stats: dict[str, Any] = {"matched_pairs": len(matches), "name_conflicts": 0, "oneway_conflicts": 0}

    matched_cdp_by_osm: dict[int, list[int]] = {}
    for ci, (oi, _, _) in matches.items():
        matched_cdp_by_osm.setdefault(oi, []).append(ci)

    common = dict(rivers=rivers, river_tree=river_tree)
    for ci, c in enumerate(cdp):
        cdp_name = cdp_display_name(c.props) or UNKNOWN
        cdp_oneway = _oneway_cdp(c.props)
        cdp_class = str(c.props.get("class") or "").strip()
        if ci in matches:
            oi, agree, missing = matches[ci]
            o = osm_street[oi]
            attrs = _osm_attrs(o.props, pedestrian_source=False)
            sources, conflicts = [CDP_DATASET, OSM_STREETS], []
            attr_src = {"geometry": "cdp", "name": "cdp" if cdp_name != UNKNOWN else ("osm" if attrs["name"] != UNKNOWN else "none")}
            if cdp_name == UNKNOWN:
                pass  # OSM name (or Unknown) already in attrs
            else:
                if not missing and not agree:
                    conflicts.append({"attribute": "name", "cdp": cdp_name, "osm": attrs["name"], "retained_source": CDP_DATASET})
                    stats["name_conflicts"] += 1
                attrs["name"] = cdp_name
            osm_ow = attrs["oneway"]
            if osm_ow == UNKNOWN:
                attrs["oneway"] = cdp_oneway
                attr_src["oneway"] = "cdp" if cdp_oneway != UNKNOWN else "none"
            else:
                attr_src["oneway"] = "osm"
                if cdp_oneway != UNKNOWN and (cdp_oneway == "no") != (osm_ow == "no"):
                    conflicts.append({"attribute": "oneway", "cdp": cdp_oneway, "osm": osm_ow, "retained_source": OSM_STREETS})
                    stats["oneway_conflicts"] += 1
            for a in ("road_type", "pedestrian_access", "bridge_flag", "tunnel_flag", "layer", "infrastructure_level", "mobility_accessibility"):
                attr_src[a] = "osm" if attrs[a] != UNKNOWN else "none"
            ids = {CDP_DATASET: c.source_id, OSM_STREETS: o.source_id}
            builder.add(f"seg-cdp-{c.source_id}", geom_item=c, sources=sources, source_ids=ids, attrs=attrs,
                        attribute_sources=attr_src, travel_source=OSM_STREETS, conflicts=conflicts, **common)
        else:
            attrs = {
                "name": cdp_name,
                "road_type": f"cdp_class_{cdp_class}" if cdp_class else UNKNOWN,
                "oneway": cdp_oneway,
                "pedestrian_access": UNKNOWN,
                "bridge_flag": UNKNOWN,
                "tunnel_flag": UNKNOWN,
                "layer": UNKNOWN,
                "infrastructure_level": UNKNOWN,
                "mobility_accessibility": UNKNOWN,
            }
            attr_src = {a: ("cdp" if attrs[a] != UNKNOWN else "none") for a in attrs}
            attr_src["geometry"] = "cdp"
            builder.add(f"seg-cdp-{c.source_id}", geom_item=c, sources=[CDP_DATASET], source_ids={CDP_DATASET: c.source_id},
                        attrs=attrs, attribute_sources=attr_src, travel_source=CDP_DATASET, conflicts=[], **common)

    stats["cdp_only"] = len(cdp) - len(matches)
    remainder_segments = 0
    osm_consumed = 0
    osm_only = 0
    for oi, o in enumerate(osm_street):
        covering = matched_cdp_by_osm.get(oi)
        pieces: list[LineString]
        if covering:
            cover = unary_union([cdp[ci].metric.buffer(MATCH_BUFFER_M) for ci in covering])
            rest = _lines(o.metric.difference(cover))
            pieces = [p for p in rest if p.length >= REMAINDER_MIN_M]
            if not pieces:
                osm_consumed += 1
                continue
            remainder_segments += len(pieces)
        else:
            pieces = [o.metric]
            osm_only += 1
        for n, piece in enumerate(pieces, start=1):
            attrs = _osm_attrs(o.props, pedestrian_source=False)
            attr_src = {a: ("osm" if attrs[a] != UNKNOWN else "none") for a in attrs if a != "_warning"}
            attr_src["geometry"] = "osm"
            suffix = f"-r{n}" if covering else ""
            item = o if not covering else _metric_item(o, piece)
            builder.add(f"seg-osm-{o.source_id}{suffix}", geom_item=item, sources=[OSM_STREETS],
                        source_ids={OSM_STREETS: o.source_id}, attrs=attrs, attribute_sources=attr_src,
                        travel_source=OSM_STREETS, conflicts=[], **common)
    stats.update(osm_street_only=osm_only, osm_street_remainders=remainder_segments, osm_street_merged_into_cdp=osm_consumed)

    for o in osm_ped:
        attrs = _osm_attrs(o.props, pedestrian_source=True)
        attr_src = {a: ("osm" if attrs[a] != UNKNOWN else "none") for a in attrs if a != "_warning"}
        attr_src["geometry"] = "osm"
        builder.add(f"seg-osmped-{o.source_id}", geom_item=o, sources=[OSM_PEDESTRIAN], source_ids={OSM_PEDESTRIAN: o.source_id},
                    attrs=attrs, attribute_sources=attr_src, travel_source=OSM_PEDESTRIAN, conflicts=[], **common)
    stats["osm_pedestrian_segments"] = len(osm_ped)
    return builder, stats


def _inverse_lonlat(geom: BaseGeometry, template: Item) -> BaseGeometry:
    """Map a sub-line of ``template.metric`` back to lon/lat by projecting onto the template line."""
    pts = []
    for x, y in geom.coords:
        from shapely.geometry import Point

        d = template.metric.project(Point(x, y))
        p = template.lonlat.interpolate(d / template.length, normalized=True) if template.length else template.lonlat.interpolate(0)
        pts.append((p.x, p.y))
    return LineString(pts)


def _metric_item(template: Item, piece: LineString) -> Item:
    item = Item.__new__(Item)
    item.source, item.source_id, item.props = template.source, template.source_id, template.props
    item.metric, item.length = piece, float(piece.length)
    item.lonlat = _inverse_lonlat(piece, template)
    return item


# -- output ------------------------------------------------------------------------------------
def build_summary(builder: Builder, stats: dict[str, Any], inputs: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    segs = builder.segments
    per_source: Counter[str] = Counter()
    for s in segs:
        per_source["+".join(s["sources"])] += 1
    unknown = {a: sum(1 for s in segs if s[a] == UNKNOWN) for a in ATTRIBUTES}
    all_warnings = warnings + builder.warnings
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "crs": TARGET_CRS,
        "segment_count": len(segs),
        "segments_per_source": dict(sorted(per_source.items())),
        "inputs": inputs,
        "matching": {
            **stats,
            "match_buffer_m": MATCH_BUFFER_M,
            "min_overlap_same_name": MATCH_MIN_OVERLAP,
            "min_overlap_name_differs_or_missing": MATCH_MIN_OVERLAP_NO_NAME,
        },
        "conflict_count": len(builder.conflicts),
        "conflicts_by_attribute": dict(Counter(c["attribute"] for c in builder.conflicts)),
        "unknown_counts": unknown,
        "infrastructure_level_counts": dict(Counter(s["infrastructure_level"] for s in segs)),
        "river_crossing_true": sum(1 for s in segs if s["river_crossing"] is True),
        "warning_count": len(all_warnings),
        "warnings": all_warnings[:50],
        "output": "data/processed/streets/segments.jsonl.gz",
    }


def write_segments(path: Path, segments: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as fh:
        for s in segments:
            fh.write(json.dumps(s, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_segments(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def merge_summary_report(path: Path, summary: dict[str, Any]) -> None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    report["street_processing"] = summary
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(path)


def process(processed_dir: Path, raw_dir: Path, output: Path | None = None) -> dict[str, Any] | None:
    """Run the merge. Returns the summary, or None when no street source exists in any form."""
    warnings: list[str] = []
    data: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    for ds in DATASETS:
        doc, warn = load_json(processed_dir, raw_dir, ds)
        data[ds] = doc
        if warn:
            warnings.append(warn)
        inputs[ds] = {"available": doc is not None}
    if all(data[ds] is None for ds in DATASETS):
        return None  # fatal: saved outputs untouched (Req 7.7)

    cdp, rivers, skipped = ([], [], 0)
    if data[CDP_DATASET] is not None:
        cdp, rivers, skipped = cdp_items(data[CDP_DATASET])
        if skipped:
            warnings.append(f"{CDP_DATASET}: {skipped} features without usable geometry skipped")
        inputs[CDP_DATASET].update(street_features=len(cdp), river_lines=len(rivers))
    else:
        warnings.append("river_crossing stays Unknown: CDP river lines unavailable")
    osm_s = osm_items(data[OSM_STREETS], OSM_STREETS) if data[OSM_STREETS] is not None else []
    osm_p = osm_items(data[OSM_PEDESTRIAN], OSM_PEDESTRIAN) if data[OSM_PEDESTRIAN] is not None else []
    inputs[OSM_STREETS]["items"] = len(osm_s)
    inputs[OSM_PEDESTRIAN]["items"] = len(osm_p)

    builder, stats = merge_streets(cdp, osm_s, osm_p, rivers)
    summary = build_summary(builder, stats, inputs, warnings)
    write_segments(output or processed_dir / "streets" / "segments.jsonl.gz", builder.segments)
    merge_summary_report(processed_dir / "summary_report.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    p.add_argument("--processed-dir", type=Path, default=ROOT / "data" / "processed")
    args = p.parse_args(argv)
    summary = process(args.processed_dir, args.raw_dir)
    if summary is None:
        print("No street source available in any form; saved outputs left unchanged", file=sys.stderr)
        return 1
    for key in ("segment_count", "segments_per_source", "matching", "conflict_count", "conflicts_by_attribute",
                "unknown_counts", "infrastructure_level_counts", "river_crossing_true", "warning_count"):
        print(f"{key}: {summary[key]}")
    for w in summary["warnings"][:10]:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
