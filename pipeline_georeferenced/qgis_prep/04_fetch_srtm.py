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

Separately (see the "Bare-earth terrain DEM" section below), it fetches a
terrain DEM -- Copernicus GLO-30 by default, falling back to SRTM, then
NASADEM; FABDEM only on request -- to data/terrain_dem.tif + data/terrain_dem_aligned.tif:
the base layer for srtm_calibration.fuse_terrain_and_ndsm().

Run:
    python 04_fetch_srtm.py                         # SRTM + terrain DEM (Copernicus)
    python 04_fetch_srtm.py --dem-source fabdem     # prefer another terrain source
    python 04_fetch_srtm.py --srtm-only             # previous behaviour
"""
import gzip
import json
import math
import os
import shutil

import numpy as np
import rasterio
import requests
from rasterio.errors import RasterioIOError
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


def fetch_via_opentopography(bbox, api_key, out_path=SRTM_PATH):
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
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"Wrote {out_path}")


def skadi_tile_name(lat_tile, lon_tile):
    lat_part = f"{'N' if lat_tile >= 0 else 'S'}{abs(lat_tile):02d}"
    lon_part = f"{'E' if lon_tile >= 0 else 'W'}{abs(lon_tile):03d}"
    return f"{lat_part}{lon_part}"


def fetch_via_skadi(bbox, out_path=SRTM_PATH):
    """No-auth fallback: download the 1-degree SRTM .hgt tile(s) covering
    bbox from the public AWS Open Data mirror, mosaic if needed, crop to
    bbox, write out_path (SRTM_PATH by default)."""
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

    with rasterio.open(out_path, "w", **clipped_profile) as dst:
        dst.write(clipped)
    print(f"Wrote {out_path}")


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
    print(f"Wrote {out_path}  (DEM resampled onto aoi_cropped.tif's grid)")


def fetch_srtm_for_aoi(bbox=None, cropped_path=CROPPED_PATH,
                       srtm_path=SRTM_PATH, aligned_path=SRTM_ALIGNED_PATH):
    """Callable entry point (pipeline_georeferenced/main.py uses this
    directly instead of shelling out to `python 04_fetch_srtm.py`).
    bbox defaults to whatever's currently in data/geo_metadata.json (i.e.
    whichever GeoTIFF was most recently processed by 03_extract_metadata.py)
    -- generalized to accept an explicit (west, south, east, north) bbox
    too, instead of only ever reading the hardcoded Nainital AOI.
    """
    if bbox is None:
        bbox = get_aoi_bounds_4326()

    _fetch_srtm(bbox, srtm_path)
    align_to_source(srtm_path, cropped_path, aligned_path)
    return srtm_path, aligned_path


def _fetch_srtm(bbox, out_path):
    api_key = os.environ.get("OPENTOPO_API_KEY")
    if api_key:
        try:
            fetch_via_opentopography(bbox, api_key, out_path=out_path)
        except Exception as e:
            print(f"OpenTopography path failed ({e}); falling back to AWS Skadi mirror.")
            fetch_via_skadi(bbox, out_path=out_path)
    else:
        print("No OPENTOPO_API_KEY set -- using the no-auth AWS Skadi SRTM mirror.")
        fetch_via_skadi(bbox, out_path=out_path)


# ---------------------------------------------------------------------------
# Terrain DEM (Copernicus GLO-30 -> SRTM -> NASADEM; FABDEM on request)
#
# SRTM above is kept as-is for the legacy SRTM-only calibration. The terrain
# DEM below is the base layer that srtm_calibration.fuse_terrain_and_ndsm()
# adds relative-depth detail on top of. Default order is copernicus -> srtm
# -> nasadem: the pipeline's output is a DSM, so a surface model is the
# right base, and all three are freely licensed. FABDEM is never an implicit
# fallback (its licence is non-commercial) -- it's used only when explicitly
# preferred.
# SRTM goes ahead of NASADEM on measured accuracy, not age: NASADEM sits ~2m
# lower than SRTM (closer to bare ground), which scores better vs ICESat-2
# terrain but worse vs canopy top -- the DSM target. RMSE vs ICESat-2
# canopy top (dsm_calibration/evaluate_dem_sources.py): Nainital SRTM 15.2m
# / NASADEM 17.3m, Bangalore SRTM 10.1m / NASADEM 12.5m (Copernicus 14.8m /
# 10.8m). NASADEM stays as a second fallback for redundancy (different host).
# Also on Nainital: FABDEM 9.2m RMSE vs bare ground.
#
#   fabdem      FABDEM V1-2 (Hawker et al. 2022, Univ. of Bristol): Copernicus
#               GLO-30 with forests and buildings removed by ML -- the closest
#               freely available thing to a global bare-earth DTM. ~30m,
#               EGM2008 heights. Only distributed as 10x10-degree zips
#               (~1-2.5GB each), but tiles inside are stored uncompressed, so
#               GDAL's /vsizip//vsicurl/ reads just the AOI window via HTTP
#               range requests -- no multi-GB download.
#               LICENSE: CC BY-NC-SA 4.0 (non-commercial) -- unlike every other
#               source here. See NOTICE.md.
#   copernicus  Copernicus GLO-30 DSM (TanDEM-X, 2011-2015), public AWS Open
#               Data COGs, no auth. ~30m, EGM2008. A *surface* model (includes
#               canopy/buildings) -- what a DSM should be -- independent of
#               SRTM and far newer. DEFAULT.
#   nasadem     NASADEM (NASA JPL, 2020): SRTM reprocessed with improved
#               phase unwrapping and ICESat GLAS control, voids filled mainly
#               from ASTER GDEM. ~30m, EGM96. Same 2000 surface as SRTM.
#               COGs on Microsoft Planetary Computer (Azure), read with an
#               anonymous SAS token -- no account. NASA data, no use
#               restrictions (LP DAAC policy). SECOND FALLBACK, for
#               redundancy (different host from SRTM).
#   srtm        The same SRTM path as above (EGM96). FIRST FALLBACK.
#
# Output GeoTIFFs carry DEM_SOURCE / VERTICAL_DATUM tags, because FABDEM /
# Copernicus (EGM2008) and SRTM (EGM96) differ by a few meters in places
# (~3m at Nainital) -- enough to matter when comparing them.
# ---------------------------------------------------------------------------

TERRAIN_DEM_PATH = os.path.join(DATA_DIR, "terrain_dem.tif")
TERRAIN_DEM_ALIGNED_PATH = os.path.join(DATA_DIR, "terrain_dem_aligned.tif")

TERRAIN_DEM_SOURCES = ("copernicus", "srtm", "nasadem", "fabdem")
DEFAULT_TERRAIN_DEM_SOURCE = "copernicus"
FALLBACK_TERRAIN_DEM_SOURCES = ("copernicus", "srtm", "nasadem")  # never FABDEM implicitly
VERTICAL_DATUM = {"fabdem": "EGM2008", "copernicus": "EGM2008", "nasadem": "EGM96", "srtm": "EGM96"}

FABDEM_BASE = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn"
COPERNICUS_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
NASADEM_BASE = "https://nasademeuwest.blob.core.windows.net/nasadem-cog/v001"
NASADEM_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/nasadem"

# Don't let GDAL list the remote "directory" before every open -- on S3 /
# the Bristol web share that's an extra slow request per tile, or a failure.
_REMOTE_GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
                    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.zip"}


def _lat_label(lat):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"


def _lon_label(lon):
    if lon >= 180:
        lon -= 360
    return f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def _one_degree_tiles(bbox):
    west, south, east, north = bbox
    # A bbox edge sitting exactly on a degree line shouldn't pull in the
    # neighbouring tile (hence the tiny epsilon on the max side).
    eps = 1e-9
    for lat in range(math.floor(south), math.floor(north - eps) + 1):
        for lon in range(math.floor(west), math.floor(east - eps) + 1):
            yield lat, lon


def fabdem_tile_path(lat, lon):
    """GDAL virtual path to one 1-degree FABDEM tile inside its 10-degree zip,
    e.g. N29E079 -> .../N20E070-N30E080_FABDEM_V1-2.zip/N29E079_FABDEM_V1-2.tif"""
    lat0, lon0 = math.floor(lat / 10) * 10, math.floor(lon / 10) * 10
    archive = (f"{_lat_label(lat0)}{_lon_label(lon0)}-"
               f"{_lat_label(lat0 + 10)}{_lon_label(lon0 + 10)}_FABDEM_V1-2.zip")
    tile = f"{_lat_label(lat)}{_lon_label(lon)}_FABDEM_V1-2.tif"
    return f"/vsizip//vsicurl/{FABDEM_BASE}/{archive}/{tile}"


def copernicus_tile_path(lat, lon):
    """GDAL virtual path to one 1-degree Copernicus GLO-30 COG on AWS."""
    name = f"Copernicus_DSM_COG_10_{_lat_label(lat)}_00_{_lon_label(lon)}_00_DEM"
    return f"/vsicurl/{COPERNICUS_BASE}/{name}/{name}.tif"


def nasadem_tile_path(lat, lon, sas_token):
    """GDAL virtual path to one 1-degree NASADEM COG on Planetary Computer,
    e.g. N29E079 -> .../NASADEM_HGT_n29e079.tif?<sas token>"""
    name = f"NASADEM_HGT_{_lat_label(lat).lower()}{_lon_label(lon).lower()}"
    return f"/vsicurl/{NASADEM_BASE}/{name}.tif?{sas_token}"


def _fetch_remote_tiles(tile_path_fn, bbox, out_path, label):
    """Open every 1-degree tile the bbox touches (skipping ones that don't
    exist -- ocean tiles simply aren't published), mosaic them clipped to
    bbox, write out_path as float32 with nodata=-9999."""
    with rasterio.Env(**_REMOTE_GDAL_ENV):
        datasets = []
        try:
            for lat, lon in _one_degree_tiles(bbox):
                path = tile_path_fn(lat, lon)
                try:
                    datasets.append(rasterio.open(path))
                except RasterioIOError:
                    print(f"  {label}: no tile at {_lat_label(lat)}{_lon_label(lon)} (skipping)")
            if not datasets:
                raise RuntimeError(f"{label}: no tiles available for bbox {bbox}")
            print(f"Fetching {label} window from {len(datasets)} tile(s) (remote, windowed read)...")
            mosaic, transform = merge(datasets, bounds=bbox, nodata=-9999, dtype="float32")
            crs = datasets[0].crs
        finally:
            for ds in datasets:
                ds.close()

    if not np.isfinite(mosaic).any() or (mosaic == -9999).all():
        raise RuntimeError(f"{label}: tiles opened but the AOI window is all nodata")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with rasterio.open(
        out_path, "w", driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2],
        count=1, dtype="float32", crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(mosaic.astype("float32"))
    print(f"Wrote {out_path}")


def fetch_via_fabdem(bbox, out_path=TERRAIN_DEM_PATH):
    _fetch_remote_tiles(fabdem_tile_path, bbox, out_path, "FABDEM")


def fetch_via_copernicus(bbox, out_path=TERRAIN_DEM_PATH):
    _fetch_remote_tiles(copernicus_tile_path, bbox, out_path, "Copernicus GLO-30")


def fetch_via_nasadem(bbox, out_path=TERRAIN_DEM_PATH):
    resp = requests.get(NASADEM_TOKEN_URL, timeout=30)
    resp.raise_for_status()
    token = resp.json()["token"]
    _fetch_remote_tiles(lambda lat, lon: nasadem_tile_path(lat, lon, token), bbox, out_path, "NASADEM")


def _tag_dem(path, source):
    with rasterio.open(path, "r+") as ds:
        ds.update_tags(DEM_SOURCE=source, VERTICAL_DATUM=VERTICAL_DATUM[source])


def terrain_dem_sources(preferred=DEFAULT_TERRAIN_DEM_SOURCE):
    """Fetch order for a preferred source: it first, then the free-licence
    fallbacks (Copernicus, SRTM, NASADEM). e.g. "fabdem" -> (fabdem,
    copernicus, srtm, nasadem), "nasadem" -> (nasadem, copernicus, srtm)."""
    if preferred not in TERRAIN_DEM_SOURCES:
        raise ValueError(f"Unknown DEM source {preferred!r}; expected one of {TERRAIN_DEM_SOURCES}")
    return (preferred,) + tuple(s for s in FALLBACK_TERRAIN_DEM_SOURCES if s != preferred)


def fetch_terrain_dem_for_aoi(bbox=None, cropped_path=CROPPED_PATH,
                              sources=terrain_dem_sources(),
                              dem_path=TERRAIN_DEM_PATH,
                              aligned_path=TERRAIN_DEM_ALIGNED_PATH):
    """Terrain DEM for the AOI: tries each of `sources` in order (default
    Copernicus GLO-30 -> SRTM -> NASADEM; see terrain_dem_sources()) and keeps the
    first that works. Pass e.g. sources=("fabdem",) to force one with no
    fallback.

    Returns {"source", "vertical_datum", "dem_path", "aligned_path",
    "failures": {source: error}} so callers can report which DEM they
    actually got, and why the preferred ones were skipped.
    """
    if bbox is None:
        bbox = get_aoi_bounds_4326()

    fetchers = {
        "fabdem": lambda: fetch_via_fabdem(bbox, out_path=dem_path),
        "copernicus": lambda: fetch_via_copernicus(bbox, out_path=dem_path),
        "nasadem": lambda: fetch_via_nasadem(bbox, out_path=dem_path),
        "srtm": lambda: _fetch_srtm(bbox, dem_path),
    }
    failures = {}
    for source in sources:
        if source not in fetchers:
            raise ValueError(f"Unknown DEM source {source!r}; expected one of {TERRAIN_DEM_SOURCES}")
        try:
            fetchers[source]()
        except Exception as e:
            print(f"Terrain DEM source '{source}' failed ({e}); trying next.")
            failures[source] = str(e)
            continue
        _tag_dem(dem_path, source)
        align_to_source(dem_path, cropped_path, aligned_path)
        if os.path.exists(aligned_path):
            _tag_dem(aligned_path, source)
        print(f"Terrain DEM source: {source} ({VERTICAL_DATUM[source]} heights)")
        return {"source": source, "vertical_datum": VERTICAL_DATUM[source],
                "dem_path": dem_path, "aligned_path": aligned_path, "failures": failures}

    raise RuntimeError(f"No terrain DEM source succeeded: {failures}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dem-source", default=DEFAULT_TERRAIN_DEM_SOURCE, choices=TERRAIN_DEM_SOURCES,
        help="preferred terrain DEM to fetch alongside SRTM (default: copernicus); "
             "falls back to Copernicus GLO-30, then SRTM, then NASADEM. fabdem is "
             "CC BY-NC-SA 4.0 (non-commercial)")
    parser.add_argument(
        "--srtm-only", action="store_true",
        help="old behaviour: fetch only the SRTM calibration reference")
    args = parser.parse_args()

    fetch_srtm_for_aoi()
    if not args.srtm_only:
        fetch_terrain_dem_for_aoi(sources=terrain_dem_sources(args.dem_source))
