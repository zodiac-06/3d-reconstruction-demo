"""
Step 1 -- Source a real, georeferenced GeoTIFF for the AOI.

Queries the public Earth Search STAC API (hosted on AWS, no account or API
key needed) for a low-cloud Sentinel-2 L2A scene covering the AOI, then
does a *windowed* read of just the buffered AOI out of that scene's
cloud-optimized GeoTIFF (COG) -- so you never download a full ~700MB
Sentinel-2 scene, only the few MB that actually cover this patch of
ground.

Output: data/raw_source.tif
  - a real Sentinel-2 true-colour (visual/TCI) GeoTIFF, 10m resolution,
    in the scene's native UTM CRS, covering the AOI plus a buffer margin
    so there's real room to crop it in QGIS in step 2.

Run:
    pip install -r requirements.txt
    python 01_fetch_geotiff.py
"""
import os

import rasterio
import requests
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from aoi_config import buffered_bbox

STAC_ENDPOINT = "https://earth-search.aws.element84.com/v1/search"
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT_PATH = os.path.join(OUT_DIR, "raw_source.tif")


def find_scene(bbox, max_cloud=15, date_range="2025-01-01T00:00:00Z/2026-09-16T23:59:59Z"):
    """Query Earth Search for the least-cloudy Sentinel-2 L2A scene over bbox."""
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": date_range,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        "limit": 5,
    }
    resp = requests.post(STAC_ENDPOINT, json=payload, timeout=60)
    print("API STATUS:", resp.status_code); print("API RESPONSE:", resp.text[:2000]); resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise RuntimeError(
            "No Sentinel-2 scenes found for this AOI/date range/cloud filter. "
            "Widen date_range or raise max_cloud in find_scene()."
        )
    best = features[0]
    print(
        f"Selected scene: {best['id']}  "
        f"(cloud cover {best['properties'].get('eo:cloud_cover'):.1f}%, "
        f"date {best['properties'].get('datetime')})"
    )
    return best


def download_cropped(scene, bbox):
    """Windowed-read the buffered AOI out of the scene's 'visual' COG asset."""
    asset = scene["assets"].get("visual") or scene["assets"].get("tci")
    if asset is None:
        raise RuntimeError(
            f"Scene {scene['id']} has no 'visual' (TCI) asset; "
            f"available assets: {list(scene['assets'])}"
        )
    href = asset["href"]
    print(f"Reading remote COG: {href}")

    with rasterio.open(href) as src:
        # AOI bbox is EPSG:4326; the Sentinel-2 COG is in its own UTM zone,
        # so reproject the bbox into the raster's CRS before windowing.
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox)
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        data = src.read(window=window)
        out_transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(
            height=data.shape[1],
            width=data.shape[2],
            transform=out_transform,
        )

    os.makedirs(OUT_DIR, exist_ok=True)
    with rasterio.open(OUT_PATH, "w", **profile) as dst:
        dst.write(data)

    print(
        f"Wrote {OUT_PATH}  ({data.shape[2]}x{data.shape[1]} px, "
        f"CRS {profile['crs']})"
    )


if __name__ == "__main__":
    bbox = buffered_bbox()
    scene = find_scene(bbox)
    download_cropped(scene, bbox)



