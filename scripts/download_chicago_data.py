"""Step 1: retrieve raw source datasets for the Chicago coverage area.

Sources (each recorded in data/raw/manifest.json with URL + retrieval timestamp):
  * OpenStreetMap (Overpass API): highways/paths, bridges/tunnels, buildings, rail/water  (ODbL)
  * Chicago Data Portal "Street Center Lines" (6imu-meau)                                   (City of Chicago open data)
  * CTA GTFS static feed                                                                    (CTA developer license)
  * Chicago Central Business District Evacuation Plan PDF (2007, historical)

If a source is unavailable the script keeps any previously downloaded file and records
a warning, so downstream steps keep working with cached data.

Usage:  python scripts/download_chicago_data.py [--skip osm,portal,gtfs,plan]
"""
from __future__ import annotations

import argparse
import sys

from common import BBOX, RAW, USER_AGENT, ensure_dirs, http_get, now_iso, record_manifest, write_json

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def _bbox_overpass() -> str:
    return f"{BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']}"


def overpass(query: str, out_name: str, desc: str) -> bool:
    last = None
    for ep in OVERPASS_ENDPOINTS:
        try:
            print(f"  [osm] {desc} via {ep}")
            r = http_get(ep, data={"data": query}, timeout=300, retries=2)
            payload = r.json()
            path = RAW / out_name
            write_json(path, payload)
            n = len(payload.get("elements", []))
            print(f"  [osm] saved {out_name}: {n} elements")
            record_manifest(
                f"osm:{out_name}",
                title=f"OpenStreetMap extract: {desc}",
                organization="OpenStreetMap contributors",
                url=ep,
                license="ODbL 1.0 - attribution required: (c) OpenStreetMap contributors",
                retrieved_at=now_iso(),
                bbox=BBOX,
                file=out_name,
                element_count=n,
                osm_base=payload.get("osm3s", {}).get("timestamp_osm_base"),
                status="ok",
            )
            return True
        except Exception as e:
            last = e
            print(f"  [osm] failed on {ep}: {e}")
    record_manifest(f"osm:{out_name}", status="failed", error=str(last), attempted_at=now_iso())
    return False


def download_osm() -> None:
    bb = _bbox_overpass()
    roads_q = f"""
[out:json][timeout:240];
(
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|service|living_street|pedestrian|footway|path|steps|cycleway|corridor)$"]({bb});
);
out body geom;
"""
    overpass(roads_q, "osm_roads.json", "highways, footways, bridges, tunnels, one-ways")

    buildings_q = f"""
[out:json][timeout:240];
(
  way["building"]({bb});
);
out body geom tags;
"""
    overpass(buildings_q, "osm_buildings.json", "building footprints (height/levels where tagged)")

    other_q = f"""
[out:json][timeout:120];
(
  way["waterway"~"^(river|canal)$"]({bb});
  way["natural"="water"]({bb});
  way["railway"="subway"]({bb});
  node["railway"="station"]({bb});
  node["station"="subway"]({bb});
  node["public_transport"="station"]({bb});
);
out body geom;
"""
    overpass(other_q, "osm_water_transit.json", "waterways, water bodies, subway lines, stations")


def download_portal() -> None:
    """Chicago Data Portal street centerlines, clipped server-side with within_box."""
    # NOTE: 6imu-meau ("Street Center Lines") is a Socrata *map asset* with no queryable columns.
    # The queryable table found via the catalog API is pr57-gg9e (title "transportation") with
    # the_geom / street_nam / oneway_dir / class / tiered. We use it as the data endpoint and
    # record both IDs in the manifest. (Backing relationship is inferred from title/schema, not
    # confirmed by Socrata metadata - flagged as a limitation in docs/data-sources.md.)
    url = "https://data.cityofchicago.org/resource/pr57-gg9e.geojson"
    where = (
        f"within_box(the_geom, {BBOX['north']}, {BBOX['west']}, {BBOX['south']}, {BBOX['east']})"
    )
    try:
        print("  [portal] Street Center Lines (6imu-meau)")
        r = http_get(url, params={"$where": where, "$limit": 50000}, timeout=180)
        payload = r.json()
        write_json(RAW / "chicago_street_centerlines.geojson", payload)
        n = len(payload.get("features", []))
        print(f"  [portal] saved {n} features")
        record_manifest(
            "portal:street_centerlines",
            title="Street Center Lines (map asset 6imu-meau; queried table pr57-gg9e 'transportation')",
            organization="City of Chicago",
            url="https://data.cityofchicago.org/d/6imu-meau",
            table_url="https://data.cityofchicago.org/d/pr57-gg9e",
            api_url=url,
            retrieved_at=now_iso(),
            bbox=BBOX,
            file="chicago_street_centerlines.geojson",
            feature_count=n,
            license="City of Chicago Data Portal terms of use (open data)",
            status="ok",
        )
    except Exception as e:
        print(f"  [portal] FAILED: {e}")
        record_manifest("portal:street_centerlines", status="failed", error=str(e), attempted_at=now_iso())


def download_gtfs() -> None:
    url = "https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
    try:
        print("  [gtfs] CTA static GTFS")
        r = http_get(url, timeout=180)
        (RAW / "cta_gtfs.zip").write_bytes(r.content)
        print(f"  [gtfs] saved {len(r.content)} bytes")
        record_manifest(
            "cta:gtfs",
            title="CTA GTFS static feed",
            organization="Chicago Transit Authority",
            url="https://www.transitchicago.com/developers/gtfs/",
            api_url=url,
            retrieved_at=now_iso(),
            file="cta_gtfs.zip",
            bytes=len(r.content),
            license="CTA Developer License Agreement - see https://www.transitchicago.com/developers/",
            status="ok",
        )
    except Exception as e:
        print(f"  [gtfs] FAILED: {e}")
        record_manifest("cta:gtfs", status="failed", error=str(e), attempted_at=now_iso())


def download_plan() -> None:
    url = "https://webapps1.chicago.gov/ChicagoAlertWeb/resources/pdf/CBD_Evac_Plan_final_30-5-07.pdf"
    try:
        print("  [plan] CBD Evacuation Plan PDF")
        r = http_get(url, timeout=120)
        (RAW / "CBD_Evac_Plan_final_30-5-07.pdf").write_bytes(r.content)
        record_manifest(
            "plan:cbd_evac_2007",
            title="Central Business District Evacuation Plan (05-16-07, additions 29 May 07)",
            organization="City of Chicago (OEMC)",
            url=url,
            retrieved_at=now_iso(),
            file="CBD_Evac_Plan_final_30-5-07.pdf",
            bytes=len(r.content),
            classification="historical",
            status="ok",
        )
        print(f"  [plan] saved {len(r.content)} bytes")
    except Exception as e:
        print(f"  [plan] FAILED: {e}")
        record_manifest("plan:cbd_evac_2007", status="failed", error=str(e), attempted_at=now_iso())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="", help="comma list: osm,portal,gtfs,plan")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    ensure_dirs()
    print(f"SafePath data download  bbox={BBOX}  UA={USER_AGENT}")
    if "osm" not in skip:
        download_osm()
    if "portal" not in skip:
        download_portal()
    if "gtfs" not in skip:
        download_gtfs()
    if "plan" not in skip:
        download_plan()
    print("done. See data/raw/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
