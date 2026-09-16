"""
Step 2 (scripted alternative to the QGIS "Clip Raster by Extent" step) --
crops data/raw_source.tif down to the exact AOI bbox.

See 02_manual_qgis_crop.md for the GUI version of this same operation.
Either path produces the same data/aoi_cropped.tif.

Run:
    python 02_crop_geotiff.py
"""
import os

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from aoi_config import AOI_BBOX

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_PATH = os.path.join(DATA_DIR, "raw_source.tif")
OUT_PATH = os.path.join(DATA_DIR, "aoi_cropped.tif")


def main():
    with rasterio.open(SRC_PATH) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *AOI_BBOX)
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        data = src.read(window=window)
        out_transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(height=data.shape[1], width=data.shape[2], transform=out_transform)

    with rasterio.open(OUT_PATH, "w", **profile) as dst:
        dst.write(data)

    print(f"Wrote {OUT_PATH}  ({data.shape[2]}x{data.shape[1]} px, CRS {profile['crs']})")


if __name__ == "__main__":
    main()
