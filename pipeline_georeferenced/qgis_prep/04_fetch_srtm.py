"""
Step 4 -- Fetch the SRTM elevation tile matching the AOI.

Uses the exact EPSG:4326 bounds written to data/geo_metadata.json in step 3
(falls back to AOI_BBOX in aoi_config.py if that file doesn't exist yet),
so the reference DEM lines up with the cropped optical image.

Two paths, tried in this order:

1. OpenTopography Global DEM API (SRTM GL1, ~30m).
   Needs a free API key: https://portal.opentopography.org -> My Account
   -> myOpenTopo Authorizations and API Key (instant, no approval wait).
   Put it in the OPENTOPO_API_KEY environment variable, e.g. in
   PowerShell:
       $env:OPENTOPO_API_KEY = "your-key-here"
   This path returns a GeoTIFF already clipped to the AOI in one request.

2. Fallback, no key needed: NASA SRTM v3 (SRTM1), distributed as 1-degree
   ".hgt" tiles in "Skadi" layout on a public, unauthenticated AWS Open
   Data bucket (the same source the Mapzen/Tilezen terrain-tile ecosystem
   uses). Downloads whichever 1-degree tile(s) the AOI touches, mosaics
   them if it spans more than one, then crops to the AOI.

Either path also writes data/srtm_dem_aligned.tif: the SRTM DEM resampled
onto the *same grid* (CRS, transform, pixel size) as data/aoi_cropped.tif,
so it can be compared pixel-for-pixel (RMSE/MAE) against whatever DSM/DEM
the depth-estimation track produces from the optical image.

Run:
    python 04_fetch_srtm.py
"""
import gzip
import json
import math
import os
import shutil

import numpy as np
import rasterio
import requests
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject
from shapely.geometry import box, mapping

from aoi_config import AOI_BBOX

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
META_PATH = os.path.join(DATA_DIR, "geo_metadata.json")
CROPPED_PATH = os.path.join(DATA_DIR, "aoi_cropped.tif")
SRTM_PATH = os.path.join(DATA_DIR, "srtm_dem.tif")
SRTM_ALIGNED_PATH = os.path.join(DATA_DIR, "srtm_dem_aligned.tif")

OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"
SKADI_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"


def get_aoi_bounds_4326():
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            b = json.load(f)["bounds_epsg4326"]
        return b["west"], b["south"], b["east"], b["north"]
    return AOI_BBOX


def fetch_via_opentopography(bbox, api_key):
    west, south, east, north = bbox
    params = {
        "demtype": "SRTMGL1",
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }
    print("Fetching SRTM via OpenTopography...")
    resp = requests.get(OPENTOPO_URL, params=params, timeout=120)
    if not resp.ok:
        raise RuntimeError(
            f"OpenTopography request failed ({resp.status_code}): {resp.text[:300]}"
        )
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SRTM_PATH, "wb") as f:
        f.write(resp.content)
    print(f"Wrote {SRTM_PATH}")


def skadi_tile_name(lat_tile, lon_tile):
    lat_part = f"{'N' if lat_tile >= 0 else 'S'}{abs(lat_tile):02d}"
    lon_part = f"{'E' if lon_tile >= 0 else 'W'}{abs(lon_tile):03d}"
    return f"{lat_part}{lon_part}"


def fetch_via_skadi(bbox):
    """No-auth fallback: download the 1-degree SRTM .hgt tile(s) covering
    bbox from the public AWS Open Data mirror, mosaic if needed, crop to
    bbox, write SRTM_PATH."""
    west, south, east, north = bbox
    lat_tiles = range(math.floor(south), math.floor(north) + 1)
    lon_tiles = range(math.floor(west), math.floor(east) + 1)

    os.makedirs(DATA_DIR, exist_ok=True)
    hgt_datasets = []
    for lat_t in lat_tiles:
        for lon_t in lon_tiles:
            name = skadi_tile_name(lat_t, lon_t)
            lat_dir = name[:3]
            url = f"{SKADI_BASE}/{lat_dir}/{name}.hgt.gz"
            hgt_path = os.path.join(DATA_DIR, f"{name}.hgt")
            print(f"Fetching SRTM tile {name} from AWS Open Data (no auth)...")
            resp = requests.get(url, timeout=120)
            if not resp.ok:
                raise RuntimeError(
                    f"Could not fetch SRTM tile {name} ({resp.status_code}). "
                    f"This AOI may be over ocean/no-data, or the mirror moved."
                )
            with open(hgt_path + ".gz", "wb") as f:
                f.write(resp.content)
            with gzip.open(hgt_path + ".gz", "rb") as gz, open(hgt_path, "wb") as out:
                shutil.copyfileobj(gz, out)
            os.remove(hgt_path + ".gz")
            # GDAL/rasterio infer CRS + geotransform straight from the
            # ".hgt" filename's NxxExxx convention -- no manual georef needed.
            hgt_datasets.append(rasterio.open(hgt_path))

    if len(hgt_datasets) == 1:
        ds = hgt_datasets[0]
        merged = ds.read(1, masked=False)[None, ...]
        profile = ds.profile.copy()
    else:
        merged, merged_transform = merge(hgt_datasets)
        profile = hgt_datasets[0].profile.copy()
        profile.update(
            height=merged.shape[1], width=merged.shape[2], transform=merged_transform
        )

    for ds in hgt_datasets:
        ds.close()

    profile["driver"] = "GTiff"
    with MemoryFile() as memfile:
        with memfile.open(**profile) as tmp:
            tmp.write(merged)
        with memfile.open() as tmp:
            aoi_geom = [mapping(box(west, south, east, north))]
            clipped, clipped_transform = mask(tmp, aoi_geom, crop=True)
            clipped_profile = tmp.profile.copy()
            clipped_profile.update(
                height=clipped.shape[1],
                width=clipped.shape[2],
                transform=clipped_transform,
            )

    with rasterio.open(SRTM_PATH, "w", **clipped_profile) as dst:
        dst.write(clipped)
    print(f"Wrote {SRTM_PATH}")


def align_to_source(srtm_path, source_path, out_path):
    """Resample the SRTM DEM onto the exact grid of aoi_cropped.tif so the
    two rasters can be compared pixel-for-pixel later."""
    if not os.path.exists(source_path):
        print(f"({os.path.basename(source_path)} not found yet -- skipping alignment step)")
        return
    with rasterio.open(source_path) as ref, rasterio.open(srtm_path) as src:
        profile = ref.profile.copy()
        profile.update(count=1, dtype="float32", nodata=-9999)

        dst_array = np.full((ref.height, ref.width), -9999, dtype="float32")
        reproject(
            source=rasterio.band(src, 1),
            destination=dst_array,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=ref.transform,
            dst_crs=ref.crs,
            dst_resolution=ref.res,
            resampling=Resampling.bilinear,
            dst_nodata=-9999,
        )

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(dst_array, 1)
    print(f"Wrote {out_path}  (SRTM resampled onto aoi_cropped.tif's grid)")


def fetch_srtm_for_aoi(bbox=None, cropped_path=CROPPED_PATH):
    """Callable entry point (pipeline_georeferenced/main.py uses this
    directly instead of shelling out to `python 04_fetch_srtm.py`).
    bbox defaults to whatever's currently in data/geo_metadata.json (i.e.
    whichever GeoTIFF was most recently processed by 03_extract_metadata.py)
    -- generalized to accept an explicit (west, south, east, north) bbox
    too, instead of only ever reading the hardcoded Nainital AOI.
    """
    if bbox is None:
        bbox = get_aoi_bounds_4326()

    api_key = os.environ.get("OPENTOPO_API_KEY")
    if api_key:
        try:
            fetch_via_opentopography(bbox, api_key)
        except Exception as e:
            print(f"OpenTopography path failed ({e}); falling back to AWS Skadi mirror.")
            fetch_via_skadi(bbox)
    else:
        print("No OPENTOPO_API_KEY set -- using the no-auth AWS Skadi SRTM mirror.")
        fetch_via_skadi(bbox)

    align_to_source(SRTM_PATH, cropped_path, SRTM_ALIGNED_PATH)
    return SRTM_PATH, SRTM_ALIGNED_PATH


if __name__ == "__main__":
    fetch_srtm_for_aoi()
