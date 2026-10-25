"""Step: download a USGS 3DEP DEM raster for the coverage bbox.

Source: USGS 3D Elevation Program (3DEP) ImageServer (The National Map).
  https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer
Output: data/raw/dem_3dep.npy (float32 metres, NaN = nodata) + data/raw/dem_3dep.meta.json

Limitations (also surfaced in the UI/docs):
  * This is a BARE-EARTH terrain model. It does NOT represent bridge decks, elevated roads,
    tunnels, Lower Wacker Drive, or underground stations. The graph builder marks such edges
    as terrain-elevation-unreliable rather than assigning a "safe" value.
  * Vertical datum: NAVD88, metres (per 3DEP product docs). Resolution here is resampled to ~10 m.
"""
from __future__ import annotations

import io
import json
import sys

import numpy as np

from common import BBOX, RAW, ensure_dirs, http_get, now_iso, record_manifest

URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"


def main() -> int:
    from PIL import Image

    ensure_dirs()
    width, height = 900, 760  # ~8 m x ~8.8 m cells over a ~7.3 x 6.7 km box
    params = {
        "bbox": f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
        "bboxSR": 4326,
        "imageSR": 4326,
        "size": f"{width},{height}",
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "noData": -9999,
        "f": "image",
    }
    try:
        r = http_get(URL, params=params, timeout=180)
        img = Image.open(io.BytesIO(r.content))
        arr = np.array(img, dtype="float32")
        arr[arr <= -9000] = np.nan
        np.save(RAW / "dem_3dep.npy", arr)
        meta = {
            "bbox": BBOX,
            "shape": list(arr.shape),
            "units": "metres",
            "vertical_datum": "NAVD88 (per USGS 3DEP product documentation)",
            "min": float(np.nanmin(arr)),
            "max": float(np.nanmax(arr)),
            "mean": float(np.nanmean(arr)),
            "nodata_fraction": float(np.isnan(arr).mean()),
            "retrieved_at": now_iso(),
            "url": URL,
        }
        (RAW / "dem_3dep.meta.json").write_text(json.dumps(meta, indent=2))
        record_manifest(
            "usgs:3dep_dem",
            title="USGS 3DEP bare-earth DEM (ImageServer export, resampled)",
            organization="U.S. Geological Survey",
            url="https://www.usgs.gov/3d-elevation-program",
            api_url=URL,
            retrieved_at=meta["retrieved_at"],
            file="dem_3dep.npy",
            license="US Government work - public domain (USGS attribution requested)",
            status="ok",
            **{k: meta[k] for k in ("shape", "units", "vertical_datum", "min", "max", "nodata_fraction")},
        )
        print("DEM saved", meta)
        return 0
    except Exception as e:
        print("DEM download FAILED:", e)
        record_manifest("usgs:3dep_dem", status="failed", error=str(e), attempted_at=now_iso())
        return 1


if __name__ == "__main__":
    sys.exit(main())
