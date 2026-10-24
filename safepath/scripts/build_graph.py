"""Build the versioned routing Graph artifact from the processed Chicago data (Req 2.2, 5.6, 6.4, 7.1, 7.8, 12).

Inputs (all produced by earlier pipeline steps; no network is used here):

* ``data/processed/streets/segments.jsonl.gz``          merged street/pedestrian segments (process_streets.py)
* ``data/processed/elevation/node_elevations.jsonl.gz``  endpoint elevations, NAVD88 m (import_elevation.py)
* ``data/processed/osm_buildings/data.json``             OSM building footprints (Overpass JSON)
* ``data/processed/cta_gtfs/data.json``                  CTA stations (GeoJSON from GTFS)

Output: a new version under ``data/processed/graphs/`` via ``app.services.graph_artifact.write_artifact``
(never overwrites an old version; ``latest.json`` is updated last) and a ``graph_build`` section merged
into ``data/processed/summary_report.json``.

Documented choices
------------------
Nodes
    One ``intersection`` Node per distinct segment endpoint, snapped by coordinate rounded to 6 decimals
    (~0.1 m).  ``node_id = "n<lat>_<lon>"`` with micro-degree integers (deterministic).  Node elevation
    comes from the elevation output (never defaulted to 0; ``Unknown`` when no sample).  Node
    ``elevation_conf`` is High (<= 1 m source data), Medium, Low when any incident segment is a
    bridge/tunnel/elevated/lower-level (terrain is not the deck), or Unknown.
Edges
    Directed.  ``edge_id = "<segment_id>:f"`` (digitised direction) or ``":r"`` (reverse); deterministic.
    Self-loop segments (start == end node) are skipped and counted.  One-way applies to *driving only*:

    * ``forward`` / ``reverse``: the driving mode exists only in the permitted direction; the opposite
      direction keeps only the walking/wheelchair modes (if the segment has any) and is omitted otherwise.
    * ``no``: both directions carry all modes.
    * ``Unknown``: conservative-but-usable.  Walking and wheelchair edges always exist in both
      directions (pedestrians may use either side of a street).  Driving is ALSO created in both
      directions so the area stays routable, but every driving edge of an Unknown-one-way segment carries
      the ``hazard_flag`` ``oneway_unknown`` so a cost model can penalise the assumption.  Nothing is
      guessed from road class.
Travel time (``estimated_travel_time_seconds`` is the single stored baseline that the cost model
multiplies by a per-mode factor, so it must be the *walking* baseline for pedestrian edges)
    * edges that carry walking or wheelchair: ``distance / 1.4 m/s``.  The wheelchair profile applies
      the configured 1.4 factor (about 1.0 m/s; the spec's ~1.1 m/s figure is within rounding of that);
    * driving-only edges: ``distance / speed`` where speed is a documented road-class default
      (``DRIVING_SPEED_MPS``: motorway 24.6 m/s, trunk 20, primary 15.6, secondary 13.4, tertiary 11.2,
      other/unknown 8.9 m/s (~20 mph), service 4.5 m/s).  These are planning defaults, not measured speeds.
    Mixed (driving + walking) edges therefore use the walking baseline; driving ETAs over such edges are
    conservative (slower).
Attributes
    ``bridge_flag``, ``tunnel_flag``, ``infrastructure_level``, ``pedestrian_access``,
    ``mobility_accessibility``, ``road_type`` are copied from the segment, ``Unknown`` preserved.
    ``current_closure_status`` is always ``Unknown`` (no closure source), classification ``Static``.
    Elevation start/end/confidence use ``import_elevation.assign_edge_elevations`` (swapped for the
    reverse direction).
Building entrances
    Connected ONLY if the source has entrance nodes.  The OSM buildings extract contains building ways
    only (no ``entrance=*`` nodes), so no entrance Nodes/Edges are created and a limitation is recorded.
Simulated station
    A two-level station model (street + platform) placed at the real CTA Clark/Lake station coordinate
    from the GTFS data, joined ONLY by Level_Transition edges (``platform_stair`` and ``ramp``) and
    attached to the real street graph by one pair of Simulated link edges at the nearest Node.  Everything is
    classified Simulated, ids start with ``sim-station-``, and it is NOT the real station layout.
Connectivity
    Weakly connected components are computed for the whole graph and per travel mode.  Nothing is
    dropped; sizes and a warning for disconnected islands are recorded in the summary.
Noding (``node_segments``)
    The merged segments are not noded (a street way spans many junctions; footways end on streets
    without a shared endpoint; using raw endpoints alone left 87 % of nodes in islands).  Segments are
    cut at endpoints, at vertices shared by two or more segments, and at T-junctions (an unshared
    endpoint within ``SNAP_TOLERANCE_M`` = 3 m of another segment is snapped onto it).  Piece distances
    are the segment distance split proportionally.  Edge ids of split segments are ``<segment_id>~<n>:f|r``.
    Cut points are not segment endpoints, so their elevation is sampled (cached) from 3DEP in the same
    way as the pre-sampled nodes; if unreachable they stay ``Unknown``.
Indoor demo building
    NOT baked in; ``app.routing.indoor`` merges it at runtime.

Run (from the ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\build_graph.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

import import_elevation as ie  # noqa: E402
from app.geo import BBOX, building_height_class, haversine  # noqa: E402
from app.models.graph import Edge, Graph, Node  # noqa: E402
from app.services.graph_artifact import write_artifact  # noqa: E402

UNKNOWN = "Unknown"
WALK_SPEED_MPS = 1.4
DRIVING_SPEED_MPS = {
    "motorway": 24.6, "motorway_link": 17.9, "trunk": 20.0, "trunk_link": 15.6, "primary": 15.6,
    "primary_link": 11.2, "secondary": 13.4, "secondary_link": 11.2, "tertiary": 11.2,
    "tertiary_link": 8.9, "residential": 8.9, "unclassified": 8.9, "living_street": 4.5, "service": 4.5,
}
DEFAULT_DRIVING_SPEED_MPS = 8.9
PEDESTRIAN_MODES = ("walking", "wheelchair")
MIN_DISTANCE_M = 0.1
SIM_PREFIX = "sim-station-"
SIM_STATION_NAME = "Clark/Lake"
SIM_NOTICE = (
    "SIMULATED two-level station model placed at the Clark/Lake CTA station coordinate. It is a "
    "simplified demonstration (street level + platform joined by stair and ramp Level_Transitions) "
    "and is NOT the real station layout."
)
SIM_SOURCE = "SIMULATED station model (not the real Clark/Lake layout)"
SOURCE_RECORD_IDS = ("src-cdp_centerlines", "src-osm_streets", "src-osm_pedestrian", "src-osm_buildings", "src-cta_gtfs")
ENTRANCE_LIMITATION = (
    "Building entrances were not connected to the graph: the OSM buildings extract contains building "
    "footprints only (no entrance nodes), so no entrance location is supported by source data."
)
MAX_STORED_WARNINGS = 50
ELEVATION_ATTRS = ("elevation_start", "elevation_end", "elevation_confidence")


# -- geometry helpers --------------------------------------------------------------------------
def parse_wkt_coords(geom: Any) -> list[tuple[float, float]] | None:
    """(lon, lat) pairs of a WKT LINESTRING, or None when unusable."""
    if not isinstance(geom, str) or "(" not in geom:
        return None
    try:
        body = geom[geom.index("(") + 1: geom.rindex(")")].replace("(", "").replace(")", "")
        pairs = [c.split() for c in body.split(",") if c.strip()]
        coords = [(float(p[0]), float(p[1])) for p in pairs]
    except (ValueError, IndexError):
        return None
    return coords if len(coords) >= 2 else None


def to_wkt(coords: list[tuple[float, float]]) -> str:
    return "LINESTRING(" + ", ".join(f"{lon:.7f} {lat:.7f}" for lon, lat in coords) + ")"


def node_id_for(lon: float, lat: float) -> str:
    return f"n{round(lat * 1e6)}_{round(lon * 1e6)}"


def driving_speed(road_type: Any) -> float:
    return DRIVING_SPEED_MPS.get(road_type, DEFAULT_DRIVING_SPEED_MPS) if isinstance(road_type, str) else DEFAULT_DRIVING_SPEED_MPS


# -- network -----------------------------------------------------------------------------------
def load_node_elevations(path: Path | None) -> dict[ie.Point, ie.ElevationSample]:
    out: dict[ie.Point, ie.ElevationSample] = {}
    if path is None or not path.exists():
        return out
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            m = r.get("elevation_m", UNKNOWN)
            m = float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else UNKNOWN
            out[ie.key_for(r["lon"], r["lat"])] = ie.ElevationSample(
                m, resolution_m=r.get("resolution_m", UNKNOWN) if m != UNKNOWN else UNKNOWN)
    return out


def _edge_flags(segment: dict[str, Any], driving: bool) -> list[str]:
    return ["oneway_unknown"] if driving and segment.get("oneway", UNKNOWN) == UNKNOWN else []


def _directions(segment: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """[(suffix 'f'|'r', modes)] edges to create for a segment."""
    modes = [m for m in segment.get("travel_mode", []) if m in ("walking", "wheelchair", "driving")]
    walk = [m for m in modes if m != "driving"]
    oneway = segment.get("oneway", UNKNOWN)
    if oneway == "forward":
        return [("f", modes)] + ([("r", walk)] if walk else [])
    if oneway == "reverse":
        return ([("f", walk)] if walk else []) + [("r", modes)]
    return [("f", modes), ("r", modes)]  # "no" and Unknown: both directions (Unknown flagged on driving)


def _travel_time(distance: float, modes: list[str], road_type: Any) -> float:
    if any(m in PEDESTRIAN_MODES for m in modes):
        return distance / WALK_SPEED_MPS
    return distance / driving_speed(road_type)


_COS_REF = math.cos(math.radians(41.88))
_M_PER_DEG = 111_320.0
SNAP_TOLERANCE_M = 3.0


def _xy(p: ie.Point) -> tuple[float, float]:
    return p[0] * _COS_REF * _M_PER_DEG, p[1] * _M_PER_DEG


def _lonlat(x: float, y: float) -> ie.Point:
    return ie.key_for(x / (_COS_REF * _M_PER_DEG), y / _M_PER_DEG)


def node_segments(
    segments: list[dict[str, Any]], tolerance_m: float = SNAP_TOLERANCE_M
) -> tuple[list[tuple[dict[str, Any], str, list[ie.Point], float]], dict[str, Any]]:
    """Split segments into noded pieces ``(segment, piece_id, [(lon, lat)...], distance_m)``.

    The source segments are NOT noded: a street way runs across many junctions and footways end on
    streets without a shared endpoint.  Pieces are cut at (1) every segment endpoint, (2) every
    vertex (rounded to 6 decimals) that two or more segments share, and (3) T-junctions: an endpoint
    that touches nobody else is snapped within ``tolerance_m`` onto the nearest other segment (onto an
    existing cut when one is within the tolerance, else a new cut is inserted at the projection).
    Snapping moves an endpoint by at most ``tolerance_m``.
    """
    import bisect
    from collections import defaultdict
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree

    stats: Counter[str] = Counter()
    recs: list[tuple[dict[str, Any], list[ie.Point]]] = []
    for seg in segments:
        raw = parse_wkt_coords(seg.get("geometry"))
        if raw is None:
            stats["segments_skipped_bad_geometry"] += 1
            continue
        pts: list[ie.Point] = []
        for c in raw:
            k = ie.key_for(*c)
            if not pts or pts[-1] != k:
                pts.append(k)
        if len(pts) < 2:
            stats["segments_skipped_degenerate"] += 1
            continue
        recs.append((seg, pts))

    owners: dict[ie.Point, set[int]] = defaultdict(set)
    for i, (_, pts) in enumerate(recs):
        for k in pts:
            owners[k].add(i)

    lines = [LineString([_xy(k) for k in pts]) for _, pts in recs]
    cum: list[list[float]] = []
    splits: list[list[tuple[float, ie.Point]]] = []
    for i, (_, pts) in enumerate(recs):
        xy = [_xy(k) for k in pts]
        c = [0.0]
        for a, b in zip(xy, xy[1:]):
            c.append(c[-1] + math.dist(a, b))
        cum.append(c)
        last = len(pts) - 1
        splits.append([(c[j], k) for j, k in enumerate(pts) if j in (0, last) or len(owners[k]) > 1])

    inserted: list[list[tuple[float, ie.Point]]] = [[] for _ in recs]
    override: dict[tuple[int, int], ie.Point] = {}  # (segment, 0|1) -> snapped endpoint
    tree = STRtree(lines) if lines else None
    if tree is not None and tolerance_m > 0:
        for i, (_, pts) in enumerate(recs):
            for end, k in ((0, pts[0]), (1, pts[-1])):
                if len(owners[k]) != 1 or pts[0] == pts[-1]:
                    continue
                pt = Point(*_xy(k))
                best_j, best_d = -1, tolerance_m + 1.0
                for j in tree.query(pt.buffer(tolerance_m)):
                    j = int(j)
                    if j == i:
                        continue
                    d = lines[j].distance(pt)
                    if d < best_d:
                        best_j, best_d = j, d
                if best_j < 0 or best_d > tolerance_m:
                    continue
                along = lines[best_j].project(pt)
                near = [(abs(a - along), key) for a, key in splits[best_j] if abs(a - along) <= tolerance_m]
                if near:
                    target = min(near)[1]
                else:
                    on = lines[best_j].interpolate(along)
                    target = _lonlat(on.x, on.y)
                    entry = (along, target)
                    bisect.insort(splits[best_j], entry)
                    inserted[best_j].append(entry)
                    stats["t_junction_cuts_inserted"] += 1
                if target != k:
                    override[(i, end)] = target
                    stats["endpoints_snapped"] += 1

    pieces: list[tuple[dict[str, Any], str, list[ie.Point], float]] = []
    for i, (seg, pts) in enumerate(recs):
        sid = str(seg.get("segment_id", "?"))
        merged: list[tuple[float, ie.Point, bool]] = []
        ins = sorted(inserted[i])
        split_keys = {k for _, k in splits[i]}
        n = len(pts)
        for j, k in enumerate(pts):
            while ins and ins[0][0] < cum[i][j]:
                a, key = ins.pop(0)
                merged.append((a, key, True))
            merged.append((cum[i][j], k, j in (0, n - 1) or k in split_keys and len(owners[k]) > 1))
        for a, key in ins:
            merged.append((a, key, True))
        if (i, 0) in override:
            merged[0] = (merged[0][0], override[(i, 0)], True)
        if (i, 1) in override:
            merged[-1] = (merged[-1][0], override[(i, 1)], True)
        seq = [k for _, k, _ in merged]
        hav = [haversine(b[1], b[0], a[1], a[0]) for a, b in zip(seq, seq[1:])]
        total_hav = sum(hav) or 1.0
        seg_dist = seg.get("distance_meters")
        seg_dist = float(seg_dist) if isinstance(seg_dist, (int, float)) and not isinstance(seg_dist, bool) else total_hav
        scale = seg_dist / total_hav
        cut_idx = [j for j, (_, _, s) in enumerate(merged) if s]
        multi = len(cut_idx) > 2
        for p, (a, b) in enumerate(zip(cut_idx, cut_idx[1:]), start=1):
            coords = seq[a: b + 1]
            dist = sum(hav[a:b]) * scale
            if coords[0] == coords[-1]:
                stats["self_loop_pieces_skipped"] += 1
                continue
            if dist < MIN_DISTANCE_M:
                dist = MIN_DISTANCE_M
                stats["pieces_below_min_distance"] += 1
            pieces.append((seg, f"{sid}~{p}" if multi else sid, coords, dist))
        if multi:
            stats["segments_split"] += 1
    stats["segments_in"] = len(recs)
    stats["pieces"] = len(pieces)
    return pieces, dict(stats)


def build_network(
    segments: list[dict[str, Any]],
    elevations: dict[ie.Point, ie.ElevationSample],
    built_at: datetime,
    tolerance_m: float = SNAP_TOLERANCE_M,
) -> tuple[list[Node], list[Edge], dict[str, Any]]:
    stats: dict[str, Any] = Counter()
    warnings: list[str] = []
    coords_of: dict[str, tuple[float, float]] = {}  # node_id -> (lon, lat)
    degraded: set[str] = set()  # nodes touched by bridge/tunnel/elevated/lower-level segments
    edges: list[Edge] = []

    pieces, nstats = node_segments(segments, tolerance_m)
    stats.update(nstats)
    for seg, pid, coords, dist in pieces:
        a_lon, a_lat = coords[0]
        b_lon, b_lat = coords[-1]
        a_id, b_id = node_id_for(a_lon, a_lat), node_id_for(b_lon, b_lat)
        coords_of.setdefault(a_id, (a_lon, a_lat))
        coords_of.setdefault(b_id, (b_lon, b_lat))
        if ie._is_elevated_or_lower(seg):
            degraded.update((a_id, b_id))
        start = elevations.get((a_lon, a_lat), ie.UNKNOWN_SAMPLE)
        end = elevations.get((b_lon, b_lat), ie.UNKNOWN_SAMPLE)
        sources = seg.get("sources") or []
        source_ids = {str(k): str(v) for k, v in (seg.get("source_ids") or {}).items()}
        source_ids["segment_id"] = str(seg.get("segment_id", "?"))
        for suffix, modes in _directions(seg):
            fwd = suffix == "f"
            frm, to = (a_id, b_id) if fwd else (b_id, a_id)
            pts = coords if fwd else coords[::-1]
            elev = ie.assign_edge_elevations(seg, start if fwd else end, end if fwd else start)
            edges.append(Edge(
                edge_id=f"{pid}:{suffix}",
                from_node=frm, to_node=to, geometry=to_wkt(pts),
                distance_meters=round(dist, 3),
                estimated_travel_time_seconds=round(_travel_time(dist, modes, seg.get("road_type")), 3),
                travel_mode=modes,  # type: ignore[arg-type]
                source="+".join(sources) if sources else UNKNOWN,
                source_ids=source_ids,
                road_type=seg.get("road_type", UNKNOWN) or UNKNOWN,
                bridge_flag=seg.get("bridge_flag", UNKNOWN),
                tunnel_flag=seg.get("tunnel_flag", UNKNOWN),
                infrastructure_level=seg.get("infrastructure_level", UNKNOWN),
                pedestrian_access=seg.get("pedestrian_access", UNKNOWN),
                mobility_accessibility=seg.get("mobility_accessibility", UNKNOWN),
                current_closure_status=UNKNOWN,
                hazard_flags=_edge_flags(seg, "driving" in modes),
                data_timestamp=built_at, edge_kind="street", classification="Static",
                **elev,
            ))
            stats["edges_created"] += 1

    nodes: list[Node] = []
    for nid, (lon, lat) in coords_of.items():
        s = elevations.get((lon, lat), ie.UNKNOWN_SAMPLE)
        if not s.known:
            conf = UNKNOWN
        elif nid in degraded:
            conf = "Low"
        else:
            conf = "High" if isinstance(s.resolution_m, (int, float)) and s.resolution_m <= ie.HIGH_RESOLUTION_M else "Medium"
        nodes.append(Node(node_id=nid, lat=lat, lon=lon, kind="intersection", level="surface",
                          elevation_m=s.meters, elevation_conf=conf))
        if not s.known:
            stats["nodes_without_elevation"] += 1
    return nodes, edges, {"counters": dict(stats), "warnings": warnings}


# -- simulated station -------------------------------------------------------------------------
def find_station(gtfs: dict[str, Any] | None, name: str = SIM_STATION_NAME) -> dict[str, Any] | None:
    for f in (gtfs or {}).get("features", []):
        if (f.get("properties") or {}).get("name") == name and f.get("geometry", {}).get("type") == "Point":
            return f
    return None


def build_simulated_station(
    station: dict[str, Any], real_nodes: list[Node], built_at: datetime
) -> tuple[list[Node], list[Edge], dict[str, Any]]:
    lon, lat = (round(float(c), 6) for c in station["geometry"]["coordinates"][:2])
    props = station.get("properties") or {}
    street = Node(node_id=f"{SIM_PREFIX}street", lat=lat, lon=lon, kind="pedestrian_junction", level="surface")
    # Platform sits at the same coordinate but on its own level (no elevation claim).
    platform = Node(node_id=f"{SIM_PREFIX}platform", lat=lat, lon=lon, kind="indoor", level="platform",
                    building_id=f"{SIM_PREFIX}clark-lake")
    nodes = [street, platform]

    def edge(eid: str, a: Node, b: Node, pts: list[tuple[float, float]], dist: float, speed: float, *,
             kind: str, transition: str | None, road_type: str, modes: list[str], access: str) -> Edge:
        return Edge(
            edge_id=eid, from_node=a.node_id, to_node=b.node_id, geometry=to_wkt(pts),
            distance_meters=round(max(dist, MIN_DISTANCE_M), 2),
            estimated_travel_time_seconds=round(max(dist, MIN_DISTANCE_M) / speed, 2),
            travel_mode=modes,  # type: ignore[arg-type]
            source=SIM_SOURCE, source_ids={"gtfs_station_id": str(props.get("station_id", UNKNOWN))},
            road_type=road_type, pedestrian_access=True, mobility_accessibility=access,  # type: ignore[arg-type]
            data_timestamp=built_at, edge_kind=kind, transition_type=transition,  # type: ignore[arg-type]
            classification="Simulated",
        )

    edges: list[Edge] = []
    here = [(lon, lat), (lon, lat)]
    for tt, label, dist, speed, modes, access in (
        ("platform_stair", "stair", 12.0, 0.8, ["walking"], "inaccessible"),
        ("ramp", "ramp", 40.0, 1.0, ["walking", "wheelchair"], UNKNOWN),
    ):
        for a, b, d in ((street, platform, "down"), (platform, street, "up")):
            edges.append(edge(f"{SIM_PREFIX}t-{label}-{d}", a, b, here, dist, speed, kind="level_transition",
                              transition=tt, road_type=f"simulated_station_{label}", modes=modes, access=access))
    link_info: dict[str, Any] = {"linked_to": None}
    if real_nodes:
        lats = np.array([n.lat for n in real_nodes])
        lons = np.array([n.lon for n in real_nodes])
        # equirectangular distance is plenty to pick the nearest node
        d2 = ((lats - lat) * 111_320.0) ** 2 + ((lons - lon) * 111_320.0 * math.cos(math.radians(lat))) ** 2
        near = real_nodes[int(np.argmin(d2))]
        d = haversine(lat, lon, near.lat, near.lon)
        link_info = {"linked_to": near.node_id, "link_distance_m": round(d, 1)}
        for a, b, tag, pts in ((street, near, "in", [(lon, lat), (near.lon, near.lat)]),
                               (near, street, "out", [(near.lon, near.lat), (lon, lat)])):
            edges.append(edge(f"{SIM_PREFIX}link-{tag}", a, b, pts, d, WALK_SPEED_MPS, kind="street",
                              transition=None, road_type="simulated_station_link",
                              modes=["walking", "wheelchair"], access=UNKNOWN))
    meta = {"simulated": True, "classification": "Simulated", "notice": SIM_NOTICE, "source": SIM_SOURCE,
            "gtfs_station_id": props.get("station_id"), "gtfs_station_name": props.get("name")}
    feats: list[dict[str, Any]] = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {**meta, "role": "street_level", "node_id": street.node_id, "level": "surface",
                        "name": "SIMULATED station street level"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {**meta, "role": "platform", "node_id": platform.node_id, "level": "platform",
                        "name": "SIMULATED station platform"}},
    ]
    for e in edges:
        if e.edge_kind == "level_transition":
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {**meta, "role": "level_transition", "edge_id": e.edge_id,
                                         "transition_type": e.transition_type,
                                         "mobility_accessibility": e.mobility_accessibility}})
    layer = {"type": "FeatureCollection", "name": "SIMULATED station (Clark/Lake location)",
             "properties": meta, "features": feats}
    return nodes, edges, {"layer": layer, **link_info}


# -- layers ------------------------------------------------------------------------------------
_NUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(m|meters?|metres?|ft|feet|')?\s*$", re.IGNORECASE)


def parse_height_m(raw: Any) -> float | None:
    """Metres from an OSM ``height`` tag (plain number, 'm' or feet); None when absent/unparseable."""
    if not isinstance(raw, str):
        return None
    m = _NUM.match(raw)
    if not m:
        return None
    v = float(m.group(1)) * (0.3048 if (m.group(2) or "").lower() in ("ft", "feet", "'") else 1.0)
    return round(v, 2) if v > 0 else None


def parse_levels(raw: Any) -> float | None:
    if not isinstance(raw, str):
        return None
    try:
        v = float(raw.strip())
    except ValueError:
        return None
    return v if v > 0 and math.isfinite(v) else None


def buildings_layer(doc: dict[str, Any] | None, decimals: int = 6) -> tuple[dict[str, Any], dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    cls: Counter[str] = Counter()
    stats: Counter[str] = Counter()
    entrance_nodes = 0
    for el in (doc or {}).get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "node":
            entrance_nodes += 1 if "entrance" in tags else 0
            continue
        geom = el.get("geometry")
        if el.get("type") != "way" or not geom:
            stats["skipped_no_geometry"] += 1
            continue
        ring: list[list[float]] = []
        for p in geom:
            c = [round(p["lon"], decimals), round(p["lat"], decimals)]
            if not ring or ring[-1] != c:
                ring.append(c)
        if len(ring) < 4 or ring[0] != ring[-1]:
            stats["skipped_not_closed"] += 1
            continue
        height = parse_height_m(tags.get("height"))
        levels = parse_levels(tags.get("building:levels"))
        klass = building_height_class(height, levels)
        cls[klass] += 1
        props: dict[str, Any] = {
            "id": f"osm-way-{el.get('id')}",
            "building_height_class": klass,
            "height_m": height if height is not None else UNKNOWN,
            "height_source": "osm:height" if height is not None else UNKNOWN,
            "levels": levels if levels is not None else UNKNOWN,
            "levels_source": "osm:building:levels" if levels is not None else UNKNOWN,
            "classification": "Static",
        }
        if isinstance(tags.get("name"), str) and tags["name"]:
            props["name"] = tags["name"]
        if isinstance(tags.get("building"), str):
            props["building_type"] = tags["building"]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props})
    layer = {"type": "FeatureCollection", "name": "OSM building footprints", "features": feats,
             "properties": {"source": "OpenStreetMap contributors (Overpass)", "classification": "Static",
                            "note": "Height class derives only from OSM height/building:levels; otherwise Unknown."}}
    return layer, {"features": len(feats), "height_class_counts": dict(cls), "entrance_nodes_in_source": entrance_nodes,
                   **dict(stats)}


def stations_layer(gtfs: dict[str, Any] | None) -> dict[str, Any]:
    feats = []
    for f in (gtfs or {}).get("features", []):
        p = f.get("properties") or {}
        wc = p.get("wheelchair_boarding")
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": {
            "id": str(p.get("station_id", UNKNOWN)), "name": p.get("name", UNKNOWN),
            "routes": [r.get("name") for r in p.get("routes", []) if isinstance(r, dict)],
            "wheelchair_boarding": wc if wc in ("accessible", "inaccessible") else UNKNOWN,
            "classification": "Static", "source": "CTA GTFS static feed",
        }})
    return {"type": "FeatureCollection", "name": "CTA stations", "features": feats,
            "properties": {"note": "Station location and GTFS wheelchair_boarding only; entrance safety is not inferred."}}


def boundary_layer() -> dict[str, Any]:
    w, s, e, n = BBOX
    return {"type": "FeatureCollection", "name": "Chicago extraction boundary", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
        "properties": {"name": "Chicago_Extraction_Area", "bbox": [w, s, e, n]}}]}


# -- connectivity ------------------------------------------------------------------------------
def _components(n: int, pairs: list[tuple[int, int]]) -> np.ndarray:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)


def connectivity(graph: Graph) -> tuple[dict[str, Any], list[str]]:
    idx = {n.node_id: i for i, n in enumerate(graph.nodes)}
    out: dict[str, Any] = {}
    warnings: list[str] = []

    def one(label: str, edges: list[Edge]) -> None:
        touched = sorted({idx[x] for e in edges for x in (e.from_node, e.to_node)})
        if not touched:
            out[label] = {"nodes": 0, "components": 0, "largest": 0, "top_sizes": [], "islands_outside_largest": 0}
            return
        local = {g: i for i, g in enumerate(touched)}
        roots = _components(len(touched), [(local[idx[e.from_node]], local[idx[e.to_node]]) for e in edges])
        sizes = sorted(Counter(roots.tolist()).values(), reverse=True)
        out[label] = {"nodes": len(touched), "components": len(sizes), "largest": sizes[0],
                      "largest_fraction": round(sizes[0] / len(touched), 4), "top_sizes": sizes[:10],
                      "islands_outside_largest": len(sizes) - 1, "nodes_outside_largest": len(touched) - sizes[0]}
        if len(sizes) > 1:
            warnings.append(
                f"{label}: {len(sizes)} disconnected components; largest has {sizes[0]} of {len(touched)} nodes "
                f"({len(touched) - sizes[0]} nodes on {len(sizes) - 1} islands; kept in the graph, not dropped)")

    one("all", graph.edges)
    for mode in ("walking", "wheelchair", "driving"):
        one(mode, [e for e in graph.edges if mode in e.travel_mode])
    return out, warnings


# -- orchestration -----------------------------------------------------------------------------
def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_segments(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def summarize(graph: Graph) -> dict[str, Any]:
    e = graph.edges
    attrs = ("bridge_flag", "tunnel_flag", "infrastructure_level", "pedestrian_access", "mobility_accessibility",
             "road_type", "elevation_confidence", "current_closure_status")
    return {
        "nodes": len(graph.nodes),
        "edges": len(e),
        "nodes_by_kind": dict(Counter(n.kind for n in graph.nodes)),
        "nodes_elevation_unknown": sum(1 for n in graph.nodes if n.elevation_m == UNKNOWN),
        "edges_by_kind": dict(Counter(x.edge_kind for x in e)),
        "edges_by_classification": dict(Counter(x.classification for x in e)),
        "edges_by_travel_mode": dict(Counter("+".join(x.travel_mode) for x in e)),
        "edges_unknown_counts": {a: sum(1 for x in e if getattr(x, a) == UNKNOWN) for a in attrs},
        "edges_elevation_unknown": sum(1 for x in e if x.elevation_start == UNKNOWN or x.elevation_end == UNKNOWN),
        "edges_oneway_unknown_driving": sum(1 for x in e if "oneway_unknown" in x.hazard_flags),
    }


def build(
    processed_dir: Path,
    built_at: datetime | None = None,
    *,
    cache_path: Path | None = None,
    max_sample_seconds: float = 300.0,
    fetcher: Any = ie.urllib_fetcher,
) -> dict[str, Any] | None:
    """Build and write the artifact. Returns the ``graph_build`` summary, or None if segments are missing.

    Elevation of noded cut points that the pre-sampled file lacks is fetched through ``fetcher``
    (cached at ``cache_path``); pass a failing fetcher to stay offline (those nodes stay Unknown)."""
    t0 = time.monotonic()
    built_at = built_at or datetime.now(timezone.utc)
    seg_path = processed_dir / "streets" / "segments.jsonl.gz"
    if not seg_path.exists():
        return None
    warnings: list[str] = []
    segments = _read_segments(seg_path)
    elevations = load_node_elevations(processed_dir / "elevation" / "node_elevations.jsonl.gz")
    if not elevations:
        warnings.append("node elevations missing: all elevations are Unknown")
    nodes, edges, net = build_network(segments, elevations, built_at)
    sampled: dict[str, Any] = {"requested": 0}
    missing = [(n.lon, n.lat) for n in nodes if n.elevation_m == UNKNOWN]
    if missing:
        # Noding adds cut points that are not segment endpoints, so the pre-sampled file lacks them.
        # Sample only those (cached in data/raw/elevation/cache.json); failures stay Unknown, never 0.
        st: dict[str, Any] = {}
        extra = ie.sample_elevations(missing, cache_path=cache_path, deadline=time.monotonic() + max_sample_seconds,
                                     stats=st, fetcher=fetcher)
        got = {k: v for k, v in extra.items() if v.known}
        sampled = {"requested": len(missing), "obtained": len(got), "requests": st.get("requests"),
                   "failed_requests": st.get("failed_requests"), "cache_hits": st.get("cache_hits"),
                   "aborted": st.get("aborted"), "abort_reason": st.get("abort_reason")}
        if st.get("aborted") or st.get("failed_requests"):
            warnings.append(f"elevation sampling for noded cut points incomplete: {st.get('abort_reason') or 'request failures'}")
        if got:
            elevations = {**elevations, **got}
            nodes, edges, net = build_network(segments, elevations, built_at)
    warnings.extend(net["warnings"])

    gtfs = _read_json(processed_dir / "cta_gtfs" / "data.json")
    buildings_doc = _read_json(processed_dir / "osm_buildings" / "data.json")
    limitations: list[str] = []
    if buildings_doc is None:
        warnings.append("osm_buildings missing: buildings layer is empty")
    bl, bstats = buildings_layer(buildings_doc)
    if bstats.get("entrance_nodes_in_source", 0) == 0:
        limitations.append(ENTRANCE_LIMITATION)
    else:
        limitations.append(
            f"{bstats['entrance_nodes_in_source']} entrance nodes exist in the source but are not connected "
            "to the graph in this version.")
    layers = {"buildings": bl, "cta_stations": stations_layer(gtfs), "extraction_boundary": boundary_layer()}

    station = find_station(gtfs)
    sim_info: dict[str, Any] = {"built": False}
    if station is None:
        warnings.append(f"CTA station {SIM_STATION_NAME} not found in GTFS data: simulated station not built")
    else:
        s_nodes, s_edges, sim = build_simulated_station(station, nodes, built_at)
        layers["simulated_station"] = sim.pop("layer")
        nodes += s_nodes
        edges += s_edges
        sim_info = {"built": True, "station": SIM_STATION_NAME, "notice": SIM_NOTICE, **sim}

    graph = Graph.build(nodes, edges)
    warnings.extend(graph.warnings)

    comps, comp_warnings = connectivity(graph)
    warnings.extend(comp_warnings)

    summary_path = processed_dir / "summary_report.json"
    report = _read_json(summary_path, {})
    pct = {k: float(v) for k, v in (report.get("elevation", {}).get("elevation_percentiles_m", {}) or {}).items()
           if isinstance(v, (int, float)) and not isinstance(v, bool)}
    known = {r.get("source_id") for r in _read_json(processed_dir / "source_records.json", []) or []}
    source_ids = [s for s in SOURCE_RECORD_IDS if s in known]

    version = write_artifact(processed_dir, graph, layers, built_at=built_at,
                             elevation_percentiles=pct, source_record_ids=source_ids)
    vdir = processed_dir / "graphs" / version
    sizes = {str(p.relative_to(vdir)).replace("\\", "/"): p.stat().st_size for p in vdir.rglob("*") if p.is_file()}

    section = {
        "generated_at": built_at.isoformat(),
        "version": version,
        "build_seconds": round(time.monotonic() - t0, 2),
        "input_segments": len(segments),
        "counters": net["counters"],
        "snap_tolerance_m": SNAP_TOLERANCE_M,
        "elevation_sampling_for_cut_points": sampled,
        "snap_tolerance_m": SNAP_TOLERANCE_M,
        "elevation_sampling_for_cut_points": sampled,
        **summarize(graph),
        "connected_components": comps,
        "simulated_station": sim_info,
        "buildings_layer": bstats,
        "limitations": limitations,
        "artifact_bytes": sizes,
        "artifact_total_bytes": sum(sizes.values()),
        "elevation_percentiles_m": pct,
        "source_record_ids": source_ids,
        "warning_count": len(warnings),
        "warnings": warnings[:MAX_STORED_WARNINGS],
    }
    report = _read_json(summary_path, {})  # re-read: keep concurrent merges from other steps
    report["graph_build"] = section
    _atomic_write_json(summary_path, report)
    return section


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--processed-dir", type=Path, default=ROOT / "data" / "processed")
    p.add_argument("--cache", type=Path, default=ROOT / "data" / "raw" / "elevation" / "cache.json")
    p.add_argument("--max-sample-seconds", type=float, default=300.0)
    args = p.parse_args(argv)
    s = build(args.processed_dir, cache_path=args.cache, max_sample_seconds=args.max_sample_seconds)
    if s is None:
        print("missing data/processed/streets/segments.jsonl.gz; run scripts/process_streets.py first", file=sys.stderr)
        return 1
    for k in ("version", "build_seconds", "nodes", "edges", "nodes_by_kind", "edges_by_kind", "edges_by_travel_mode",
              "edges_unknown_counts", "edges_oneway_unknown_driving", "artifact_total_bytes", "warning_count"):
        print(f"{k}: {s[k]}")
    print("components:", {k: (v["components"], v["largest"], v["nodes"]) for k, v in s["connected_components"].items()})
    for w in s["warnings"][:10]:
        print("  warning:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
