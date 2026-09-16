"""
Converts real GeoTIFF outputs into the 3D viewer's input contract
(3d_visualization/README.md): browsers can't load .tif as a texture at
all, so this does the missing conversion step the README flags as
unowned -- PNG/JPG + real elevation min/max metadata, computed from the
DSM's actual pixel data, not guessed.
"""
import json
import os

import numpy as np
import rasterio
from PIL import Image

# dsm_calibration/, qgis_prep/, and 3d_visualization/ are all siblings under
# this same parent (pipeline_georeferenced/), so this still resolves
# correctly regardless of where that parent itself lives.
PIPELINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DSM_PATH = os.path.join(PIPELINE_ROOT, "dsm_calibration", "real_run", "nainital_dsm.tif")
SATELLITE_SRC_PATH = os.path.join(PIPELINE_ROOT, "qgis_prep", "data", "aoi_cropped.tif")
ASSETS_DIR = os.path.join(PIPELINE_ROOT, "3d_visualization", "assets")


def convert_elevation(dsm_path, out_png_path):
    with rasterio.open(dsm_path) as ds:
        arr = ds.read(1).astype(np.float64)
        shape = (ds.height, ds.width)

    min_elev, max_elev = float(arr.min()), float(arr.max())
    normalized = (arr - min_elev) / max(max_elev - min_elev, 1e-9)
    as_16bit = np.clip(normalized * 65535, 0, 65535).astype(np.uint16)

    Image.fromarray(as_16bit, mode="I;16").save(out_png_path)
    return shape, min_elev, max_elev


def convert_satellite(geotiff_path, out_jpg_path):
    with rasterio.open(geotiff_path) as ds:
        arr = ds.read()  # (bands, H, W)
        shape = (ds.height, ds.width)
    rgb = np.transpose(arr[:3], (1, 2, 0))  # -> (H, W, 3)
    Image.fromarray(rgb, mode="RGB").save(out_jpg_path, quality=92)
    return shape


def generate_viewer_assets(dsm_path=DSM_PATH, satellite_path=SATELLITE_SRC_PATH, assets_dir=ASSETS_DIR):
    """Callable entry point (pipeline_georeferenced/main.py uses this
    directly for a per-upload DSM/satellite pair instead of always reading
    the fixed Nainital paths)."""
    os.makedirs(assets_dir, exist_ok=True)

    elev_shape, min_elev, max_elev = convert_elevation(
        dsm_path, os.path.join(assets_dir, "elevation_16bit.png")
    )
    print(f"Wrote elevation_16bit.png  shape={elev_shape}  "
          f"real elevation range: {min_elev:.2f}m - {max_elev:.2f}m")

    sat_shape = convert_satellite(
        satellite_path, os.path.join(assets_dir, "satellite.jpg")
    )
    print(f"Wrote satellite.jpg  shape={sat_shape}")

    assert elev_shape == sat_shape, (
        f"elevation {elev_shape} and satellite {sat_shape} shapes differ -- "
        f"this WILL misalign the drape"
    )

    metadata = {"minElevation": min_elev, "maxElevation": max_elev, "units": "meters"}
    meta_path = os.path.join(assets_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote metadata.json: {metadata}")
    return metadata


if __name__ == "__main__":
    generate_viewer_assets()
