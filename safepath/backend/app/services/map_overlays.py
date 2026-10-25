"""Read-only map overlay snapshot for one incident (map-legend-layers-fix, design D1-D6).

``build_map_overlays`` assembles everything the map needs to draw the legend's hazard and
restriction entries for the selected incident from rows that already exist in every repository:

* ``overlays``      active restrictions (edge lines and area polygons)
* ``overlay_meta``  ``overlay_version`` (read first, so rows are never older than the version)
* ``notices``       SIMULATED ``temporary_perimeter`` notices (fire perimeter)
* ``demo_scenarios`` the scenario row (pk ``scenarios``, sk ``incident.scenario_id``), read ONLY
  for SIMULATED incidents: flood highlight, debris zones, fallback fire perimeter

The function never writes and is not used by ``RoutingService``; it only shapes display data.
Every emitted feature goes through a per-type property allow-list: no user identifiers, no
``source_report_id``, no depth-like keys, and SIMULATED items are never marked official.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.adapters.ports import Repository
from app.api.routers.incidents import _check_geometry
from app.models.domain import Incident
from app.routing.graph import Graph, edge_line_geojson_at
from app.services import demo_data
from app.services.overlays import NOTICES_TABLE, OVERLAYS_TABLE, read_overlay_meta, strip_keys

DISCLAIMER = ("SIMULATED data for demonstration. Not official. Follow official instructions and "
              "call 911 in an emergency.")
LABEL_EDGE_SIMULATED = "SIMULATED closed road segment (not a real closure)"
LABEL_FLOOD = "SIMULATED flood area (modeled low-lying streets, no depth values)"
LABEL_SCENARIO_DEBRIS = "SIMULATED scenario area, not applied to routing"
LABEL_FIRE_NOTICE = "SIMULATED fire perimeter (not an official instruction)"
LABEL_FIRE_SCENARIO = "SIMULATED fire perimeter (scenario area, not an official instruction)"
LABEL_AREA_SIMULATED = {"debris": "SIMULATED debris zone", "fire": "SIMULATED fire area",
                        "flood": "SIMULATED flood area"}
LABEL_AREA_OFFICIAL = "Restricted area"

#: D2b: inactive scenario closures (``resolved.closure``) are NOT drawn until they are active.
INCLUDE_PENDING_SCENARIO_CLOSURES = False

MAX_EDGE_FEATURES = 2000
COORD_DECIMALS = 6
HAZARD_BY_INCIDENT_TYPE = {"earthquake": "debris", "fire": "fire", "flood": "flood"}
AREA_TYPES = ("Polygon", "MultiPolygon")
QUERY_LIMIT = 10_000

_DEPTH = re.compile("depth", re.IGNORECASE)


# ------------------------------------------------------------------------------ sanitizing
def _plain(value: Any) -> Any:
    """Decimal -> float (DynamoDB decode), recursively; other values unchanged."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _round_coords(node: Any) -> Any:
    if isinstance(node, list):
        return [_round_coords(v) for v in node]
    if isinstance(node, float):
        return round(node, COORD_DECIMALS)
    return node


def _clean_geometry(geom: Any, allowed: tuple[str, ...]) -> dict[str, Any] | None:
    """Validated, rounded GeoJSON geometry of an allowed type, or None (skip the feature)."""
    if not isinstance(geom, Mapping) or geom.get("type") not in allowed:
        return None
    candidate = {"type": geom.get("type"), "coordinates": _plain(geom.get("coordinates"))}
    try:
        checked = _check_geometry(candidate)
    except (ValueError, TypeError):
        return None
    if checked is None:
        return None
    return {"type": checked["type"], "coordinates": _round_coords(checked["coordinates"])}


def _clean_props(props: Mapping[str, Any]) -> dict[str, Any]:
    """Drop depth-like keys and None values, convert Decimals."""
    return {k: _plain(v) for k, v in props.items() if not _DEPTH.search(k) and v is not None}


def _feature(geometry: dict[str, Any], props: Mapping[str, Any]) -> dict[str, Any]:
    out = _clean_props(props)
    if out.get("simulated") is True:
        out["classification"] = "Simulated"
        out.pop("is_official", None)
    return {"type": "Feature", "geometry": geometry, "properties": out}


def _fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _pick(src: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {k: src[k] for k in keys if k in src}


# --------------------------------------------------------------------------------- inputs
def _incident_fields(incident: Incident | Mapping[str, Any]) -> tuple[str, str, bool, str | None]:
    if isinstance(incident, Incident):
        itype = incident.incident_type
        return (incident.incident_id, getattr(itype, "value", str(itype)),
                incident.simulation_flag is True, incident.scenario_id)
    itype = incident.get("incident_type")
    return (str(incident["incident_id"]), str(getattr(itype, "value", itype)),
            incident.get("simulation_flag") is True, incident.get("scenario_id"))


def _graph_loaded(graph: Graph | None) -> bool:
    return (graph is not None and getattr(graph, "model", None) is not None
            and bool(getattr(graph, "loaded", True)) and graph.num_edges > 0)


def _active_overlays(repo: Repository, incident_id: str) -> list[dict[str, Any]]:
    rows = [strip_keys(r) for r in repo.query(OVERLAYS_TABLE, {"pk": incident_id}, limit=QUERY_LIMIT)]
    active = [r for r in rows if r.get("status") == "active" and r.get("active") is not False]
    return sorted(active, key=lambda r: str(r.get("restriction_id", "")))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _not_expired(row: Mapping[str, Any], now: datetime) -> bool:
    exp = _parse_time(row.get("expires_at"))
    if exp is None:
        return True
    try:
        return exp > now
    except TypeError:  # naive vs aware: keep the notice rather than guess
        return True


def _simulated_perimeters(repo: Repository, incident_id: str, now: datetime) -> list[dict[str, Any]]:
    rows = [strip_keys(r) for r in repo.query(NOTICES_TABLE, {"pk": incident_id}, limit=QUERY_LIMIT)]
    out = [r for r in rows
           if r.get("notice_type") == "temporary_perimeter" and r.get("simulation_flag") is True
           and isinstance(r.get("geometry"), Mapping) and r["geometry"].get("type") in AREA_TYPES
           and _not_expired(r, now)]
    return sorted(out, key=lambda r: str(r.get("notice_id", "")))


def _scenario_resolved(repo: Repository, incident_id: str, simulated: bool,
                       scenario_id: str | None) -> dict[str, Any]:
    if not (simulated and scenario_id):
        return {}
    row = demo_data.get_scenario(repo, scenario_id)
    if not row or row.get("incident_id") not in (None, incident_id):
        return {}  # never show another incident's scenario data
    resolved = row.get("resolved")
    return resolved if isinstance(resolved, dict) else {}


def _features_of(obj: Any) -> list[Mapping[str, Any]]:
    """Features of a FeatureCollection, a single Feature, or a bare geometry."""
    if not isinstance(obj, Mapping):
        return []
    if obj.get("type") == "FeatureCollection":
        return [f for f in obj.get("features") or [] if isinstance(f, Mapping)]
    if obj.get("type") == "Feature":
        return [obj]
    if obj.get("type") in AREA_TYPES:
        return [{"type": "Feature", "geometry": obj, "properties": {}}]
    return []


# ---------------------------------------------------------------------------------- build
def build_map_overlays(repo: Repository, graph: Graph | None, incident: Incident | Mapping[str, Any],
                       now: datetime) -> dict[str, Any]:
    """Public, read-only MapOverlays payload for ``incident`` (design "Fix Implementation")."""
    iid, itype, simulated_incident, scenario_id = _incident_fields(incident)
    overlay_version, _ = read_overlay_meta(repo, iid)  # version first: rows never older
    overlays = _active_overlays(repo, iid)
    perimeters = _simulated_perimeters(repo, iid, now) if itype == "fire" else []
    resolved = _scenario_resolved(repo, iid, simulated_incident, scenario_id)

    loaded = _graph_loaded(graph)
    index: dict[str, int] = {eid: i for i, eid in enumerate(graph.edge_ids)} if loaded else {}
    hazards: dict[str, list[dict[str, Any]]] = {"flood": [], "debris": [], "fire": []}
    area_kind = HAZARD_BY_INCIDENT_TYPE.get(itype, "debris")

    # ---- active restrictions: edge lines and area polygons (applied to routing)
    lines: list[dict[str, Any]] = []
    missing = 0
    active_ids: set[str] = set()
    for ov in overlays:
        rid = str(ov.get("restriction_id", ""))
        active_ids.add(rid)
        sim = ov.get("simulated") is True
        kind = str(ov.get("kind", "closure"))
        if loaded:
            for eid in ov.get("edge_ids") or []:
                idx = index.get(eid)
                if idx is None:
                    missing += 1
                    continue
                if len(lines) >= MAX_EDGE_FEATURES:
                    continue
                geom = _clean_geometry(edge_line_geojson_at(graph, idx), ("LineString",))
                if geom is None:
                    missing += 1
                    continue
                lines.append(_feature(geom, {
                    "incident_id": iid, "restriction_id": rid, "edge_id": eid, "kind": kind,
                    "simulated": sim, "status": "active", "applied_to_routing": True,
                    "label": LABEL_EDGE_SIMULATED if sim else f"Closed road segment ({kind})",
                }))
        area = _clean_geometry(ov.get("geometry"), AREA_TYPES)
        if area is not None:
            hazards[area_kind].append(_feature(area, {
                "incident_id": iid, "restriction_id": rid, "zone_id": rid, "kind": kind,
                "simulated": sim, "status": "active", "applied_to_routing": True,
                "label": LABEL_AREA_SIMULATED[area_kind] if sim else LABEL_AREA_OFFICIAL,
            }))

    # ---- SIMULATED fire perimeter notices (the routing context reads these notices)
    for n in perimeters:
        geom = _clean_geometry(n.get("geometry"), AREA_TYPES)
        if geom is None:
            continue
        hazards["fire"].append(_feature(geom, {
            "incident_id": iid, "notice_id": n.get("notice_id"), "notice_type": "temporary_perimeter",
            "simulated": True, "applied_to_routing": True, "label": LABEL_FIRE_NOTICE,
        }))

    # ---- scenario display data (SIMULATED incidents only, never applied to routing here)
    if resolved:
        for f in _features_of(resolved.get("flood_highlight")):
            geom = _clean_geometry(f.get("geometry"), AREA_TYPES)
            if geom is None:
                continue
            props = f.get("properties") if isinstance(f.get("properties"), Mapping) else {}
            hazards["flood"].append(_feature(geom, {
                **_pick(props, ("basis", "edge_count", "elevation_percentile")),
                "incident_id": iid, "simulated": True, "applied_to_routing": False,
                "label": LABEL_FLOOD,
            }))
        zones = resolved.get("debris_zones") or []
        for z in zones if isinstance(zones, list) else []:
            if not isinstance(z, Mapping):
                continue
            zid = str(z.get("zone_id", ""))
            if not zid or zid in active_ids:  # D2: the active overlay copy wins
                continue
            geom = _clean_geometry(z.get("geometry"), AREA_TYPES)
            if geom is None:
                continue
            disclaimer = z.get("disclaimer")
            hazards["debris"].append(_feature(geom, {
                "incident_id": iid, "zone_id": zid, "simulated": True, "applied_to_routing": False,
                "label": LABEL_SCENARIO_DEBRIS,
                "disclaimer": disclaimer if isinstance(disclaimer, str) and disclaimer else None,
            }))
        if not perimeters:  # fallback only when no SIMULATED perimeter notice exists
            for f in _features_of(resolved.get("perimeter")):
                geom = _clean_geometry(f.get("geometry"), AREA_TYPES)
                if geom is None:
                    continue
                hazards["fire"].append(_feature(geom, {
                    "incident_id": iid, "simulated": True, "applied_to_routing": False,
                    "label": LABEL_FIRE_SCENARIO,
                }))
        # resolved.closure: intentionally not drawn while inactive (INCLUDE_PENDING_SCENARIO_CLOSURES).

    return {
        "incident_id": iid,
        "simulation_flag": simulated_incident,
        "overlay_version": int(overlay_version),
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "restrictions": _fc(lines),
        "hazards": {k: _fc(v) for k, v in hazards.items()},
        "limitations": {"missing_edge_geometry": missing, "graph_loaded": loaded},
    }
