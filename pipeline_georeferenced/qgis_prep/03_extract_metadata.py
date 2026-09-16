"""
Step 3 -- Extract georeference metadata from the cropped AOI GeoTIFF via
rasterio.

Reads data/aoi_cropped.tif (from step 2, either the QGIS or scripted path)
and writes data/geo_metadata.json: the CRS, affine transform, bounds in
both the raster's native CRS and EPSG:4326, pixel resolution, size, band
count/dtype and nodata value.

Other tracks (depth estimation, mesh reconstruction) read this file to
know exactly what patch of ground the image covers and at what scale,
without having to touch GDAL/rasterio themselves. Step 4 also reads it, so
the SRTM tile it fetches matches this crop's bounds exactly.

Run:
    python 03_extract_metadata.py
"""
import json
import os

import rasterio
from rasterio.warp import transform_bounds

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_PATH = os.path.join(DATA_DIR, "aoi_cropped.tif")
OUT_PATH = os.path.join(DATA_DIR, "geo_metadata.json")


def extract_metadata(src_path=SRC_PATH, out_path=OUT_PATH):
    """Callable entry point (pipeline_georeferenced/main.py uses this
    directly for an uploaded GeoTIFF instead of always reading
    data/aoi_cropped.tif)."""
    with rasterio.open(src_path) as src:
        bounds_native = tuple(src.bounds)
        bounds_wgs84 = transform_bounds(src.crs, "EPSG:4326", *bounds_native)

        meta = {
            "source_file": os.path.basename(src_path),
            "crs": src.crs.to_string(),
            "crs_epsg": src.crs.to_epsg(),
            "transform": list(src.transform)[:6],  # affine a,b,c,d,e,f
            "width": src.width,
            "height": src.height,
            "band_count": src.count,
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "pixel_size": [src.res[0], src.res[1]],
            "bounds_native_crs": {
                "left": bounds_native[0],
                "bottom": bounds_native[1],
                "right": bounds_native[2],
                "top": bounds_native[3],
            },
            "bounds_epsg4326": {
                "west": bounds_wgs84[0],
                "south": bounds_wgs84[1],
                "east": bounds_wgs84[2],
                "north": bounds_wgs84[3],
            },
        }

    with open(out_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Wrote {out_path}")
    return meta


def main():
    meta = extract_metadata()
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
