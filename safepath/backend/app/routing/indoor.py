"""Fictional multi-floor Indoor_Graph (Req 21, 12.5).

Source of truth: ``data/demo/indoor_building.json`` ("SIMULATED Demo Building"). It is NOT a real
building and NOT a real UIC floor plan. Every node, edge and GeoJSON feature is classified
Simulated and labelled SIMULATED. Attributes the spec does not state stay ``Unknown``.

Floors are separate levels (``L1`` ground, ``L2``, ``L3``). Floors connect only through explicit
Level_Transition edges (three stairwells A/B/C, bidirectional pairs, ``transition_type`` =
``stairwell``). Ground-floor exits link to the nearest *outdoor* node of the graph being merged
into via Level_Transition edges (``transition_type`` = ``exit``), resolved at load time, so the
same building attaches to the SIMULATED fixture graph or to the real one.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.geo import UNKNOWN, haversine
from app.models.graph import Edge, Graph, Node

DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[3] / "data" / "demo" / "indoor_building.json"
LAYER_NAME = "indoor_building"
OUTDOOR_LEVEL = "surface"
BUILT_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_M_PER_DEG = 111_320.0


class IndoorError(ValueError):
    pass


def load_spec(path: Path | str = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def node_id(level: str, key: str) -> str:
    return f"sim-indoor-{level}-{key}"


def _latlon(spec: dict, x: float, y: float) -> tuple[float, float]:
    a = spec["anchor"]
    lat = a["lat"] + y / _M_PER_DEG
    lon = a["lon"] + x / (_M_PER_DEG * math.cos(math.radians(a["lat"])))
    return lat, lon


def _wkt(pts: list[tuple[float, float]]) -> str:
    return "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in pts) + ")"


def _floor_keys(spec: dict) -> dict[str, tuple[float, float, str]]:
    """key -> (x, y, label) for nodes present on every floor."""
    out: dict[str, tuple[float, float, str]] = {}
    for n in spec["floor_nodes"]:
        out[n["key"]] = (n["x"], n["y"], n["label"])
    for z in spec["zones"]:
        out[z["id"]] = (z["x"], z["y"], z["name"])
    for s in spec["stairwells"]:
        out[f"stair-{s['id']}"] = (s["x"], s["y"], s["name"])
    return out


def _intra_links(spec: dict, ground: bool) -> list[tuple[str, str, str]]:
    """(key_a, key_b, road_type) undirected same-floor links."""
    links: list[tuple[str, str, str]] = []
    for c in spec["corridors"]:
        links += [(a, b, "indoor_corridor") for a, b in zip(c["path"], c["path"][1:])]
    links += [(a, b, "indoor_corridor") for a, b in spec["connectors"]]
    links += [(z["id"], z["connect"], "indoor_zone_link") for z in spec["zones"]]
    links += [(f"stair-{s['id']}", s["connect"], "indoor_stair_landing") for s in spec["stairwells"]]
    if ground:
        links += [(e["id"], e["connect"], "indoor_corridor") for e in spec["exits"]]
    return links


class _Builder:
    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []

    def add_node(self, nid: str, lat: float, lon: float, level: str) -> None:
        self.nodes[nid] = Node(
            node_id=nid, lat=lat, lon=lon, kind="indoor", level=level,
            elevation_m=UNKNOWN, elevation_conf=UNKNOWN, building_id=self.spec["building_id"],
        )

    def add_edge(self, eid: str, a: str, b: str, *, dist: float, speed: float, road_type: str,
                 modes: list[str], kind: str, transition: str | None = None) -> None:
        pts = [(self.nodes[a].lat, self.nodes[a].lon), (self.nodes[b].lat, self.nodes[b].lon)]
        self.edges.append(Edge(
            edge_id=eid, from_node=a, to_node=b, geometry=_wkt(pts),
            distance_meters=round(max(dist, 0.5), 2),
            estimated_travel_time_seconds=round(max(dist, 0.5) / speed, 2),
            travel_mode=modes, source=self.spec["source"],
            source_ids={"fixture": f"{self.spec['source']}:{eid}"},
            road_type=road_type, pedestrian_access=True,
            edge_kind=kind, transition_type=transition,  # type: ignore[arg-type]
            data_timestamp=BUILT_AT, classification="Simulated",
        ))


def build_indoor_parts(spec: dict, outdoor_nodes: list[Node]) -> tuple[list[Node], list[Edge]]:
    """Indoor Nodes and Edges. Exits attach to the nearest non-indoor node in ``outdoor_nodes``."""
    outdoor = [n for n in outdoor_nodes if n.kind != "indoor"]
    if not outdoor:
        raise IndoorError("no outdoor node available to attach exits to")
    b = _Builder(spec)
    keys = _floor_keys(spec)
    walk = spec["speeds_mps"]["walking"]
    stairs = spec["speeds_mps"]["stairs"]
    ground_levels = {f["level"] for f in spec["floors"] if f["ground"]}
    for f in spec["floors"]:
        lvl = f["level"]
        ground = lvl in ground_levels
        local = dict(keys)
        if ground:
            for e in spec["exits"]:
                local[e["id"]] = (e["x"], e["y"], e["name"])
        for k, (x, y, _) in local.items():
            lat, lon = _latlon(spec, x, y)
            b.add_node(node_id(lvl, k), lat, lon, lvl)
        for a, c, rt in _intra_links(spec, ground):
            d = math.dist(local[a][:2], local[c][:2])
            for s, t in ((a, c), (c, a)):
                b.add_edge(f"sim-indoor-e-{lvl}-{s}-{t}", node_id(lvl, s), node_id(lvl, t),
                           dist=d, speed=walk, road_type=rt,
                           modes=["walking", "wheelchair"], kind="indoor")
    levels = [f["level"] for f in spec["floors"]]
    for s in spec["stairwells"]:
        for lo, hi in zip(levels, levels[1:]):
            for src, dst in ((lo, hi), (hi, lo)):
                b.add_edge(f"sim-indoor-t-stair-{s['id']}-{src}-{dst}", node_id(src, f"stair-{s['id']}"),
                           node_id(dst, f"stair-{s['id']}"), dist=spec["stair_length_m"],
                           speed=stairs, road_type="indoor_stairwell", modes=["walking"],
                           kind="level_transition", transition="stairwell")
    for e in spec["exits"]:
        en = b.nodes[node_id(e["level"], e["id"])]
        near = min(outdoor, key=lambda n: (haversine(en.lat, en.lon, n.lat, n.lon), n.node_id))
        d = haversine(en.lat, en.lon, near.lat, near.lon)
        pts = [(en.lat, en.lon), (near.lat, near.lon)]
        for s, t, p in ((en.node_id, near.node_id, pts), (near.node_id, en.node_id, pts[::-1])):
            eid = f"sim-indoor-t-{e['id']}-{'out' if s == en.node_id else 'in'}"
            b.edges.append(Edge(
                edge_id=eid, from_node=s, to_node=t, geometry=_wkt(p),
                distance_meters=round(max(d, 0.5), 2),
                estimated_travel_time_seconds=round(max(d, 0.5) / walk, 2),
                travel_mode=["walking", "wheelchair"], source=spec["source"],
                source_ids={"fixture": f"{spec['source']}:{eid}"}, road_type="indoor_exit",
                pedestrian_access=True, edge_kind="level_transition", transition_type="exit",
                data_timestamp=BUILT_AT, classification="Simulated",
            ))
    return list(b.nodes.values()), b.edges


def exit_links(edges: list[Edge], spec: dict) -> dict[str, str]:
    """exit id -> outdoor node id it is attached to."""
    out: dict[str, str] = {}
    for e in spec["exits"]:
        nid = node_id(e["level"], e["id"])
        for ed in edges:
            if ed.transition_type == "exit" and ed.from_node == nid:
                out[e["id"]] = ed.to_node
    return out


def merge_indoor(graph: Graph, spec: dict | None = None) -> Graph:
    """Return a new routable Graph model with the indoor building merged in (idempotent)."""
    spec = spec or load_spec()
    if any(n.building_id == spec["building_id"] for n in graph.nodes):
        return graph
    nodes, edges = build_indoor_parts(spec, graph.nodes)
    return Graph.build(list(graph.nodes) + nodes, list(graph.edges) + edges)


def merge_indoor_runtime(g, spec: dict | None = None):
    """Runtime ``routing.graph.Graph`` -> new runtime Graph with indoor merged and layer added."""
    from app.routing.graph import Graph as RuntimeGraph

    if not g.loaded:
        return g
    spec = spec or load_spec()
    merged = merge_indoor(g.model, spec)
    if merged is g.model:
        return g
    layers = dict(g.layers)
    layers[LAYER_NAME] = indoor_geojson(spec, merged)
    out = RuntimeGraph._from_model(merged, g.manifest, layers)
    out.status, out.warnings, out.failed_versions = g.status, list(g.warnings), dict(g.failed_versions)
    return out


# ------------------------------------------------------------------------------ GeoJSON
def indoor_geojson(spec: dict | None = None, graph: Graph | None = None) -> dict:
    """Frontend layer: floors, corridors, zones, stairwells, exits, start positions (SIMULATED)."""
    spec = spec or load_spec()
    meta = {"simulated": True, "classification": "Simulated", "source": spec["source"],
            "notice": spec["notice"], "building_id": spec["building_id"]}
    keys = _floor_keys(spec)
    feats: list[dict] = []

    def pt(x: float, y: float) -> list[float]:
        lat, lon = _latlon(spec, x, y)
        return [lon, lat]

    def feat(geom: dict, **props: Any) -> None:
        feats.append({"type": "Feature", "geometry": geom, "properties": {**props, **meta}})

    w, d = spec["footprint"]["width_m"], spec["footprint"]["depth_m"]
    ring = [pt(0, 0), pt(w, 0), pt(w, d), pt(0, d), pt(0, 0)]
    links_by_exit = exit_links(graph.edges, spec) if graph else {}
    for f in spec["floors"]:
        lvl = f["level"]
        feat({"type": "Polygon", "coordinates": [ring]}, role="floor", level=lvl,
             floor=f["number"], name=f["name"])
        local = dict(keys)
        if f["ground"]:
            for e in spec["exits"]:
                local[e["id"]] = (e["x"], e["y"], e["name"])
        for c in spec["corridors"]:
            feat({"type": "LineString", "coordinates": [pt(*local[k][:2]) for k in c["path"]]},
                 role="corridor", level=lvl, corridor_id=c["id"], name=c["name"],
                 node_ids=[node_id(lvl, k) for k in c["path"]])
        for z in spec["zones"]:
            feat({"type": "Point", "coordinates": pt(z["x"], z["y"])}, role="zone", level=lvl,
                 zone_id=z["id"], node_id=node_id(lvl, z["id"]), name=z["name"])
        for s in spec["stairwells"]:
            feat({"type": "Point", "coordinates": pt(s["x"], s["y"])}, role="stairwell",
                 level=lvl, stairwell_id=s["id"], node_id=node_id(lvl, f"stair-{s['id']}"),
                 name=s["name"])
        if f["ground"]:
            for e in spec["exits"]:
                props = {"outdoor_node_id": links_by_exit[e["id"]]} if e["id"] in links_by_exit else {}
                feat({"type": "Point", "coordinates": pt(e["x"], e["y"])}, role="exit", level=lvl,
                     exit_id=e["id"], node_id=node_id(lvl, e["id"]), name=e["name"], **props)
    for sp in spec["start_positions"]:
        x, y, _ = keys[sp["node"]]
        feat({"type": "Point", "coordinates": pt(x, y)}, role="start_position", level=sp["level"],
             start_id=sp["id"], node_id=node_id(sp["level"], sp["node"]), name=sp["name"])
    return {"type": "FeatureCollection", "name": spec["name"], "properties": meta,
            "features": feats}
