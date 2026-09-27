"""
Step 6 -- Build the 15-channel Sentinel-2 input stack the Lang et al. global
canopy height model (github.com/langnico/global-canopy-height-model,
"Lang-CHM") expects.

Step 1 only saves the 3-band uint8 true-colour 'visual' (TCI) asset, which
is a display product, not reflectance. Lang-CHM needs all 12 Sentinel-2 L2A
bands plus 3 lat/lon channels. This script windowed-reads each band's COG
from the same Earth Search scene step 1 selects and stacks them to mirror
what gchm.datasets.dataset_sentinel2_deploy.Sentinel2Deploy feeds the model
(before its train-mean/std Normalize transform):

  channel  0-11: B01 B02 B03 B04 B05 B06 B07 B08 B8A B09 B11 B12
                 (gchm.utils.gdal_process.sort_band_arrays order), L2A
                 digital numbers (reflectance * 10000). 20m and 60m bands are
                 resampled bicubically onto the 10m grid (gchm uses
                 skimage resize order=3).
  channel 12:    lat           -- latitude in degrees, NOT cyclically encoded
  channel 13:    sin(2*pi*lon/360)
  channel 14:    cos(2*pi*lon/360)

lat/lon follow gchm.utils.gdal_process.create_latlon_mask: the geographic
coordinates of the top-left corner of the first and last pixel, linearly
interpolated across rows / columns.

Processing baseline >= 04.00 (Jan 2022 onward) adds +1000 to every L2A DN.
Lang-CHM was trained on 2019-2020 imagery without that offset and gchm
applies no correction, so the stack must be offset-free. Earth Search's STAC
declares the offset (raster:bands offset -0.1) but its COGs turn out to be
already harmonized, so the offset is only removed (clip(DN, 1000) - 1000)
when the pixel values actually show it -- see offset_in_data(). Nodata (0)
stays 0.

Outputs:
  data/s2_l2a_stack.tif  15-band float32, channels in the order above
  data/s2_scl.tif        L2A scene classification (uint8, nearest) on the
                         same grid -- gchm uses it to mask cloud/snow/water

The stack is written on data/aoi_cropped.tif's grid by default, so its
pixel indices match every raster derived from that crop (the aligned DEMs,
the depth map). That grid sits a sub-pixel distance off the Sentinel-2
lattice (02 crops on a fractional window); --native-grid snaps AOI_BBOX to
the B02 lattice instead.

Run:
    python 06_fetch_s2_l2a_stack.py                    # aoi_cropped.tif's grid
    python 06_fetch_s2_l2a_stack.py --like other.tif   # another raster's grid
    python 06_fetch_s2_l2a_stack.py --native-grid      # AOI_BBOX on the B02 lattice
"""
import argparse
import importlib.util
import os

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform, transform_bounds
from rasterio.windows import from_bounds

from aoi_config import AOI_BBOX, buffered_bbox

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.join(HERE, "data")
OUT_PATH = os.path.join(OUT_DIR, "s2_l2a_stack.tif")
SCL_PATH = os.path.join(OUT_DIR, "s2_scl.tif")
LIKE_PATH = os.path.join(OUT_DIR, "aoi_cropped.tif")

# gchm band order -> Earth Search v1 asset key
BAND_ASSETS = [
    ("B01", "coastal"), ("B02", "blue"), ("B03", "green"), ("B04", "red"),
    ("B05", "rededge1"), ("B06", "rededge2"), ("B07", "rededge3"),
    ("B08", "nir"), ("B8A", "nir08"), ("B09", "nir09"),
    ("B11", "swir16"), ("B12", "swir22"),
]
LATLON_NAMES = ["lat", "lon_sin", "lon_cos"]
CHANNEL_NAMES = [b for b, _ in BAND_ASSETS] + LATLON_NAMES


def _load_find_scene():
    # 01_fetch_geotiff.py isn't importable by name (leading digit)
    spec = importlib.util.spec_from_file_location("fetch_geotiff", os.path.join(HERE, "01_fetch_geotiff.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_scene


def target_grid(scene, bbox=AOI_BBOX, like=None):
    """(crs, transform, width, height) of the 10m output grid: either an
    existing raster's grid, or bbox snapped to the scene's B02 pixel grid."""
    if like:
        with rasterio.open(like) as ref:
            return ref.crs, ref.transform, ref.width, ref.height
    with rasterio.open(scene["assets"]["blue"]["href"]) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox)
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        window = window.round_offsets().round_lengths()
        return src.crs, src.window_transform(window), int(window.width), int(window.height)


def read_on_grid(href, crs, dst_transform, width, height, resampling):
    with rasterio.open(href) as src:
        # 10m bands: on the native lattice nearest is an exact copy; on
        # aoi_cropped.tif's grid (< 0.25 px off) it picks the same source
        # pixel the TCI crop shows there
        if src.res[0] <= 10 and resampling != Resampling.nearest:
            resampling = Resampling.nearest
        with WarpedVRT(src, crs=crs, transform=dst_transform, width=width, height=height,
                       resampling=resampling, src_nodata=0, nodata=0) as vrt:
            return vrt.read(1)


def declared_dn_offset(asset):
    rb = (asset.get("raster:bands") or [{}])[0]
    offset, scale = rb.get("offset") or 0, rb.get("scale") or 1e-4
    return int(round(-offset / scale))


def offset_in_data(blue_dn, dn_offset, max_frac_below=0.01):
    """Whether the DNs really carry the +dn_offset. Earth Search declares
    offset -0.1 on baseline >= 04.00 items even though its COGs are already
    harmonized (e.g. S2C_44RLT_20251211: B02 median DN 135, 10th pct 1).
    Offset-encoded L2A has essentially no valid pixels below the offset
    (that would be negative reflectance), so check B02 -- the darkest band."""
    valid = blue_dn[blue_dn > 0]
    frac_below = float(np.mean(valid < dn_offset)) if valid.size else 0.0
    print(f"B02: {frac_below:.1%} of valid pixels below declared offset {dn_offset}")
    return frac_below <= max_frac_below


def latlon_channels(crs, dst_transform, width, height):
    """Replicates gchm.utils.gdal_process.create_latlon_mask + the
    Sentinel2Deploy encoding: raw lat, sin/cos of lon."""
    xs = [dst_transform * (0, 0), dst_transform * (width - 1, height - 1)]
    lons, lats = transform(crs, "EPSG:4326", [p[0] for p in xs], [p[1] for p in xs])
    lat_col = np.linspace(lats[0], lats[1], num=height, dtype=np.float32)
    lon_row = np.linspace(lons[0], lons[1], num=width, dtype=np.float32)
    lat = np.repeat(lat_col[:, None], width, axis=1)
    lon = np.repeat(lon_row[None, :], height, axis=0)
    return np.stack([lat, np.sin(2 * np.pi * lon / 360), np.cos(2 * np.pi * lon / 360)]).astype(np.float32)


def build_stack(scene, bbox=AOI_BBOX, like=LIKE_PATH, out_path=OUT_PATH, scl_path=SCL_PATH):
    crs, dst_transform, width, height = target_grid(scene, bbox, like)
    print(f"Target grid: {width}x{height} px, CRS {crs}, "
          f"bounds {array_bounds(height, width, dst_transform)}")

    bands = []
    for name, key in BAND_ASSETS:
        asset = scene["assets"][key]
        print(f"Reading {name} ({key}, {asset.get('gsd')}m): {asset['href']}")
        bands.append(read_on_grid(asset["href"], crs, dst_transform, width, height, Resampling.cubic))
    bands = np.stack(bands).astype(np.int32)

    dn_offset = declared_dn_offset(scene["assets"]["blue"])
    if dn_offset and offset_in_data(bands[1], dn_offset):
        bands = np.where(bands > 0, np.clip(bands, dn_offset, None) - dn_offset, 0)
        print(f"Removed +{dn_offset} DN offset")
    else:
        dn_offset = 0
        print("No DN offset in the data; bands left as-is")

    stack = np.concatenate([bands.astype(np.float32), latlon_channels(crs, dst_transform, width, height)])
    assert stack.shape == (15, height, width), stack.shape

    scl = read_on_grid(scene["assets"]["scl"]["href"], crs, dst_transform, width, height, Resampling.nearest)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    profile = dict(driver="GTiff", crs=crs, transform=dst_transform, width=width, height=height,
                   compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out_path, "w", count=15, dtype="float32", predictor=3, **profile) as dst:
        dst.write(stack)
        dst.descriptions = tuple(CHANNEL_NAMES)
        dst.update_tags(scene_id=scene["id"],
                        datetime=scene["properties"].get("datetime", ""),
                        processing_baseline=scene["properties"].get("s2:processing_baseline", ""),
                        dn_offset_removed=str(dn_offset),
                        layout="gchm Sentinel2Deploy inputs before Normalize")
    with rasterio.open(scl_path, "w", count=1, dtype="uint8", nodata=0, **profile) as dst:
        dst.write(scl, 1)

    print(f"Wrote {out_path}  shape {stack.shape} (C,H,W), DN offset removed: {dn_offset}")
    print(f"Wrote {scl_path}")
    return stack


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--like", default=LIKE_PATH,
                    help="raster whose grid (CRS/transform/size) the stack matches (default: %(default)s)")
    ap.add_argument("--native-grid", action="store_true",
                    help="ignore --like; snap AOI_BBOX to the scene's B02 pixel lattice")
    args = ap.parse_args()
    if not args.native_grid and not os.path.exists(args.like):
        ap.error(f"{args.like} not found -- run 01 and 02 first, or pass --native-grid")
    scene = _load_find_scene()(buffered_bbox())
    build_stack(scene, like=None if args.native_grid else args.like)


if __name__ == "__main__":
    main()
