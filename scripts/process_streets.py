"""Step: normalise raw datasets into clean, WGS84 GeoJSON layers under data/processed/.

Outputs
  buildings.geojson      OSM footprints; height/levels ONLY if tagged (else null/"unknown")
  water.geojson          rivers/canals/water polygons (OSM)
  subway_lines.geojson   OSM subway alignments (underground sections are not routable)
  stations.geojson       CTA GTFS parent stations + OSM station nodes (station-level only)
  portal_streets.geojson Chicago Data Portal centerlines (cleaned) used to cross-check the graph

Processing is done in EPSG:26916 (UTM 16N) for metric operations; outputs are EPSG:4326.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter

from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import transform
from shapely.validation import make_valid

from common import BBOX, PROCESSED, RAW, ensure_dirs, now_iso, read_json, record_manifest, write_json

TO_UTM = Transformer.from_crs(4326, 26916, always_xy=True).transform
TO_WGS = Transformer.from_crs(26916, 4326, always_xy=True).transform

WARNINGS: list[str] = []
COUNTS: dict[str, int] = {}


def in_bbox(lon: float, lat: float) -> bool:
    return BBOX["west"] <= lon <= BBOX["east"] and BBOX["south"] <= lat <= BBOX["north"]


def parse_height_m(raw: str | None):
    if not raw:
        return None
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(m|ft|')?", str(raw))
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) in ("ft", "'"):
        v *= 0.3048
    return round(v, 1)


def parse_int(raw):
    try:
        return int(float(str(raw).split(";")[0]))
    except Exception:
        return None


def classify_building(height_m, levels):
    """Documented heuristic classification. Never invents values: unknown stays unknown."""
    if height_m is not None:
        basis = "osm_height_tag"
        c = "high_rise" if height_m >= 35 else "mid_rise" if height_m >= 12 else "low_rise"
        return c, basis
    if levels is not None:
        basis = "osm_building_levels_tag"
        c = "high_rise" if levels >= 10 else "mid_rise" if levels >= 4 else "low_rise"
        return c, basis
    return "unknown", "no_height_or_levels_in_source"


def process_buildings():
    src = read_json(RAW / "osm_buildings.json")
    if not src:
        WARNINGS.append("osm_buildings.json missing - buildings layer skipped")
        return
    feats, invalid, dropped = [], 0, 0
    for e in src["elements"]:
        geom = e.get("geometry")
        if e["type"] != "way" or not geom or len(geom) < 4:
            dropped += 1
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                invalid += 1
                poly = make_valid(poly)
                if poly.geom_type != "Polygon":
                    parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
                    if not parts:
                        dropped += 1
                        continue
                    poly = max(parts, key=lambda g: g.area)
            poly = poly.simplify(0.000004, preserve_topology=True)
        except Exception:
            dropped += 1
            continue
        t = e.get("tags", {})
        h = parse_height_m(t.get("height"))
        lv = parse_int(t.get("building:levels"))
        cls, basis = classify_building(h, lv)
        feats.append(
            {
                "type": "Feature",
                "geometry": json.loads(json.dumps(mapping(poly), default=list)),
                "properties": {
                    "osm_id": e["id"],
                    "name": t.get("name"),
                    "building": t.get("building"),
                    "height_m": h,
                    "levels": lv,
                    "height_class": cls,
                    "height_class_basis": basis,
                    "structural_condition": "unknown",
                },
            }
        )
    # round coordinates to 6 dp to keep file small
    for f in feats:
        f["geometry"]["coordinates"] = _round(f["geometry"]["coordinates"])
    COUNTS["buildings"] = len(feats)
    COUNTS["buildings_with_height_tag"] = sum(1 for f in feats if f["properties"]["height_m"] is not None)
    COUNTS["buildings_with_levels_tag"] = sum(1 for f in feats if f["properties"]["levels"] is not None)
    COUNTS["buildings_class_unknown"] = sum(1 for f in feats if f["properties"]["height_class"] == "unknown")
    COUNTS["buildings_invalid_repaired"] = invalid
    COUNTS["buildings_dropped"] = dropped
    write_json(PROCESSED / "buildings.geojson", {"type": "FeatureCollection", "features": feats})


def _round(c):
    if isinstance(c, (list, tuple)):
        if c and isinstance(c[0], (int, float)):
            return [round(c[0], 6), round(c[1], 6)]
        return [_round(x) for x in c]
    return c


def process_water_transit():
    src = read_json(RAW / "osm_water_transit.json")
    if not src:
        WARNINGS.append("osm_water_transit.json missing - water/subway/stations skipped")
        return
    water, subway, stations = [], [], []
    for e in src["elements"]:
        t = e.get("tags", {})
        if e["type"] == "way" and e.get("geometry"):
            coords = [[round(p["lon"], 6), round(p["lat"], 6)] for p in e["geometry"]]
            if t.get("railway") == "subway":
                if len(coords) >= 2:
                    subway.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": coords},
                            "properties": {
                                "osm_id": e["id"],
                                "name": t.get("name"),
                                "tunnel": t.get("tunnel"),
                                "layer": t.get("layer"),
                                "note": "Rail alignment only. Not a pedestrian route.",
                            },
                        }
                    )
            elif t.get("waterway") or t.get("natural") == "water":
                closed = len(coords) > 3 and coords[0] == coords[-1] and t.get("natural") == "water"
                geom = {"type": "Polygon", "coordinates": [coords]} if closed else {"type": "LineString", "coordinates": coords}
                water.append(
                    {
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {
                            "osm_id": e["id"],
                            "name": t.get("name"),
                            "kind": t.get("waterway") or t.get("natural"),
                        },
                    }
                )
        elif e["type"] == "node":
            if t.get("railway") in ("station", "halt") or t.get("public_transport") == "station" or t.get("station") == "subway":
                if not in_bbox(e["lon"], e["lat"]):
                    continue
                stations.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [round(e["lon"], 6), round(e["lat"], 6)]},
                        "properties": {
                            "id": f"osm-{e['id']}",
                            "name": t.get("name"),
                            "source": "OpenStreetMap",
                            "network": t.get("network"),
                            "wheelchair": t.get("wheelchair", "unknown"),
                            "entrances": "unknown",
                            "note": "Station point only; entrance/exit locations and levels are unknown.",
                        },
                    }
                )
    COUNTS["water_features"] = len(water)
    COUNTS["subway_lines"] = len(subway)
    COUNTS["osm_stations"] = len(stations)
    write_json(PROCESSED / "water.geojson", {"type": "FeatureCollection", "features": water})
    write_json(PROCESSED / "subway_lines.geojson", {"type": "FeatureCollection", "features": subway})
    return stations


def process_gtfs_stations():
    zpath = RAW / "cta_gtfs.zip"
    if not zpath.exists():
        WARNINGS.append("cta_gtfs.zip missing - CTA GTFS stations skipped")
        return []
    out = []
    with zipfile.ZipFile(zpath) as z:
        rows = csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), encoding="utf-8-sig"))
        for r in rows:
            try:
                lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
            except Exception:
                continue
            if r.get("location_type") != "1" or not in_bbox(lon, lat):
                continue
            wb = {"0": "unknown", "1": "accessible_per_gtfs", "2": "not_accessible_per_gtfs", "": "unknown"}.get(
                r.get("wheelchair_boarding", ""), "unknown"
            )
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": {
                        "id": f"cta-{r['stop_id']}",
                        "name": r["stop_name"],
                        "source": "CTA GTFS (stops.txt, parent station)",
                        "wheelchair": wb,
                        "entrances": "unknown",
                        "note": "Station-level GTFS location. Entrance safety, hours, and level are NOT inferred.",
                    },
                }
            )
    COUNTS["cta_gtfs_parent_stations"] = len(out)
    return out


def process_portal():
    src = read_json(RAW / "chicago_street_centerlines.geojson")
    if not src:
        WARNINGS.append("chicago_street_centerlines.geojson missing - portal cross-check skipped")
        return
    feats, bad = [], 0
    for f in src["features"]:
        try:
            g = shape(f["geometry"])
            if g.is_empty:
                bad += 1
                continue
            if not g.is_valid:
                g = make_valid(g)
        except Exception:
            bad += 1
            continue
        p = f["properties"]
        name = " ".join(x for x in [p.get("pre_dir"), p.get("street_nam"), p.get("street_typ"), p.get("suf_dir")] if x)
        feats.append(
            {
                "type": "Feature",
                "geometry": json.loads(json.dumps(mapping(g))),
                "properties": {
                    "objectid": p.get("objectid"),
                    "trans_id": p.get("trans_id"),
                    "name": name or None,
                    "class": p.get("class"),
                    "dir_travel": p.get("dir_travel"),  # B both, F from->to only, T to->from only
                    "oneway_dir": p.get("oneway_dir"),
                    "tiered": p.get("tiered"),
                    "status": p.get("status"),
                    "update_tim": p.get("update_tim"),
                },
            }
        )
    COUNTS["portal_centerline_features"] = len(feats)
    COUNTS["portal_invalid_dropped"] = bad
    COUNTS["portal_status_not_open"] = sum(1 for f in feats if f["properties"]["status"] in ("P", "UC", "V", "C"))
    write_json(PROCESSED / "portal_streets.geojson", {"type": "FeatureCollection", "features": feats})


def main() -> int:
    ensure_dirs()
    process_buildings()
    osm_st = process_water_transit() or []
    gtfs_st = process_gtfs_stations()
    write_json(PROCESSED / "stations.geojson", {"type": "FeatureCollection", "features": gtfs_st + osm_st})
    COUNTS["stations_total"] = len(gtfs_st) + len(osm_st)
    process_portal()
    report = {"generated_at": now_iso(), "counts": COUNTS, "warnings": WARNINGS}
    write_json(PROCESSED / "process_streets_report.json", report, indent=2)
    record_manifest("processed:layers", generated_at=now_iso(), counts=COUNTS)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
