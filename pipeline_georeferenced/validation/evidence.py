"""
Validation evidence for one job: the job's DSM sampled at every ICESat-2
ATL08 lidar segment over the AOI, per point and in aggregate (design doc,
section 4.5).

ICESat-2 is independent of every DEM source and of the Depth Anything fit
(which is calibrated against the DEM, not the lidar), so every point is
held out from the DSM.

Truth comes from an evaluation run on the job's exact pixel grid: a run
directory holding aoi_cropped.tif + icesat2_atl08_truth.csv, written by
dsm_calibration/fetch_icesat2_truth.py (the same lookup DEM Court uses for
its ICESat-2 overlay). The CSV carries heights in both EGM96 and EGM2008,
so the job's DSM is compared in its own DEM's datum with no geoid download.
"""
import os

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from evaluate_fusion import error_stats, sample_at_points

TRUTH_CSV = "icesat2_atl08_truth.csv"
DATUM_COLUMN = {"EGM2008": "egm2008", "EGM96": "egm96"}


def find_truth_run(grid_path, search_dirs):
    """First run dir whose aoi_cropped.tif has grid_path's CRS, transform
    and shape and that has an ICESat-2 truth CSV, else None."""
    with rasterio.open(grid_path) as g:
        crs, transform, shape = g.crs, g.transform, g.shape
    for d in search_dirs:
        ref, csv = os.path.join(d, "aoi_cropped.tif"), os.path.join(d, TRUTH_CSV)
        if not (os.path.exists(ref) and os.path.exists(csv)):
            continue
        with rasterio.open(ref) as r:
            if r.crs == crs and r.transform == transform and r.shape == shape:
                return d
    return None


def _stats(predicted, reference):
    """error_stats, or just the count when too few points overlap to score."""
    n = int((np.isfinite(predicted) & np.isfinite(reference)).sum())
    if n < 2:
        return {"n": n}
    # NaN (e.g. r of a constant series) isn't valid JSON: send null instead
    return {k: v if np.isfinite(v) else None for k, v in error_stats(predicted, reference).items()}


def build_evidence(dsm_path, vertical_datum, search_dirs):
    """Per-point and aggregate error of the DSM at dsm_path against
    ICESat-2 canopy top (what a DSM should match) and terrain.

    Returns None if no truth exists for this grid. Errors are DSM - lidar,
    in metres, in vertical_datum ("EGM2008" or "EGM96").
    """
    if vertical_datum not in DATUM_COLUMN:
        raise ValueError(f"Unknown vertical datum {vertical_datum!r}")
    run = find_truth_run(dsm_path, search_dirs)
    if run is None:
        return None

    with rasterio.open(dsm_path) as ds:
        dsm = ds.read(1, masked=True).astype(np.float64).filled(np.nan)
        transform, crs = ds.transform, ds.crs
    truth = pd.read_csv(os.path.join(run, TRUTH_CSV))
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        truth.lon.to_numpy(), truth.lat.to_numpy())
    predicted = sample_at_points(dsm, transform, np.asarray(xs), np.asarray(ys))

    d = DATUM_COLUMN[vertical_datum]
    ref = {"canopy_top": truth[f"canopy_top_{d}"].to_numpy(dtype=float),
           "terrain": truth[f"terrain_{d}"].to_numpy(dtype=float)}

    keep = np.isfinite(predicted)
    r2 = lambda v: None if not np.isfinite(v) else round(float(v), 2)
    points = [
        [round(float(lat), 6), round(float(lon), 6), r2(p), r2(ct), r2(p - ct), r2(tr), r2(p - tr),
         int(rgt), int(cyc)]
        for lat, lon, p, ct, tr, rgt, cyc in zip(
            truth.lat[keep], truth.lon[keep], predicted[keep], ref["canopy_top"][keep],
            ref["terrain"][keep], truth.rgt[keep], truth.cycle[keep])
    ]
    return {
        "source_run": os.path.basename(os.path.normpath(run)),
        "vertical_datum": vertical_datum,
        "n_segments": int(len(truth)),
        "n_on_dsm": int(keep.sum()),
        "stats": {k: _stats(predicted, v) for k, v in ref.items()},
        "fields": ["lat", "lon", "dsm_m", "canopy_top_m", "error_canopy_top_m",
                   "terrain_m", "error_terrain_m", "rgt", "cycle"],
        "points": points,
    }
