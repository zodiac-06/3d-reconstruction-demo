"""
SRTM-based calibration: converts a relative depth map (e.g. Depth Anything
V2 run on a satellite/aerial image crop) into an absolute-elevation DSM,
using an SRTM tile as ground truth.

This is the geospatial equivalent of track_c_api/scale.py's tape-measured
reference-object calibration -- same idea (a real-world measurement fixes
the unknown scale of a relative model output), different math. scale.py
fits a single scalar from one reference length, because a photographed
object's shape is just relative depth scaled up. Elevation is different:
it isn't zero at the camera, so relative depth -> absolute elevation needs
a full affine fit (slope AND intercept) from many matched points, not a
one-point ratio.
"""
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from scipy.stats import theilslopes


def resample_srtm_to_grid(srtm_path, dst_transform, dst_crs, dst_shape):
    """Reproject/resample an SRTM tile (~30m resolution) onto the exact
    pixel grid of the depth map (likely much finer), so every depth pixel
    has a matching SRTM elevation value. Bilinear resampling since SRTM
    elevation is a continuous surface, not categorical data."""
    with rasterio.open(srtm_path) as src:
        srtm_on_grid = np.full(dst_shape, np.nan, dtype=np.float64)
        reproject(
            source=rasterio.band(src, 1),
            destination=srtm_on_grid,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=src.nodata,
            dst_nodata=np.nan,
        )
    return srtm_on_grid


def sample_matched_points(relative_depth, srtm_elevation, n_samples=2000, rng=None):
    """Sample matched (relative_depth, srtm_elevation) point pairs, spread
    across the image via a jittered grid of cells rather than clustered in
    one region or just the corners -- so the fit reflects the AOI's actual
    terrain variety instead of overfitting to one flat patch."""
    if rng is None:
        rng = np.random.default_rng()

    valid = np.isfinite(relative_depth) & np.isfinite(srtm_elevation)
    if not valid.any():
        raise ValueError("No valid overlapping pixels between depth map and SRTM grid")

    h, w = relative_depth.shape
    n_cells = max(1, int(np.sqrt(n_samples)))
    cell_h = max(1, h // n_cells)
    cell_w = max(1, w // n_cells)
    per_cell = max(1, n_samples // (n_cells * n_cells))

    picked_rows, picked_cols = [], []
    for cy in range(0, h, cell_h):
        for cx in range(0, w, cell_w):
            cell_valid = valid[cy:cy + cell_h, cx:cx + cell_w]
            cell_idx = np.argwhere(cell_valid)
            if len(cell_idx) == 0:
                continue
            k = min(per_cell, len(cell_idx))
            chosen = rng.choice(len(cell_idx), size=k, replace=False)
            for c in chosen:
                yy, xx = cell_idx[c]
                picked_rows.append(cy + yy)
                picked_cols.append(cx + xx)

    if not picked_rows:
        raise ValueError("Sampling produced zero matched points")

    picked_rows = np.array(picked_rows)
    picked_cols = np.array(picked_cols)
    depth_samples = relative_depth[picked_rows, picked_cols]
    elev_samples = srtm_elevation[picked_rows, picked_cols]
    return depth_samples, elev_samples


def fit_calibration(depth_samples, elevation_samples, robust=False):
    """Fit elevation = slope * depth + intercept from matched point pairs.
    Plain least-squares by default; Theil-Sen (median-of-slopes, robust to
    outliers -- e.g. clouds, water, or SRTM voids in the matched points)
    when robust=True."""
    if robust:
        slope, intercept, _, _ = theilslopes(elevation_samples, depth_samples)
    else:
        slope, intercept = np.polyfit(depth_samples, elevation_samples, 1)
    return float(slope), float(intercept)


def apply_calibration(relative_depth, slope, intercept):
    """Apply the fitted affine model across the entire depth map."""
    return slope * relative_depth + intercept


def write_geotiff(out_path, array, transform, crs, nodata=None):
    """Write a single-band float32 GeoTIFF, preserving the given georeference."""
    array = np.asarray(array, dtype=np.float32)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(array, 1)


def relative_depth_to_dsm(
    relative_depth,
    dst_transform,
    dst_crs,
    srtm_path,
    out_path,
    n_samples=2000,
    robust=False,
    rng=None,
):
    """End-to-end: resample SRTM onto the depth map's grid, sample matched
    (depth, elevation) points, fit a linear calibration, apply it across the
    whole depth map, and write the result as a georeferenced GeoTIFF.

    Returns (dsm_array, slope, intercept, n_points_used) so callers can log
    or sanity-check the fit, not just the output raster.
    """
    srtm_on_grid = resample_srtm_to_grid(srtm_path, dst_transform, dst_crs, relative_depth.shape)
    depth_samples, elev_samples = sample_matched_points(
        relative_depth, srtm_on_grid, n_samples=n_samples, rng=rng
    )
    slope, intercept = fit_calibration(depth_samples, elev_samples, robust=robust)
    dsm = apply_calibration(relative_depth, slope, intercept)
    write_geotiff(out_path, dsm, dst_transform, dst_crs)
    return dsm, slope, intercept, len(depth_samples)


def relative_depth_to_dsm_from_geotiff(
    depth_npy_path, reference_geotiff_path, srtm_path, out_path, **kwargs
):
    """Convenience wrapper for the real pipeline: pulls the geotransform and
    CRS from a reference GeoTIFF (the georeferenced satellite/aerial crop
    the depth map was run on) instead of requiring the caller to already
    have those values in hand."""
    depth = np.load(depth_npy_path)
    with rasterio.open(reference_geotiff_path) as ref:
        transform, crs = ref.transform, ref.crs
        ref_shape = (ref.height, ref.width)
        if ref_shape != depth.shape:
            raise ValueError(
                f"depth.npy shape {depth.shape} doesn't match reference "
                f"GeoTIFF shape {ref_shape} -- was the depth map run on this exact crop?"
            )
    return relative_depth_to_dsm(depth, transform, crs, srtm_path, out_path, **kwargs)
