"""Runtime routing graph: checksum-validated load, array adjacency, lazy STRtree, snapping.

Edge geometry encoding (the model field is an opaque ``str``): a WKT ``LINESTRING`` in
EPSG:4326 with ``lon lat`` order, e.g. ``LINESTRING(-87.65 41.88, -87.64 41.88)``. A JSON
list of ``[lon, lat]`` pairs is also accepted. Anything undecodable (or an empty value)
falls back to the straight segment between the edge's endpoint nodes.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from app.geo import haversine
from app.models.graph import Edge, Graph as GraphModel
from app.services.graph_artifact import (
    ArtifactError,
    Manifest,
    read_artifact,
    read_latest_version,
)

OK = "ok"
FALLBACK = "fallback"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

_M_PER_DEG = 111_320.0
_REF_LAT = 41.88  # projection reference latitude (centre of the Chicago extraction area)
_COS_REF = math.cos(math.radians(_REF_LAT))


def _xy(lon: float, lat: float) -> tuple[float, float]:
    """Local equirectangular projection to meters (adequate at city scale)."""
    return lon * _COS_REF * _M_PER_DEG, lat * _M_PER_DEG


def decode_geometry(encoded: str) -> list[tuple[float, float]] | None:
    """Decode an edge geometry string to ``[(lon, lat), ...]`` or None when undecodable."""
    if not encoded or not isinstance(encoded, str):
        return None
    s = encoded.strip()
    try:
        if s.upper().startswith("LINESTRING"):
            g = wkt.loads(s)
            coords = [(float(x), float(y)) for x, y, *_ in g.coords]
        elif s.startswith("["):
            coords = [(float(c[0]), float(c[1])) for c in json.loads(s)]
        else:
            return None
    except Exception:
        return None
    return coords if len(coords) >= 2 else None


@dataclass(frozen=True)
class SnapResult:
    edge_id: str
    edge_index: int
    node_id: str  # nearest endpoint of the snapped edge whose edge is allowed by the mask
    lat: float  # snapped point on the edge
    lon: float
    distance_m: float


@dataclass
class Graph:
    """Loaded graph plus load status. ``status`` is ok, fallback or INSUFFICIENT_DATA."""

    status: str = INSUFFICIENT_DATA
    version: str | None = None
    manifest: Manifest | None = None
    model: GraphModel | None = None
    layers: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failed_versions: dict[str, str] = field(default_factory=dict)

    # array-based structures (populated on success)
    node_ids: list[str] = field(default_factory=list)
    node_index: dict[str, int] = field(default_factory=dict)
    node_lat: np.ndarray = field(default_factory=lambda: np.empty(0))
    node_lon: np.ndarray = field(default_factory=lambda: np.empty(0))
    edge_ids: list[str] = field(default_factory=list)
    edge_from: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))
    edge_to: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))
    edge_dist: np.ndarray = field(default_factory=lambda: np.empty(0))
    edge_time: np.ndarray = field(default_factory=lambda: np.empty(0))
    # CSR adjacency over outgoing edges: out-edges of node i are adj_edges[adj_ptr[i]:adj_ptr[i+1]]
    adj_ptr: np.ndarray = field(default_factory=lambda: np.zeros(1, dtype=np.int32))
    adj_edges: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))

    _geoms: list[LineString] | None = field(default=None, repr=False)
    _tree: STRtree | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def loaded(self) -> bool:
        return self.model is not None

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def num_edges(self) -> int:
        return len(self.edge_ids)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, source: Path | str, version: str | None = None) -> "Graph":
        """Load from artifact root ``source`` (containing ``graphs/``).

        Tries ``version`` (default: latest.json) with checksum validation; on any failure falls
        back to previous versions (newest first). If none loads, returns a Graph with status
        INSUFFICIENT_DATA.
        """
        root = Path(source)
        failed: dict[str, str] = {}
        warnings: list[str] = []
        candidates: list[str] = []
        try:
            candidates.append(version or read_latest_version(root))
        except (ArtifactError, OSError, ValueError, KeyError) as exc:
            warnings.append(f"latest version unreadable: {exc}")
        for v in _list_versions(root):
            if v not in candidates:
                candidates.append(v)

        for i, v in enumerate(candidates):
            try:
                model, manifest, layers = read_artifact(root, v, verify=True)
            except Exception as exc:  # checksum, missing file, parse error
                failed[v] = f"{type(exc).__name__}: {exc}"
                warnings.append(f"graph version {v} failed to load: {exc}")
                continue
            g = cls._from_model(model, manifest, layers)
            g.failed_versions = failed
            g.warnings = warnings + list(model.warnings)
            if i == 0 and not failed:
                g.status = OK
            else:
                g.status = FALLBACK
                g.warnings.append(f"using previous graph version {v}")
            return g

        g = cls(status=INSUFFICIENT_DATA)
        g.failed_versions = failed
        g.warnings = warnings + ["no graph version could be loaded"]
        return g

    @classmethod
    def _from_model(cls, model: GraphModel, manifest: Manifest, layers: dict[str, dict]) -> "Graph":
        node_ids = [n.node_id for n in model.nodes]
        node_index = {nid: i for i, nid in enumerate(node_ids)}
        n_nodes = len(node_ids)
        edges = model.edges
        frm = np.fromiter((node_index[e.from_node] for e in edges), dtype=np.int32, count=len(edges))
        to = np.fromiter((node_index[e.to_node] for e in edges), dtype=np.int32, count=len(edges))
        # CSR build: stable sort of edge indices by source node
        order = np.argsort(frm, kind="stable").astype(np.int32)
        counts = np.bincount(frm, minlength=n_nodes) if len(edges) else np.zeros(n_nodes, dtype=np.int64)
        ptr = np.zeros(n_nodes + 1, dtype=np.int32)
        np.cumsum(counts, out=ptr[1:])
        return cls(
            status=OK,
            version=manifest.version,
            manifest=manifest,
            model=model,
            layers=layers,
            node_ids=node_ids,
            node_index=node_index,
            node_lat=np.array([n.lat for n in model.nodes], dtype=np.float64),
            node_lon=np.array([n.lon for n in model.nodes], dtype=np.float64),
            edge_ids=[e.edge_id for e in edges],
            edge_from=frm,
            edge_to=to,
            edge_dist=np.array([float(e.distance_meters) for e in edges], dtype=np.float64),
            edge_time=np.array([float(e.estimated_travel_time_seconds) for e in edges], dtype=np.float64),
            adj_ptr=ptr,
            adj_edges=order,
        )

    # --------------------------------------------------------------- adjacency
    def out_edges(self, node_idx: int) -> np.ndarray:
        return self.adj_edges[self.adj_ptr[node_idx] : self.adj_ptr[node_idx + 1]]

    def edge(self, idx: int) -> Edge:
        assert self.model is not None
        return self.model.edges[idx]

    # ------------------------------------------------------------------ snapping
    def _ensure_tree(self) -> None:
        if self._tree is not None:
            return
        with self._lock:
            if self._tree is not None:
                return
            geoms: list[LineString] = []
            for i, e in enumerate(self.model.edges if self.model else []):
                coords = decode_geometry(e.geometry)
                if coords is None:
                    a, b = int(self.edge_from[i]), int(self.edge_to[i])
                    coords = [
                        (float(self.node_lon[a]), float(self.node_lat[a])),
                        (float(self.node_lon[b]), float(self.node_lat[b])),
                    ]
                geoms.append(LineString([_xy(x, y) for x, y in coords]))
            self._geoms = geoms
            self._tree = STRtree(geoms)

    def snap(self, lat: float, lon: float, mask: np.ndarray | None = None) -> SnapResult | None:
        """Snap to the nearest edge allowed by ``mask`` (True = usable; None = all usable).

        Returns None when no allowed edge exists. Distance is in meters (local projection).
        """
        if not self.loaded or self.num_edges == 0:
            return None
        if mask is not None:
            mask = np.asarray(mask, dtype=bool)
            if mask.shape != (self.num_edges,):
                raise ValueError("mask must be a boolean array with one entry per edge")
            if not mask.any():
                return None
        self._ensure_tree()
        assert self._tree is not None and self._geoms is not None
        px, py = _xy(lon, lat)
        pt = Point(px, py)
        if mask is None:
            idx = int(self._tree.nearest(pt))
        else:
            radius = 50.0
            idx = -1
            while idx < 0:
                cand = self._tree.query(pt.buffer(radius))
                cand = cand[mask[cand]] if len(cand) else cand
                if len(cand):
                    d = [self._geoms[int(c)].distance(pt) for c in cand]
                    best = int(cand[int(np.argmin(d))])
                    # a closer allowed edge may lie in (best_dist, radius]; best_dist <= radius
                    # guarantees every closer edge is inside the query window.
                    if min(d) <= radius:
                        idx = best
                        break
                radius *= 2
        geom = self._geoms[idx]
        proj = geom.interpolate(geom.project(pt))
        snapped_lon = proj.x / (_COS_REF * _M_PER_DEG)
        snapped_lat = proj.y / _M_PER_DEG
        a, b = int(self.edge_from[idx]), int(self.edge_to[idx])
        da = haversine(snapped_lat, snapped_lon, float(self.node_lat[a]), float(self.node_lon[a]))
        db = haversine(snapped_lat, snapped_lon, float(self.node_lat[b]), float(self.node_lon[b]))
        nearest = a if da <= db else b
        return SnapResult(
            edge_id=self.edge_ids[idx],
            edge_index=idx,
            node_id=self.node_ids[nearest],
            lat=snapped_lat,
            lon=snapped_lon,
            distance_m=float(geom.distance(pt)),
        )

    # ------------------------------------------------------------------- status
    def status_dict(self) -> dict:
        return {
            "status": self.status,
            "version": self.version,
            "built_at": self.manifest.built_at.isoformat() if self.manifest else None,
            "node_count": self.num_nodes,
            "edge_count": self.num_edges,
            "crs": self.manifest.crs if self.manifest else None,
            "warnings": list(self.warnings),
            "failed_versions": dict(self.failed_versions),
        }


def edge_line_geojson_at(graph: Graph, idx: int) -> dict:
    """GeoJSON LineString (``[lon, lat]`` pairs) for the edge at index ``idx``.

    Uses the decoded edge geometry; falls back to the straight segment between the edge's
    endpoint nodes when the geometry string is undecodable.
    """
    coords = decode_geometry(graph.edge(idx).geometry)
    if coords is None:
        a, b = int(graph.edge_from[idx]), int(graph.edge_to[idx])
        coords = [(float(graph.node_lon[a]), float(graph.node_lat[a])),
                  (float(graph.node_lon[b]), float(graph.node_lat[b]))]
    return {"type": "LineString", "coordinates": [[x, y] for x, y in coords]}


def edge_line_geojson(graph: Graph, edge_id: str) -> dict | None:
    """GeoJSON LineString for ``edge_id``, or None when the edge is not in the loaded graph."""
    if graph.model is None:
        return None
    try:
        idx = graph.edge_ids.index(edge_id)
    except ValueError:
        return None
    return edge_line_geojson_at(graph, idx)


def _list_versions(root: Path) -> list[str]:
    """Version directories (with a manifest), newest first by version id."""
    gdir = root / "graphs"
    if not gdir.is_dir():
        return []
    names = [
        p.name
        for p in gdir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / "manifest.json").exists()
    ]
    return sorted(names, reverse=True)
