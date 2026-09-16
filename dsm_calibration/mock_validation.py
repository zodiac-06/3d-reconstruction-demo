"""
Mock-first validation for srtm_calibration.py -- same pattern as this
project's original mock-first approach (Track A's Day-1 fixture, Track B/C's
standalone tests before wiring into the API): verify the math is correct
against synthetic data with a KNOWN ground truth before Person 1's real
depth.npy / GeoTIFF crop / SRTM tile exist.

Builds:
  - A smooth synthetic "true" elevation surface (a hill + terrain noise).
  - A synthetic "relative depth" map: a KNOWN affine transform of that true
    elevation, plus independent noise -- simulating a monocular depth
    model's arbitrarily-scaled, imperfect output. The calibration code
    never sees the true affine parameters; it has to recover them from the
    matched points, same as it will have to for real Depth-Anything-V2
    output.
  - A synthetic "SRTM-like" tile: the SAME true elevation evaluated on a
    coarser grid (mimicking ~30m SRTM vs a finer depth grid) with its own
    independent measurement noise, written out as a real GeoTIFF -- so the
    reprojection step is exercised against an actual file read, not just
    an in-memory array.

Then runs the full pipeline and checks the recovered DSM against the known
true elevation (not just "did it run without crashing").
"""
import os
import shutil
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, os.path.dirname(__file__))
from srtm_calibration import (
    relative_depth_to_dsm,
    relative_depth_to_dsm_from_geotiff,
    fit_calibration,
    sample_matched_points,
)

OUT_DIR = os.path.join(os.path.dirname(__file__), "mock_data")
CRS = "EPSG:32633"  # arbitrary real UTM zone; math doesn't care which one

# Geometry: fine (depth) grid vs coarse (SRTM-like) grid over the same AOI
FINE_SHAPE = (300, 300)
FINE_PIXEL_M = 2.0
COARSE_FACTOR = 15  # -> 30m/px coarse grid, matching real SRTM's ~30m vs a fine crop
ORIGIN_X, ORIGIN_Y = 500000.0, 5000000.0  # arbitrary UTM-ish origin (meters)

# Known ground-truth affine relationship: elevation = TRUE_SLOPE * depth + TRUE_INTERCEPT.
# The calibration code must recover values close to these from matched points alone.
TRUE_SLOPE = 42.0
TRUE_INTERCEPT = 118.0

DEPTH_NOISE_SIGMA = 0.05     # independent noise in the "relative depth" model output
SRTM_NOISE_SIGMA_M = 1.5     # independent SRTM measurement noise, in meters
N_SAMPLES = 2000
RNG = np.random.default_rng(42)


def true_elevation(x, y):
    """Smooth hill (Gaussian bump) + a bit of rolling terrain, in meters."""
    hill = 60.0 * np.exp(-(((x - 300) ** 2 + (y - 350) ** 2)) / (2 * 120.0 ** 2))
    rolling = 8.0 * np.sin(x / 90.0) * np.cos(y / 70.0)
    base = 120.0
    return base + hill + rolling


def pixel_centers(shape, pixel_m):
    h, w = shape
    xs = (np.arange(w) + 0.5) * pixel_m
    ys = (np.arange(h) + 0.5) * pixel_m
    xx, yy = np.meshgrid(xs, ys)
    return xx, yy


def build_fine_depth_and_truth():
    xx, yy = pixel_centers(FINE_SHAPE, FINE_PIXEL_M)
    elevation_true = true_elevation(xx, yy)

    depth_noise = RNG.normal(0, DEPTH_NOISE_SIGMA, size=FINE_SHAPE)
    relative_depth = (elevation_true - TRUE_INTERCEPT) / TRUE_SLOPE + depth_noise

    fine_transform = from_origin(ORIGIN_X, ORIGIN_Y + FINE_SHAPE[0] * FINE_PIXEL_M, FINE_PIXEL_M, FINE_PIXEL_M)
    return relative_depth.astype(np.float32), elevation_true, fine_transform


def build_coarse_srtm_geotiff(out_path):
    coarse_shape = (FINE_SHAPE[0] // COARSE_FACTOR, FINE_SHAPE[1] // COARSE_FACTOR)
    coarse_pixel_m = FINE_PIXEL_M * COARSE_FACTOR
    xx, yy = pixel_centers(coarse_shape, coarse_pixel_m)
    srtm_elevation = true_elevation(xx, yy) + RNG.normal(0, SRTM_NOISE_SIGMA_M, size=coarse_shape)

    coarse_transform = from_origin(
        ORIGIN_X, ORIGIN_Y + FINE_SHAPE[0] * FINE_PIXEL_M, coarse_pixel_m, coarse_pixel_m
    )
    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=coarse_shape[0], width=coarse_shape[1], count=1,
        dtype=np.float32, crs=CRS, transform=coarse_transform,
    ) as dst:
        dst.write(srtm_elevation.astype(np.float32), 1)
    return coarse_shape, coarse_pixel_m


def inject_srtm_outliers(srtm_path, fraction=0.15):
    """Corrupt a chunk of SRTM pixels with gross spurious values, simulating
    voids/artifacts, to actually exercise (and prove the value of) robust=True.
    Bilinear resampling onto the fine grid smears/dilutes each corrupted
    pixel's effect over its neighbors, so the contamination needs real
    weight (not just one or two pixels) to still show up after reprojection."""
    with rasterio.open(srtm_path, "r+") as ds:
        arr = ds.read(1)
        h, w = arr.shape
        n_outliers = max(1, int(h * w * fraction))
        flat_idx = RNG.choice(h * w, size=n_outliers, replace=False)
        ys, xs = np.unravel_index(flat_idx, (h, w))
        arr[ys, xs] += RNG.choice([-1, 1], size=n_outliers) * RNG.uniform(800, 1500, size=n_outliers)
        ds.write(arr, 1)
    return n_outliers


def rmse(a, b):
    return float(np.sqrt(np.nanmean((a - b) ** 2)))


def main():
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== Building synthetic mock data ===")
    relative_depth, elevation_true, fine_transform = build_fine_depth_and_truth()
    srtm_path = os.path.join(OUT_DIR, "mock_srtm.tif")
    coarse_shape, coarse_pixel_m = build_coarse_srtm_geotiff(srtm_path)
    print(f"Fine depth grid: {FINE_SHAPE} at {FINE_PIXEL_M}m/px")
    print(f"Mock SRTM grid: {coarse_shape} at {coarse_pixel_m}m/px (written to {srtm_path})")
    print(f"True elevation range: {elevation_true.min():.1f}m - {elevation_true.max():.1f}m")
    print(f"Known ground-truth relationship: elevation = {TRUE_SLOPE} * depth + {TRUE_INTERCEPT}")

    # --- Clean-data test: plain least-squares fit ---
    print("\n=== Test 1: clean data, plain least-squares fit ===")
    out_path = os.path.join(OUT_DIR, "dsm_clean.tif")
    dsm, slope, intercept, n_used = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_path, out_path,
        n_samples=N_SAMPLES, robust=False, rng=np.random.default_rng(1),
    )
    print(f"Matched points used: {n_used}")
    print(f"Recovered slope={slope:.3f} (true {TRUE_SLOPE}), intercept={intercept:.3f} (true {TRUE_INTERCEPT})")
    slope_err_pct = abs(slope - TRUE_SLOPE) / TRUE_SLOPE * 100
    intercept_err_m = abs(intercept - TRUE_INTERCEPT)
    dsm_rmse = rmse(dsm, elevation_true)
    print(f"Slope error: {slope_err_pct:.2f}% | Intercept error: {intercept_err_m:.2f}m")
    print(f"DSM vs true elevation RMSE: {dsm_rmse:.3f}m (SRTM noise sigma was {SRTM_NOISE_SIGMA_M}m)")

    assert slope_err_pct < 5, f"Slope recovery off by {slope_err_pct:.2f}% -- fit is wrong"
    assert intercept_err_m < 5, f"Intercept recovery off by {intercept_err_m:.2f}m -- fit is wrong"
    assert dsm_rmse < 2 * SRTM_NOISE_SIGMA_M, f"DSM RMSE {dsm_rmse:.2f}m too high vs true elevation"
    print("PASS: recovered calibration matches known ground truth within noise tolerance.")

    # --- GeoTIFF round-trip check ---
    print("\n=== Test 2: GeoTIFF round-trip integrity ===")
    with rasterio.open(out_path) as f:
        assert f.shape == FINE_SHAPE, f"shape mismatch: {f.shape} != {FINE_SHAPE}"
        assert str(f.crs) == str(rasterio.crs.CRS.from_string(CRS)), f"CRS mismatch: {f.crs}"
        assert f.transform == fine_transform, "geotransform mismatch"
        written = f.read(1)
        assert np.allclose(written, dsm, atol=1e-3), "written GeoTIFF values don't match in-memory DSM"
    print(f"PASS: {out_path} preserves shape/CRS/transform, values match what was written.")

    # --- Outlier-contaminated test: robust vs non-robust ---
    print("\n=== Test 3: SRTM outliers injected -- robust=True vs robust=False ===")
    srtm_outlier_path = os.path.join(OUT_DIR, "mock_srtm_outliers.tif")
    shutil.copy(srtm_path, srtm_outlier_path)
    n_outliers = inject_srtm_outliers(srtm_outlier_path, fraction=0.15)
    print(f"Corrupted {n_outliers} / {coarse_shape[0] * coarse_shape[1]} coarse SRTM pixels")

    _, slope_plain, intercept_plain, _ = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_outlier_path,
        os.path.join(OUT_DIR, "dsm_outliers_plain.tif"),
        n_samples=N_SAMPLES, robust=False, rng=np.random.default_rng(1),
    )
    _, slope_robust, intercept_robust, _ = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_outlier_path,
        os.path.join(OUT_DIR, "dsm_outliers_robust.tif"),
        n_samples=N_SAMPLES, robust=True, rng=np.random.default_rng(1),
    )
    plain_slope_err = abs(slope_plain - TRUE_SLOPE) / TRUE_SLOPE * 100
    robust_slope_err = abs(slope_robust - TRUE_SLOPE) / TRUE_SLOPE * 100
    print(f"Plain fit with outliers:  slope={slope_plain:.3f} ({plain_slope_err:.2f}% off)")
    print(f"Robust fit with outliers: slope={slope_robust:.3f} ({robust_slope_err:.2f}% off)")

    assert robust_slope_err < plain_slope_err, (
        "robust=True should recover a better slope than robust=False when the "
        "matched points include gross SRTM outliers -- it didn't"
    )
    print(f"PASS: robust=True degrades less than robust=False under SRTM outlier contamination "
          f"({robust_slope_err:.2f}% vs {plain_slope_err:.2f}% slope error).")

    # --- The real-world entry point: pulling transform/CRS from a reference GeoTIFF ---
    print("\n=== Test 4: relative_depth_to_dsm_from_geotiff (the real-world entry point) ===")
    depth_npy_path = os.path.join(OUT_DIR, "mock_depth.npy")
    np.save(depth_npy_path, relative_depth)

    reference_geotiff_path = os.path.join(OUT_DIR, "mock_reference_crop.tif")
    with rasterio.open(
        reference_geotiff_path, "w", driver="GTiff",
        height=FINE_SHAPE[0], width=FINE_SHAPE[1], count=3,
        dtype=np.uint8, crs=CRS, transform=fine_transform,
    ) as dst:
        # 3-band dummy RGB, standing in for the actual satellite/aerial crop
        # Depth Anything V2 was run on -- only its georeference matters here.
        dst.write(np.zeros((3, *FINE_SHAPE), dtype=np.uint8))

    dsm_from_wrapper, slope_w, intercept_w, _ = relative_depth_to_dsm_from_geotiff(
        depth_npy_path, reference_geotiff_path, srtm_path,
        os.path.join(OUT_DIR, "dsm_from_wrapper.tif"),
        n_samples=N_SAMPLES, robust=False, rng=np.random.default_rng(1),
    )
    assert slope_w == slope and intercept_w == intercept, (
        "wrapper produced a different fit than the direct call with the same inputs"
    )
    print(f"PASS: wrapper recovers slope={slope_w:.3f}, intercept={intercept_w:.3f} "
          f"-- identical to the direct-call result, correctly pulling transform/CRS from the GeoTIFF.")

    # Shape-mismatch guard: depth.npy that doesn't match the reference crop should raise, not silently misalign
    bad_depth_path = os.path.join(OUT_DIR, "mock_depth_wrong_shape.npy")
    np.save(bad_depth_path, relative_depth[:100, :100])
    try:
        relative_depth_to_dsm_from_geotiff(
            bad_depth_path, reference_geotiff_path, srtm_path,
            os.path.join(OUT_DIR, "dsm_should_not_exist.tif"),
        )
        raise AssertionError("expected a shape-mismatch ValueError, got none")
    except ValueError as e:
        print(f"PASS: shape mismatch correctly rejected ({e})")

    print("\n=== All mock validations passed ===")
    print(f"Mock artifacts left in {OUT_DIR} for inspection (gitignored).")


if __name__ == "__main__":
    main()
