"""Flood module (Req 17).

Hard restrictions: modeled impassable flood polygons / confirmed closures (overlays) and edges or
geometry of officially reported flooded segments (Official_Notices). Soft penalties: tunnel /
underpass and lower-level infrastructure, lower-level edges within 50 m of water, and an
elevation term that never increases with mean elevation. Wording is "lower modeled exposure";
the module never claims a route is flood-safe. Simulated polygons carry no water-depth values.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
import shapely

from app.disasters.base import (
    Advisory,
    DisasterModule,
    IncidentContext,
    PenaltyParts,
    PolicyOverride,
    register_module,
)
from app.models.domain import IncidentType, Restriction, UncertaintyLevel
from app.models.graph import UNKNOWN, Edge
from app.routing.cost import Weights, load_weights
from app.routing.graph import _COS_REF, _M_PER_DEG, Graph

SHORE_DISTANCE_M = 50.0
DEFAULT_GUIDANCE_PATH = Path(__file__).resolve().parents[3] / "data" / "sources" / "official_guidance.json"

NOT_FLOOD_SAFE_NOTE = (
    "Routes favored by elevation have lower modeled exposure to flooding. This is not a "
    "guarantee of safety: conditions can change, so follow official instructions."
)
NO_WATER_DATA_NOTE = (
    "Water geometry (Chicago River / Lake Michigan shoreline) is unavailable, so the "
    "shoreline-proximity penalty was skipped and exposure near water is uncertain."
)
NO_EVIDENCE_NOTE = (
    "No flood polygon, official notice or confirmed closure is on record for this incident; "
    "only infrastructure and elevation penalties apply and uncertainty is High."
)
_FLOOD_WORDS = ("flood",)

# A flood-related Edge flag (hazard_flags / road_type) that marks an underpass.
_UNDERPASS = "underpass"


def _project(geojson: Mapping[str, Any]) -> BaseGeometry | None:
    try:
        geom = shape(dict(geojson))
        if geom.is_empty:
            return None
        scale = (_COS_REF * _M_PER_DEG, _M_PER_DEG)
        return shapely.transform(geom, lambda c: c[:, :2] * scale, include_z=False)
    except Exception:
        return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class FloodModule:
    incident_type = IncidentType.FLOOD

    def __init__(
        self,
        weights: Weights | None = None,
        water_geometries: Sequence[Mapping[str, Any]] | None = None,
        guidance_path: str | Path | None = None,
    ) -> None:
        self._weights = weights
        self._water_geojson = list(water_geometries) if water_geometries is not None else None
        self._guidance_path = Path(guidance_path) if guidance_path else DEFAULT_GUIDANCE_PATH
        self._water_proj: BaseGeometry | None | bool = False  # False = not computed
        self._near_water: dict[tuple[int, str | None], frozenset[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    @property
    def weights(self) -> Weights:
        if self._weights is None:
            self._weights = load_weights()
        return self._weights

    def _water(self, graph: Graph | None = None) -> BaseGeometry | None:
        """Projected water geometry, or None when no water data is available."""
        if self._water_geojson is not None:  # injected: compute once
            if self._water_proj is False:
                self._water_proj = self._union(self._water_geojson)
            return self._water_proj or None  # type: ignore[return-value]
        layer = graph.layers.get("water") if graph is not None and graph.layers else None
        return self._union(_features(layer))

    @staticmethod
    def _union(raw: Sequence[Mapping[str, Any]]) -> BaseGeometry | None:
        geoms = [g for g in (_project(x) for x in raw) if g is not None]
        return unary_union(geoms) if geoms else None

    def _has_water(self, graph: Graph | None) -> bool:
        return self._water(graph) is not None

    def _near_water_edges(self, graph: Graph) -> frozenset[str]:
        key = (id(graph), graph.version)
        with self._lock:
            hit = self._near_water.get(key)
        if hit is not None:
            return hit
        water = self._water(graph)
        ids: set[str] = set()
        if water is not None and graph.num_edges:
            graph._ensure_tree()
            hits = graph._tree.query(water, predicate="dwithin", distance=SHORT_DIST)  # type: ignore[union-attr]
            ids = {graph.edge_ids[int(i)] for i in hits}
        out = frozenset(ids)
        with self._lock:
            self._near_water[key] = out
        return out

    def _scale(self, edge: Edge) -> float:
        return max(float(edge.distance_meters), self.weights.d_min_meters) / 100.0

    @staticmethod
    def _has_evidence(ctx: IncidentContext) -> bool:
        iid = ctx.incident.incident_id
        return bool(
            ctx.overlays
            or ctx.reports
            or any(n.incident_id == iid for n in ctx.notices)
        )

    @staticmethod
    def _is_flood_notice(n) -> bool:
        return any(w in n.text.lower() for w in _FLOOD_WORDS)

    # ----------------------------------------------------------------- protocol
    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None:
        return None

    def hard_restrictions(self, ctx: IncidentContext, graph: Graph) -> Iterable[Restriction]:
        out: list[Restriction] = []
        iid = ctx.incident.incident_id
        for r in ctx.overlays:
            if r.active and r.incident_id == iid and r.kind in ("impassable_polygon", "closure"):
                out.append(r)
        for n in ctx.notices:
            if n.incident_id != iid or n.cbd_derived or not self._is_flood_notice(n):
                continue
            if n.notice_type not in ("road_closure", "temporary_perimeter"):
                continue
            if not (n.edge_ids or n.geometry):
                continue
            out.append(
                Restriction(
                    restriction_id=n.notice_id,
                    incident_id=iid,
                    kind="closure" if n.notice_type == "road_closure" else "prohibited_area",
                    edge_ids=list(n.edge_ids),
                    geometry=n.geometry,
                    source_record_id=n.source_record_id,
                    simulated=n.simulation_flag,
                )
            )
        return out

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> PenaltyParts:
        f = self.weights.flood
        s = self._scale(edge)
        hazard: dict[str, float] = {}
        unc: dict[str, float] = {}

        flags = {str(x).lower() for x in edge.hazard_flags}
        road = edge.road_type.lower() if isinstance(edge.road_type, str) else ""
        lower = edge.infrastructure_level == "lower-level"
        if (
            edge.tunnel_flag is True
            or edge.infrastructure_level == "tunnel"
            or _UNDERPASS in flags
            or _UNDERPASS in road
        ):
            hazard["flood_tunnel"] = f["w_tunnel"] * s
        if lower:
            hazard["flood_lower_level"] = f["w_lower"] * s
            if edge.edge_id in self._near_water_edges(graph):
                hazard["flood_shoreline"] = f["w_shore"] * s

        es, ee = edge.elevation_start, edge.elevation_end
        known = _is_number(es) and _is_number(ee)
        if known:
            mean = (float(es) + float(ee)) / 2.0
            span = f["E_high"] - f["E_low"]
            t = (f["E_high"] - mean) / span if span > 0 else (1.0 if mean < f["E_high"] else 0.0)
            hazard["flood_elevation"] = f["w_elev"] * min(1.0, max(0.0, t)) * s
        if not known:
            unc["flood_elevation_unknown"] = f["w_elev"] * s
        elif edge.elevation_confidence in ("Low", UNKNOWN):
            unc["flood_elevation_confidence"] = f["w_elev"] * s * 0.5
        return PenaltyParts(hazard=hazard, uncertainty=unc)

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool:
        return True

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None:
        return None if self._has_evidence(ctx) else UncertaintyLevel.HIGH

    def advisories(self, ctx: IncidentContext, graph: Graph | None = None) -> list[Advisory]:
        out: list[Advisory] = []
        iid = ctx.incident.incident_id
        for n in ctx.notices:
            if n.incident_id == iid and self._is_flood_notice(n) and n.source_record_id:
                out.append(
                    Advisory(
                        f"{n.text} (Source: {n.source_organization})",
                        n.source_record_id,
                        simulated=n.simulation_flag,
                    )
                )
        for r in ctx.overlays:
            if r.kind == "impassable_polygon" and r.simulated:
                out.append(Advisory(
                    "Simulated flood polygon (demo data). No water-depth values are provided.",
                    r.source_record_id, simulated=True,
                ))
        g = self._guidance()
        if g is not None:
            out.append(g)
        out.append(Advisory(NOT_FLOOD_SAFE_NOTE, None))
        if not self._has_evidence(ctx):
            out.append(Advisory(NO_EVIDENCE_NOTE, None))
        if not self._has_water(graph):
            out.append(Advisory(NO_WATER_DATA_NOTE, None))
        return out

    def _guidance(self) -> Advisory | None:
        try:
            data = json.loads(self._guidance_path.read_text(encoding="utf-8"))
            item = next(g for g in data.get("guidance", []) if g.get("hazard") == "flood")
        except (OSError, ValueError, StopIteration, AttributeError):
            return None
        notes = " ".join(item.get("notes", []))
        text = (
            f"{item.get('title', 'Flood guidance')}: {notes} "
            f"(Source: {item.get('source_url', 'unknown')}, retrieved {item.get('retrieved_at', 'unknown')})"
        )
        return Advisory(text, item.get("source_id") or item.get("guidance_id"))


SHORT_DIST = SHORE_DISTANCE_M


def _features(layer: Any) -> list[Mapping[str, Any]]:
    """Geometries from a GeoJSON FeatureCollection / Feature / geometry dict (or None)."""
    if not isinstance(layer, Mapping):
        return []
    t = layer.get("type")
    if t == "FeatureCollection":
        return [f["geometry"] for f in layer.get("features", []) if f.get("geometry")]
    if t == "Feature":
        return [layer["geometry"]] if layer.get("geometry") else []
    return [layer] if t else []


FLOOD_MODULE: DisasterModule = FloodModule()
try:
    register_module(FLOOD_MODULE)
except ValueError:  # already registered (module reload)
    pass

