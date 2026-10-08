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
        arr = ds.read(1, masked=True).astype(np.float64).filled(np.nan)
        shape = (ds.height, ds.width)

    # DEM-fused DSMs can have a thin nodata rim where the ~30m DEM doesn't
    # quite cover the crop's edge pixels. Min/max must ignore it (a -9999
    # "minimum" would flatten the whole terrain), and the PNG has no nodata,
    # so fill those pixels from their nearest valid neighbour.
    invalid = ~np.isfinite(arr)
    if invalid.all():
        raise ValueError(f"{dsm_path} has no valid elevation pixels")
    if invalid.any():
        from scipy.ndimage import distance_transform_edt
        _, (rows, cols) = distance_transform_edt(invalid, return_indices=True)
        arr = arr[rows, cols]

    min_elev, max_elev = float(arr.min()), float(arr.max())
    normalized = (arr - min_elev) / max(max_elev - min_elev, 1e-9)
    # round to the nearest step: astype alone truncates, reading every height
    # up to one step (~2.8 cm over Nainital's range) low
    as_16bit = np.clip(np.rint(normalized * 65535), 0, 65535).astype(np.uint16)

    Image.fromarray(as_16bit, mode="I;16").save(out_png_path)
    return shape, min_elev, max_elev


def georeference_metadata(dsm_path):
    """The DSM's CRS and pixel-edge bounds, native and EPSG:4326. The 3D
    viewer's click probe derives pixel size (for slope) and lat/lon from
    these plus the PNG's dimensions (3d_visualization/elevation_probe.js)."""
    from rasterio.warp import transform_bounds

    with rasterio.open(dsm_path) as ds:
        b = ds.bounds
        west, south, east, north = transform_bounds(ds.crs, "EPSG:4326", *b)
        return {
            "crs": ds.crs.to_string(),
            "boundsNative": {"left": b.left, "bottom": b.bottom, "right": b.right, "top": b.top},
            "boundsEPSG4326": {"west": west, "south": south, "east": east, "north": north},
        }


def write_provenance_asset(dsm_path, dem_on_grid, out_png_path):
    """The Depth Anything contribution per pixel, DSM - DEM (the detail
    layer the fusion added), as a 16-bit PNG for the 3D viewer's provenance
    view. Value 0 marks pixels with no DSM or no DEM; 1..65535 map linearly
    onto [detailMin, detailMax] metres. dem_on_grid must be the DEM exactly
    as the fusion resampled it onto the DSM's grid."""
    with rasterio.open(dsm_path) as ds:
        dsm = ds.read(1, masked=True).astype(np.float64).filled(np.nan)
    if dem_on_grid.shape != dsm.shape:
        raise ValueError(f"DEM grid {dem_on_grid.shape} doesn't match DSM {dsm.shape}")
    detail = dsm - dem_on_grid
    valid = np.isfinite(detail)
    if not valid.any():
        raise ValueError("no pixel has both a DSM and a DEM height")
    lo, hi = float(detail[valid].min()), float(detail[valid].max())
    scaled = np.zeros(detail.shape, np.uint16)
    scaled[valid] = np.clip(np.rint(1 + (detail[valid] - lo) / max(hi - lo, 1e-9) * 65534), 1, 65535)
    Image.fromarray(scaled, mode="I;16").save(out_png_path)
    return {"detailMin": lo, "detailMax": hi, "nodataValue": 0,
            "detailStd": float(detail[valid].std()), "noDataPixels": int((~valid).sum())}


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

    metadata = {"minElevation": min_elev, "maxElevation": max_elev, "units": "meters",
                **georeference_metadata(dsm_path)}
    meta_path = os.path.join(assets_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote metadata.json: {metadata}")
    return metadata


if __name__ == "__main__":
    generate_viewer_assets()
