"""
Optional step -- render a normal, viewable PNG from aoi_cropped.tif.

GeoTIFFs don't preview inline on GitHub (or in most chat/demo tools); PNGs
do. This exists purely as visual proof that a real image was fetched and
cropped -- something to put in the PR description or a demo slide, not
something the rest of the pipeline depends on.

Written to qgis_prep/aoi_preview.png (deliberately OUTSIDE qgis_prep/data/,
which is gitignored) so it's small enough and meant to be committed.

Run:
    pip install pillow   (rasterio/numpy are already required by the earlier steps)
    python 05_preview_png.py
"""
import os

import numpy as np
import rasterio
from PIL import Image

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_PATH = os.path.join(DATA_DIR, "aoi_cropped.tif")
OUT_PATH = os.path.join(os.path.dirname(__file__), "aoi_preview.png")


def main():
    if not os.path.exists(SRC_PATH):
        raise FileNotFoundError(
            f"{SRC_PATH} not found -- run 01_fetch_geotiff.py and "
            f"02_crop_geotiff.py (or the QGIS crop) first."
        )

    with rasterio.open(SRC_PATH) as src:
        arr = src.read()  # (bands, H, W)

    rgb = arr[:3] if arr.shape[0] >= 3 else np.repeat(arr[:1], 3, axis=0)
    img = np.transpose(rgb, (1, 2, 0)).astype("float32")  # (H, W, 3)

    nonzero = img[img > 0]
    if nonzero.size:
        p2, p98 = np.percentile(nonzero, (2, 98))
        img = np.clip((img - p2) / max(p98 - p2, 1e-3) * 255, 0, 255)

    Image.fromarray(img.astype("uint8")).save(OUT_PATH)
    print(f"Wrote {OUT_PATH}  ({img.shape[1]}x{img.shape[0]} px)")


if __name__ == "__main__":
    main()
