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


def split_points(depth_samples, elev_samples, test_fraction=0.3, rng=None):
    """Split matched (depth, elevation) points into a fit set and a held-out
    test set. Fitting error on the same points used to fit is optimistic
    almost by construction (more so for a 2-parameter affine fit than it
    might look), so it's not a number worth reporting as accuracy -- only
    error on points the fit never saw means anything for that claim."""
    if rng is None:
        rng = np.random.default_rng()
    n = len(depth_samples)
    if n < 4:
        raise ValueError(f"Need at least 4 matched points to hold out a test split, got {n}")

    idx = rng.permutation(n)
    n_test = max(1, int(round(n * test_fraction)))
    n_test = min(n_test, n - 2)  # always leave >=2 points to fit on
    test_idx, fit_idx = idx[:n_test], idx[n_test:]

    return (
        depth_samples[fit_idx], elev_samples[fit_idx],
        depth_samples[test_idx], elev_samples[test_idx],
    )


def evaluate_fit(slope, intercept, depth_test, elev_test):
    """RMSE/MAE/correlation of the fitted model on held-out points."""
    predicted = slope * depth_test + intercept
    residual = predicted - elev_test
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    mae = float(np.mean(np.abs(residual)))
    if len(elev_test) >= 2 and np.std(depth_test) > 0 and np.std(elev_test) > 0:
        correlation = float(np.corrcoef(depth_test, elev_test)[0, 1])
    else:
        correlation = float("nan")
    return {"rmse": rmse, "mae": mae, "correlation": correlation, "n_test": len(depth_test)}


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
    test_fraction=0.3,
    rng=None,
):
    """End-to-end: resample SRTM onto the depth map's grid, sample matched
    (depth, elevation) points, hold out a test_fraction of them, fit the
    calibration on the rest, apply it across the whole depth map, and write
    the result as a georeferenced GeoTIFF.

    Returns (dsm_array, slope, intercept, metrics), where metrics is a dict
    with rmse/mae/correlation/n_test (from evaluate_fit on the held-out
    split, NOT the points used to fit) plus n_fit -- the number that
    actually means something for accuracy claims, since error on the
    training points themselves is optimistic.
    """
    if rng is None:
        rng = np.random.default_rng()

    srtm_on_grid = resample_srtm_to_grid(srtm_path, dst_transform, dst_crs, relative_depth.shape)
    depth_samples, elev_samples = sample_matched_points(
        relative_depth, srtm_on_grid, n_samples=n_samples, rng=rng
    )
    depth_fit, elev_fit, depth_test, elev_test = split_points(
        depth_samples, elev_samples, test_fraction=test_fraction, rng=rng
    )

    slope, intercept = fit_calibration(depth_fit, elev_fit, robust=robust)
    metrics = evaluate_fit(slope, intercept, depth_test, elev_test)
    metrics["n_fit"] = len(depth_fit)

    dsm = apply_calibration(relative_depth, slope, intercept)
    write_geotiff(out_path, dsm, dst_transform, dst_crs)
    return dsm, slope, intercept, metrics


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


# ---------------------------------------------------------------------------
# Terrain + detail fusion
#
# relative_depth_to_dsm() above calibrates relative depth straight onto SRTM,
# so every elevation in its output comes from DA2 and SRTM is both the
# calibration source and the only check -- circular. The functions below
# instead take the bare-earth terrain from a DEM (FABDEM by default; see
# qgis_prep/04_fetch_srtm.py's fetch_terrain_dem_for_aoi) and use relative
# depth only for what the ~30m DEM can't resolve:
#
#     DSM = DEM + s * detail + offset
#
# where `detail` is relative depth, scaled to meters by the same Theil-Sen
# fit as before, then high-pass filtered at the DEM's own resolution so it
# doesn't re-add the terrain the DEM already has. The Theil-Sen fit and its
# held-out metrics are still reported as a consistency check.
# ---------------------------------------------------------------------------

def fuse_terrain_and_ndsm(dem_on_grid, relative_depth, s=1.0, offset=0.0):
    """DSM = dem_on_grid + s * relative_depth + offset.

    dem_on_grid is the bare-earth terrain (meters) already resampled onto
    relative_depth's grid; relative_depth is the calibrated, zero-mean
    detail layer (see relative_depth_detail_layer), i.e. an nDSM-like
    height-above-terrain term. s scales that detail; offset is a constant
    (e.g. mean canopy height, or a vertical datum shift). NaNs in either
    input stay NaN in the output.
    """
    dem_on_grid = np.asarray(dem_on_grid, dtype=np.float64)
    relative_depth = np.asarray(relative_depth, dtype=np.float64)
    if dem_on_grid.shape != relative_depth.shape:
        raise ValueError(
            f"DEM grid {dem_on_grid.shape} and relative depth {relative_depth.shape} "
            f"must be on the same grid -- resample the DEM first"
        )
    return dem_on_grid + s * relative_depth + offset


def pixel_size_metres(transform, crs, shape):
    """(row, col) ground size of one pixel in metres. A geographic CRS
    (e.g. an EPSG:4326 upload) has pixel sizes in degrees, so convert with
    the WGS84 meridional / prime-vertical radii at the grid's centre
    latitude; a projected CRS is scaled by its linear unit (1 for metres)."""
    dy, dx = abs(transform.e), abs(transform.a)
    if crs.is_geographic:
        _, lat = transform * (shape[1] / 2, shape[0] / 2)
        phi = np.radians(lat)
        a, e2 = 6378137.0, 0.00669437999014
        w = 1 - e2 * np.sin(phi) ** 2
        m_per_deg_lat = np.radians(1) * a * (1 - e2) / w ** 1.5
        m_per_deg_lon = np.radians(1) * a * np.cos(phi) / np.sqrt(w)
        return float(dy * m_per_deg_lat), float(dx * m_per_deg_lon)
    factor = crs.linear_units_factor[1]
    return float(dy * factor), float(dx * factor)


def relative_depth_detail_layer(relative_depth, sigma_px):
    """High-pass relative_depth: subtract a Gaussian low-pass of sigma_px
    pixels (one number, or (rows, cols) for non-square pixels). Choose sigma_px ~ the terrain DEM's resolution in depth-map
    pixels, so what's left is structure finer than the DEM can represent.
    NaN-aware (normalized convolution), so voids don't bleed into their
    neighbourhood."""
    from scipy.ndimage import gaussian_filter

    relative_depth = np.asarray(relative_depth, dtype=np.float64)
    valid = np.isfinite(relative_depth)
    filled = np.where(valid, relative_depth, 0.0)
    weight = gaussian_filter(valid.astype(np.float64), sigma_px)
    with np.errstate(invalid="ignore", divide="ignore"):
        lowpass = gaussian_filter(filled, sigma_px) / weight
    return np.where(valid, relative_depth - lowpass, np.nan)


# Guards on the detail layer before it is added to the DEM. Measured on the
# validated jobs (Nainital, Bangalore, a 0.10 degree Nainital box): every
# |detail| > 5 m pixel sat within 100 m of the crop border, the worst at the
# top-right corner (-121 m at Nainital), while the 99th percentile was ~1 m.
DETAIL_BORDER_PX = 6     # linear fade to zero over the outer pixels of the crop
DETAIL_CLIP_M = 3.0      # then clip what's left to +-this many metres
MIN_ABS_SLOPE = 1e-6     # a Theil-Sen slope this flat means no depth signal: skip the layer


def guard_detail_layer(detail, slope, border_px=DETAIL_BORDER_PX, clip_m=DETAIL_CLIP_M,
                       min_abs_slope=MIN_ABS_SLOPE):
    """(guarded detail, summary). Skips the layer (zeros) when |slope| <
    min_abs_slope; otherwise fades it linearly to zero over the outer
    border_px pixels (weight = distance to the nearest edge / border_px, so 0
    on the edge) and clips the rest to +-clip_m. NaN stays NaN."""
    detail = np.asarray(detail, dtype=np.float64)
    finite = np.isfinite(detail)
    if not abs(slope) >= min_abs_slope:
        return np.where(finite, 0.0, np.nan), {"skipped": True, "reason": f"|slope| {abs(slope):.3g} < {min_abs_slope}",
                                               "border_px": border_px, "clip_m": clip_m, "n_clipped": 0}
    h, w = detail.shape
    rows, cols = np.ogrid[:h, :w]
    edge = np.minimum(np.minimum(rows, h - 1 - rows), np.minimum(cols, w - 1 - cols))
    tapered = detail * np.clip(edge / border_px, 0.0, 1.0)
    n_clipped = int(np.sum(finite & (np.abs(tapered) > clip_m)))
    return np.clip(tapered, -clip_m, clip_m), {"skipped": False, "border_px": border_px, "clip_m": clip_m,
                                               "n_clipped": n_clipped}


def terrain_plus_detail_dsm(
    relative_depth,
    dst_transform,
    dst_crs,
    dem_path,
    out_path,
    s=1.0,
    offset=0.0,
    detail_sigma_m=30.0,
    n_samples=2000,
    test_fraction=0.3,
    rng=None,
):
    """End-to-end fusion: resample the terrain DEM onto the depth map's
    grid, Theil-Sen fit relative depth -> DEM elevation (held-out metrics
    reported, exactly as relative_depth_to_dsm does) to put relative depth
    in meters, high-pass it at detail_sigma_m, and write
    DSM = DEM + s * detail + offset as a georeferenced GeoTIFF.

    Returns (dsm, info). info holds the Theil-Sen slope/intercept and
    held-out metrics (the consistency check), s, offset, the detail
    layer's spread, and the correlation between the fused DSM and the
    plain Theil-Sen DSM over the same pixels -- a large disagreement there
    is worth a look before trusting either.
    """
    if rng is None:
        rng = np.random.default_rng()

    dem_on_grid = resample_srtm_to_grid(dem_path, dst_transform, dst_crs, relative_depth.shape)
    depth_samples, elev_samples = sample_matched_points(
        relative_depth, dem_on_grid, n_samples=n_samples, rng=rng
    )
    depth_fit, elev_fit, depth_test, elev_test = split_points(
        depth_samples, elev_samples, test_fraction=test_fraction, rng=rng
    )
    slope, intercept = fit_calibration(depth_fit, elev_fit, robust=True)
    consistency = evaluate_fit(slope, intercept, depth_test, elev_test)
    consistency["n_fit"] = len(depth_fit)

    dy_m, dx_m = pixel_size_metres(dst_transform, dst_crs, relative_depth.shape)
    detail = relative_depth_detail_layer(slope * relative_depth, (detail_sigma_m / dy_m, detail_sigma_m / dx_m))
    detail, guard = guard_detail_layer(detail, slope)
    dsm = fuse_terrain_and_ndsm(dem_on_grid, detail, s=s, offset=offset)

    theil_sen_dsm = apply_calibration(relative_depth, slope, intercept)
    both = np.isfinite(dsm) & np.isfinite(theil_sen_dsm)
    info = {
        "theil_sen_slope": slope,
        "theil_sen_intercept": intercept,
        "theil_sen_heldout": consistency,
        "s": s,
        "offset": offset,
        "detail_sigma_m": detail_sigma_m,
        "detail_std_m": float(np.nanstd(detail)),
        "detail_guard": guard,
        "corr_fused_vs_theil_sen_dsm": float(np.corrcoef(dsm[both], theil_sen_dsm[both])[0, 1]),
    }

    write_geotiff(out_path, np.where(np.isfinite(dsm), dsm, -9999), dst_transform, dst_crs,
                  nodata=-9999)
    return dsm, info


def terrain_plus_detail_dsm_from_geotiff(
    depth_npy_path, reference_geotiff_path, dem_path, out_path, **kwargs
):
    """terrain_plus_detail_dsm with georeference pulled from the GeoTIFF the
    depth map was run on -- the fusion counterpart of
    relative_depth_to_dsm_from_geotiff."""
    depth = np.load(depth_npy_path)
    with rasterio.open(reference_geotiff_path) as ref:
        transform, crs = ref.transform, ref.crs
        ref_shape = (ref.height, ref.width)
    if ref_shape != depth.shape:
        raise ValueError(
            f"depth.npy shape {depth.shape} doesn't match reference "
            f"GeoTIFF shape {ref_shape} -- was the depth map run on this exact crop?"
        )
    return terrain_plus_detail_dsm(depth, transform, crs, dem_path, out_path, **kwargs)
