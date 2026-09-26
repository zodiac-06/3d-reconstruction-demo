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


def crop_geotiff(src_path=SRC_PATH, out_path=OUT_PATH, bbox=AOI_BBOX):
    """Callable entry point (pipeline_georeferenced/main.py uses this to
    crop an uploaded or STAC-fetched scene to user-given bounds). bbox is
    (west, south, east, north) in EPSG:4326. Returns the output's
    (width, height)."""
    with rasterio.open(src_path) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox)
        if (left >= src.bounds.right or right <= src.bounds.left
                or bottom >= src.bounds.top or top <= src.bounds.bottom):
            raise ValueError(
                f"Bounds {tuple(bbox)} don't overlap {os.path.basename(src_path)} "
                f"(its extent is {tuple(transform_bounds(src.crs, 'EPSG:4326', *src.bounds))})"
            )
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        data = src.read(window=window)
        out_transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(height=data.shape[1], width=data.shape[2], transform=out_transform)

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data)

    print(f"Wrote {out_path}  ({data.shape[2]}x{data.shape[1]} px, CRS {profile['crs']})")
    return data.shape[2], data.shape[1]


def main():
    crop_geotiff()


if __name__ == "__main__":
    main()
