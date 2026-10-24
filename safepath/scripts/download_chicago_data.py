"""Download the Chicago source datasets (Req 3.1, 3.2, 5.1, 6.1, 7.1, 7.4, 7.5, 7.9).

Datasets (saved under the git-ignored ``data/raw/<dataset>/``):

* ``cdp_centerlines``  Chicago Data Portal Street Center Lines. ``6imu-meau`` is the portal *map view*;
  its tabular data lives in parent dataset ``pr57-gg9e`` (the ``6imu-meau`` SODA resource returns
  rows with null geometry), so the Socrata GeoJSON API of ``pr57-gg9e`` is queried with a bbox.
* ``osm_streets``      OSM vehicle streets (OSMnx ``drive`` network highway classes).
* ``osm_pedestrian``   OSM walkways, pedestrian streets, paths, steps and corridors.
* ``osm_buildings``    OSM building footprints.
* ``cta_gtfs``         CTA GTFS static feed; CTA stations (id, name, location, routes) inside the
  extraction area are extracted to ``data/processed/cta_gtfs/data.json``.

OSM data is fetched with a direct Overpass API query using the same highway filters that OSMnx
uses, instead of calling OSMnx itself. This keeps the download inside the shared pipeline framework
(3 attempts x 60 s, previous-copy fallback, missing recording, Source_Records, summary report) and
needs no native geo stack. OSMnx/GeoPandas live in the optional ``pipeline`` extra for the later
processing scripts.

Every fallback (reused previous copy / missing) and each standing data limitation is written to
``data/processed/fallbacks.json`` (input for ``docs/safety-limitations.md``).

Run (from the repository's ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\download_chicago_data.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from app.geo import BBOX, EAST, NORTH, SOUTH, WEST, clip_linestring, in_bbox  # noqa: E402
from app.models.provenance import DataClassification  # noqa: E402
from pipeline import (  # noqa: E402
    ACQ_FRESH,
    ACQ_MISSING,
    ACQ_REUSED,
    DatasetSpec,
    PipelineRunner,
    osm_usage_restrictions,
    urllib_fetcher,
)
from pipeline.runner import StageContext  # noqa: E402

FALLBACK_PREFIX = "download:"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CDP_URL = "https://data.cityofchicago.org/resource/pr57-gg9e.geojson"
CTA_GTFS_URL = "https://www.transitchicago.com/downloads/sch_data/google_transit.zip"

# Overpass bbox order is (south, west, north, east).
_OP_BBOX = f"{SOUTH},{WEST},{NORTH},{EAST}"

_DRIVE = (
    "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|"
    "tertiary|tertiary_link|unclassified|residential|living_street|service"
)
_PEDESTRIAN = "footway|pedestrian|path|steps|corridor"


def overpass_url(body: str) -> str:
    query = f"[out:json][timeout:180];({body});out geom;"
    return f"{OVERPASS_URL}?{urllib.parse.urlencode({'data': query})}"


def cdp_url() -> str:
    where = f"within_box(the_geom,{NORTH},{WEST},{SOUTH},{EAST})"
    return CDP_URL + "?" + urllib.parse.urlencode({"$limit": 50000, "$where": where}, safe="(),$")


# -- stage handlers -----------------------------------------------------------------------------
def _json(data: Any) -> Any:
    return json.loads(data) if isinstance(data, (bytes, bytearray)) else data


def count_features(data: Any) -> int:
    """Feature/element count that also understands not-yet-parsed JSON bytes."""
    if isinstance(data, (bytes, bytearray)):
        if data[:2] == b"PK":  # zip archive (GTFS) is not countable before normalize
            return 0
        data = json.loads(data)
    if isinstance(data, dict):
        for key in ("features", "elements"):
            if isinstance(data.get(key), list):
                return len(data[key])
    return len(data) if isinstance(data, list) else 0


def clip_cdp(data: Any, _ctx: StageContext) -> dict[str, Any]:
    """Clip centerline geometries to the Chicago_Extraction_Area; drop null/empty geometries."""
    fc = _json(data)
    out = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        lines = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        parts = [p for line in lines for p in clip_linestring(line) if len(p) >= 2]
        if parts:
            out.append(
                {
                    "type": "Feature",
                    "properties": feat.get("properties", {}),
                    "geometry": {"type": "MultiLineString", "coordinates": [[list(c) for c in p] for p in parts]},
                }
            )
    return {"type": "FeatureCollection", "features": out}


def clip_overpass(data: Any, _ctx: StageContext) -> dict[str, Any]:
    """Keep ways with geometry that touches the extraction area (full clipping is process_streets)."""
    doc = _json(data)
    kept = [
        e
        for e in doc.get("elements", [])
        if e.get("geometry") and clip_linestring([(p["lon"], p["lat"]) for p in e["geometry"]])
    ]
    return {"osm3s": doc.get("osm3s"), "elements": kept}


def _read_csv(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))


def extract_stations(data: Any, _ctx: StageContext) -> dict[str, Any]:
    """CTA stations (GTFS parent stations) in the area with their routes."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    stations: dict[str, dict[str, Any]] = {}
    child_to_station: dict[str, str] = {}
    for row in _read_csv(zf, "stops.txt"):
        if row.get("location_type") == "1":
            try:
                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
            except (KeyError, ValueError):
                continue
            if in_bbox(lon, lat):
                stations[row["stop_id"]] = {
                    "id": row["stop_id"],
                    "name": row.get("stop_name") or "Unknown",
                    "lat": lat,
                    "lon": lon,
                    "wheelchair_boarding": {"1": "accessible", "2": "inaccessible"}.get(
                        row.get("wheelchair_boarding", ""), "Unknown"
                    ),
                    "route_ids": set(),
                }
    for row in _read_csv(zf, "stops.txt"):
        if row.get("parent_station") in stations:
            child_to_station[row["stop_id"]] = row["parent_station"]
    trip_route = {r["trip_id"]: r["route_id"] for r in _read_csv(zf, "trips.txt")}
    seen: set[tuple[str, str]] = set()
    for row in _read_csv(zf, "stop_times.txt"):
        station = child_to_station.get(row["stop_id"])
        if station is None:
            continue
        key = (station, row["trip_id"])
        if key in seen:
            continue
        seen.add(key)
        route = trip_route.get(row["trip_id"])
        if route:
            stations[station]["route_ids"].add(route)
    route_names = {
        r["route_id"]: r.get("route_long_name") or r.get("route_short_name") or r["route_id"]
        for r in _read_csv(zf, "routes.txt")
    }
    features = []
    for st in sorted(stations.values(), key=lambda s: s["id"]):
        routes = sorted(st.pop("route_ids"))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                "properties": {
                    "station_id": st["id"],
                    "name": st["name"],
                    "routes": [{"route_id": r, "name": route_names.get(r, r)} for r in routes],
                    "wheelchair_boarding": st["wheelchair_boarding"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features, "bbox": list(BBOX)}


def build_datasets() -> list[DatasetSpec]:
    osm_common = dict(
        organization="OpenStreetMap contributors",
        data_classification=DataClassification.STATIC,
        is_official_source=False,
        usage_restrictions=osm_usage_restrictions(),
        handlers={"clip": clip_overpass},
        count_features=count_features,
        file_extension="json",
    )
    return [
        DatasetSpec(
            name="cdp_centerlines",
            url=cdp_url(),
            organization="City of Chicago Data Portal",
            title="Street Center Lines (6imu-meau, data from pr57-gg9e)",
            is_street_source=True,
            is_official_source=True,
            handlers={"clip": clip_cdp},
            count_features=count_features,
            file_extension="geojson",
        ),
        DatasetSpec(
            name="osm_streets",
            url=overpass_url(f'way["highway"~"^({_DRIVE})$"]({_OP_BBOX});'),
            title="OpenStreetMap streets (Overpass, OSMnx drive highway classes)",
            is_street_source=True,
            **osm_common,
        ),
        DatasetSpec(
            name="osm_pedestrian",
            url=overpass_url(f'way["highway"~"^({_PEDESTRIAN})$"]({_OP_BBOX});'),
            title="OpenStreetMap walkways and pedestrian paths (Overpass)",
            is_street_source=True,
            **osm_common,
        ),
        DatasetSpec(
            name="osm_buildings",
            url=overpass_url(f'way["building"]({_OP_BBOX});'),
            title="OpenStreetMap building footprints (Overpass)",
            **osm_common,
        ),
        DatasetSpec(
            name="cta_gtfs",
            url=CTA_GTFS_URL,
            organization="Chicago Transit Authority",
            title="CTA GTFS static feed",
            is_official_source=True,
            handlers={"normalize": extract_stations},
            count_features=count_features,
            file_extension="zip",
        ),
    ]


# -- fallbacks.json -----------------------------------------------------------------------------
STANDING_LIMITATIONS = {
    "cdp_centerlines": "Chicago Data Portal centerlines (pr57-gg9e) are a periodic snapshot (map view 6imu-meau dated August 2016) and carry no bridge, tunnel or pedestrian-access attributes; those come from OSM or stay Unknown.",
    "osm_streets": "OSM street attributes (bridge, tunnel, layer, access, oneway) are volunteer-contributed and incomplete; missing values are kept Unknown and penalized, never assumed safe.",
    "osm_pedestrian": "OSM pedestrian coverage (walkways, steps, indoor corridors, skywalk or underpass tagging) is incomplete; Level_Transition and accessibility data may be Unknown.",
    "osm_buildings": "OSM building height and floor tags are sparse; building height class is Unknown where tags are absent. Multipolygon relations are not downloaded; only building ways.",
    "cta_gtfs": "CTA GTFS is a schedule feed; stations are GTFS parent stations. Wheelchair boarding is Unknown where the feed does not state it. It says nothing about service disruptions.",
}


def update_fallbacks(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Replace this script's entries in fallbacks.json, preserving entries from other scripts."""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    merged = {k: v for k, v in existing.items() if not k.startswith(FALLBACK_PREFIX)}
    for entry in report["datasets"]:
        name, status = entry["dataset"], entry["acquisition_status"]
        item: dict[str, Any] = {
            "dataset": name,
            "source_url": entry["source_url"],
            "acquisition_status": status,
            "retrieved_at": entry["retrieved_at"],
            "feature_count": entry["feature_count"],
            "limitation": STANDING_LIMITATIONS[name],
            "warnings": entry["warnings"],
            "errors": entry["errors"],
            "reproduce": "backend\\.venv\\Scripts\\python.exe scripts\\download_chicago_data.py",
            "requirements": ["7.4", "7.5", "7.9"],
        }
        if status == ACQ_REUSED:
            item["fallback"] = "Previous raw copy reused; data may be older than the current source."
        elif status == ACQ_MISSING:
            item["fallback"] = "Source missing: no fresh download and no previous copy. Dependent layers are marked Unknown/unavailable."
        elif status == ACQ_FRESH and entry["errors"]:
            item["fallback"] = "Downloaded but processing failed; dataset excluded from processed data."
        merged[f"{FALLBACK_PREFIX}{name}"] = item
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return merged


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    p.add_argument("--processed-dir", type=Path, default=ROOT / "data" / "processed")
    p.add_argument("--only", nargs="*", help="dataset names to run (default: all)")
    args = p.parse_args(argv)

    datasets = build_datasets()
    if args.only:
        datasets = [d for d in datasets if d.name in args.only]
    runner = PipelineRunner(datasets, raw_dir=args.raw_dir, processed_dir=args.processed_dir, fetcher=urllib_fetcher)
    result = runner.run()
    update_fallbacks(args.processed_dir / "fallbacks.json", result.report)
    for e in result.report["datasets"]:
        print(f"{e['dataset']:16} {e['acquisition_status']:22} features={e['feature_count']} errors={len(e['errors'])}")
        for w in e["warnings"]:
            print(f"    warning: {w}")
        for err in e["errors"]:
            print(f"    error: {err}")
    if not result.ok:
        print(result.report["fatal_reason"], file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
