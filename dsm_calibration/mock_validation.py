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
    resample_srtm_to_grid,
    fit_calibration,
    sample_matched_points,
    split_points,
    evaluate_fit,
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
    dsm, slope, intercept, metrics = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_path, out_path,
        n_samples=N_SAMPLES, robust=False, rng=np.random.default_rng(1),
    )
    print(f"Matched points: {metrics['n_fit']} fit / {metrics['n_test']} held-out test")
    print(f"Recovered slope={slope:.3f} (true {TRUE_SLOPE}), intercept={intercept:.3f} (true {TRUE_INTERCEPT})")
    slope_err_pct = abs(slope - TRUE_SLOPE) / TRUE_SLOPE * 100
    intercept_err_m = abs(intercept - TRUE_INTERCEPT)
    dsm_rmse = rmse(dsm, elevation_true)
    print(f"Slope error: {slope_err_pct:.2f}% | Intercept error: {intercept_err_m:.2f}m")
    print(f"Full-grid DSM vs true elevation RMSE: {dsm_rmse:.3f}m (only computable here because "
          f"the mock knows true elevation everywhere -- real pipelines can't check this)")
    print(f"HELD-OUT test-set metrics (the number that means something for accuracy claims): "
          f"RMSE={metrics['rmse']:.3f}m, MAE={metrics['mae']:.3f}m, correlation={metrics['correlation']:.4f}")

    assert slope_err_pct < 5, f"Slope recovery off by {slope_err_pct:.2f}% -- fit is wrong"
    assert intercept_err_m < 5, f"Intercept recovery off by {intercept_err_m:.2f}m -- fit is wrong"
    assert dsm_rmse < 2 * SRTM_NOISE_SIGMA_M, f"DSM RMSE {dsm_rmse:.2f}m too high vs true elevation"
    assert metrics["rmse"] < 2 * SRTM_NOISE_SIGMA_M, f"held-out RMSE {metrics['rmse']:.2f}m too high"
    assert metrics["correlation"] > 0.9, f"held-out correlation {metrics['correlation']:.3f} too low"
    print("PASS: recovered calibration matches known ground truth within noise tolerance, "
          "and held-out metrics confirm it generalizes to points not used for fitting.")

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

    _, slope_plain, intercept_plain, metrics_plain = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_outlier_path,
        os.path.join(OUT_DIR, "dsm_outliers_plain.tif"),
        n_samples=N_SAMPLES, robust=False, rng=np.random.default_rng(1),
    )
    _, slope_robust, intercept_robust, metrics_robust = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_outlier_path,
        os.path.join(OUT_DIR, "dsm_outliers_robust.tif"),
        n_samples=N_SAMPLES, robust=True, rng=np.random.default_rng(1),
    )
    plain_slope_err = abs(slope_plain - TRUE_SLOPE) / TRUE_SLOPE * 100
    robust_slope_err = abs(slope_robust - TRUE_SLOPE) / TRUE_SLOPE * 100
    print(f"Plain fit with outliers:  slope={slope_plain:.3f} ({plain_slope_err:.2f}% off), "
          f"held-out RMSE={metrics_plain['rmse']:.2f}m")
    print(f"Robust fit with outliers: slope={slope_robust:.3f} ({robust_slope_err:.2f}% off), "
          f"held-out RMSE={metrics_robust['rmse']:.2f}m")

    assert robust_slope_err < plain_slope_err, (
        "robust=True should recover a better slope than robust=False when the "
        "matched points include gross SRTM outliers -- it didn't"
    )
    print(f"PASS: robust=True recovers a far better slope than robust=False under SRTM outlier "
          f"contamination ({robust_slope_err:.2f}% vs {plain_slope_err:.2f}% error).")
    print(f"NOTE: held-out RMSE is ~equally bad for both here ({metrics_robust['rmse']:.1f}m vs "
          f"{metrics_plain['rmse']:.1f}m) -- expected, not a bug. split_points draws the test set "
          f"from the SAME contaminated point pool as the fit set, so a handful of corrupted test "
          f"points dominate squared error regardless of fit quality. A better-recovered model "
          f"doesn't un-contaminate its own evaluation data. See Test 5 for a scenario where the "
          f"test set is clean and this comparison is actually meaningful.")

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

    dsm_from_wrapper, slope_w, intercept_w, metrics_w = relative_depth_to_dsm_from_geotiff(
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

    # --- Held-out validation split: unit checks + robust=True under clean and contaminated scenarios ---
    print("\n=== Test 5: held-out validation split ===")

    # 5a. split_points itself: sizes add up, no overlap, roughly matches test_fraction
    srtm_on_grid = resample_srtm_to_grid(srtm_path, fine_transform, CRS, relative_depth.shape)
    depth_all, elev_all = sample_matched_points(
        relative_depth, srtm_on_grid, n_samples=N_SAMPLES, rng=np.random.default_rng(7)
    )
    depth_fit, elev_fit, depth_test, elev_test = split_points(
        depth_all, elev_all, test_fraction=0.3, rng=np.random.default_rng(7)
    )
    assert len(depth_fit) + len(depth_test) == len(depth_all), "fit/test split lost or duplicated points"
    fit_set = set(zip(depth_fit.tolist(), elev_fit.tolist()))
    test_set = set(zip(depth_test.tolist(), elev_test.tolist()))
    assert not (fit_set & test_set), "fit and test sets overlap -- not actually held out"
    test_frac_actual = len(depth_test) / len(depth_all)
    assert 0.25 < test_frac_actual < 0.35, f"test_fraction=0.3 requested, got {test_frac_actual:.2f} actual"
    print(f"PASS: split_points gives {len(depth_fit)} fit / {len(depth_test)} test points "
          f"({test_frac_actual:.1%} held out), no overlap.")

    # 5b. evaluate_fit itself: known slope/intercept + hand-picked residuals -> exact expected RMSE/MAE
    known_depth = np.array([0.0, 1.0, 2.0, 3.0])
    known_slope, known_intercept = 2.0, 10.0
    fake_residuals = np.array([1.0, -1.0, 2.0, -2.0])  # predicted - true, chosen by hand
    fake_true_elev = (known_slope * known_depth + known_intercept) - fake_residuals
    m = evaluate_fit(known_slope, known_intercept, known_depth, fake_true_elev)
    expected_rmse = float(np.sqrt(np.mean(fake_residuals ** 2)))
    expected_mae = float(np.mean(np.abs(fake_residuals)))
    assert abs(m["rmse"] - expected_rmse) < 1e-9, f"evaluate_fit RMSE {m['rmse']} != expected {expected_rmse}"
    assert abs(m["mae"] - expected_mae) < 1e-9, f"evaluate_fit MAE {m['mae']} != expected {expected_mae}"
    print(f"PASS: evaluate_fit's RMSE/MAE match hand-computed expected values exactly "
          f"(RMSE={m['rmse']:.4f}, MAE={m['mae']:.4f}).")

    # 5c. The actual ask, done fairly: does robust=True's held-out metric stay good in
    # both a clean scenario and a contaminated one? Test 3 showed that contaminating the
    # SRTM raster taints split_points' test set exactly as much as its fit set, which makes
    # held-out RMSE uninformative there regardless of fit quality -- that's not a fit
    # problem, it's an evaluation-data problem. The scenario that actually isolates "does
    # robust regression protect held-out accuracy from bad training references" is: split
    # FIRST on clean data, corrupt only the fit side, keep the test side clean. That's also
    # the more realistic framing -- a trustworthy held-out check shouldn't come from the
    # same noisy source as the training references being validated.
    rng5c = np.random.default_rng(3)
    depth_fit5, elev_fit5, depth_test5, elev_test5 = split_points(
        depth_all, elev_all, test_fraction=0.3, rng=rng5c
    )
    n_corrupt = max(1, int(len(elev_fit5) * 0.15))
    corrupt_idx = rng5c.choice(len(elev_fit5), size=n_corrupt, replace=False)
    elev_fit5_contaminated = elev_fit5.copy()
    elev_fit5_contaminated[corrupt_idx] += rng5c.choice([-1, 1], size=n_corrupt) * rng5c.uniform(800, 1500, size=n_corrupt)
    print(f"Corrupted {n_corrupt} / {len(elev_fit5)} FIT-set points only; "
          f"{len(elev_test5)} TEST-set points stay clean.")

    slope_c_plain, intercept_c_plain = fit_calibration(depth_fit5, elev_fit5_contaminated, robust=False)
    slope_c_robust, intercept_c_robust = fit_calibration(depth_fit5, elev_fit5_contaminated, robust=True)
    metrics_c_plain = evaluate_fit(slope_c_plain, intercept_c_plain, depth_test5, elev_test5)
    metrics_c_robust = evaluate_fit(slope_c_robust, intercept_c_robust, depth_test5, elev_test5)
    _, _, _, metrics_clean_robust = relative_depth_to_dsm(
        relative_depth, fine_transform, CRS, srtm_path,
        os.path.join(OUT_DIR, "dsm_clean_robust.tif"),
        n_samples=N_SAMPLES, robust=True, rng=np.random.default_rng(1),
    )

    print(f"Clean scenario, robust=True:                held-out RMSE={metrics_clean_robust['rmse']:.3f}m, "
          f"correlation={metrics_clean_robust['correlation']:.4f}")
    print(f"Contaminated FIT / clean TEST, robust=False: held-out RMSE={metrics_c_plain['rmse']:.3f}m, "
          f"correlation={metrics_c_plain['correlation']:.4f}")
    print(f"Contaminated FIT / clean TEST, robust=True:  held-out RMSE={metrics_c_robust['rmse']:.3f}m, "
          f"correlation={metrics_c_robust['correlation']:.4f}")

    rmse_tolerance = 3 * SRTM_NOISE_SIGMA_M
    assert metrics_clean_robust["rmse"] < rmse_tolerance, (
        f"clean+robust held-out RMSE {metrics_clean_robust['rmse']:.2f}m exceeds {rmse_tolerance}m"
    )
    assert metrics_c_robust["rmse"] < rmse_tolerance, (
        f"contaminated-fit/clean-test robust held-out RMSE {metrics_c_robust['rmse']:.2f}m "
        f"exceeds {rmse_tolerance}m -- robust=True should protect held-out accuracy here"
    )
    assert metrics_c_robust["rmse"] < metrics_c_plain["rmse"], (
        "with a clean test set, robust=True's held-out RMSE should beat robust=False's "
        "when the fit set is contaminated -- it didn't"
    )
    assert metrics_clean_robust["correlation"] > 0.9 and metrics_c_robust["correlation"] > 0.9, (
        "held-out correlation should stay strong in both scenarios"
    )
    print(f"PASS: against a clean test set, robust=True's held-out RMSE stays under "
          f"{rmse_tolerance:.1f}m in both the clean scenario and when 15% of the FIT set is "
          f"contaminated ({metrics_c_robust['rmse']:.2f}m vs plain's {metrics_c_plain['rmse']:.2f}m) "
          f"-- the held-out metric genuinely reflects model quality here, not evaluation-data noise.")

    print("\n=== All mock validations passed ===")
    print(f"Mock artifacts left in {OUT_DIR} for inspection (gitignored).")


if __name__ == "__main__":
    main()
