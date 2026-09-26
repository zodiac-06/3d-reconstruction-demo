"""
Before/after evaluation: the current SRTM-only Theil-Sen calibration vs.
terrain + detail fusion (DSM = FABDEM + s * detail + offset), scored against
ICESat-2 ATL08 lidar -- independent of every DEM involved -- instead of
against SRTM itself.

Expects a run directory holding (see README.md, "Terrain + detail fusion"):
    aoi_cropped.tif             the optical crop the depth map was run on
    depth.npy                   Depth Anything V2 output for that crop
    srtm_dem.tif                04_fetch_srtm.fetch_srtm_for_aoi output
    terrain_fabdem.tif          04_fetch_srtm.fetch_terrain_dem_for_aoi(sources=("fabdem",))
    terrain_copernicus.tif      ... (sources=("copernicus",)) -- main.py's default base
    icesat2_atl08_truth.csv     fetch_icesat2_truth.py output

Writes the compared DSMs as GeoTIFFs plus fusion_eval.json into the same
directory, and prints the tables.

Run:
    python evaluate_fusion.py real_run_nainital_fusion
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.ndimage import map_coordinates
from scipy.stats import theilslopes

from srtm_calibration import (
    fuse_terrain_and_ndsm,
    relative_depth_detail_layer,
    relative_depth_to_dsm,
    resample_srtm_to_grid,
    terrain_plus_detail_dsm,
    write_geotiff,
)

N_SEEDS = 10  # the Theil-Sen fit samples points randomly; report its spread


def sample_at_points(array, transform, xs, ys):
    """Bilinear sample of a grid at map coordinates (NaN outside / on NaN)."""
    cols, rows = ~transform * (xs, ys)
    return map_coordinates(array, [rows - 0.5, cols - 0.5], order=1, mode="constant",
                           cval=np.nan)


def error_stats(pred, truth):
    ok = np.isfinite(pred) & np.isfinite(truth)
    e = pred[ok] - truth[ok]
    return {
        "n": int(ok.sum()),
        "bias": float(np.mean(e)),
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "mae": float(np.mean(np.abs(e))),
        "nmad": float(1.4826 * np.median(np.abs(e - np.median(e)))),
        "corr": float(np.corrcoef(pred[ok], truth[ok])[0, 1]),
    }


def surface_stats(dsm, pixel_m):
    gy, gx = np.gradient(dsm, pixel_m)
    slope_deg = np.degrees(np.arctan(np.hypot(gx, gy)))
    lap = np.abs(np.gradient(gx, pixel_m, axis=1) + np.gradient(gy, pixel_m, axis=0))
    return {
        "p2_p98_relief_m": float(np.nanpercentile(dsm, 98) - np.nanpercentile(dsm, 2)),
        "mean_slope_deg": float(np.nanmean(slope_deg)),
        "mean_abs_laplacian": float(np.nanmean(lap)),
    }


def fit_s_offset(detail, residual, max_points=3000, seed=0):
    """Theil-Sen fit residual ~ s * detail + offset (on a random subsample
    of at most max_points -- Theil-Sen is O(n^2) in memory)."""
    detail, residual = np.ravel(detail), np.ravel(residual)
    idx = np.flatnonzero(np.isfinite(detail) & np.isfinite(residual))
    if len(idx) > max_points:
        idx = np.random.default_rng(seed).choice(idx, max_points, replace=False)
    s, offset, _, _ = theilslopes(residual[idx], detail[idx])
    return float(s), float(offset)


def main(run_dir):
    p = lambda name: os.path.join(run_dir, name)
    depth = np.load(p("depth.npy")).astype(np.float64)
    with rasterio.open(p("aoi_cropped.tif")) as ref:
        transform, crs = ref.transform, ref.crs
    pixel_m = abs(transform.a)
    shape = depth.shape

    srtm = resample_srtm_to_grid(p("srtm_dem.tif"), transform, crs, shape)        # EGM96
    fabdem = resample_srtm_to_grid(p("terrain_fabdem.tif"), transform, crs, shape)  # EGM2008
    cop = resample_srtm_to_grid(p("terrain_copernicus.tif"), transform, crs, shape)  # EGM2008

    truth = pd.read_csv(p("icesat2_atl08_truth.csv"))
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        truth.lon.to_numpy(), truth.lat.to_numpy())
    at = lambda arr: sample_at_points(arr, transform, xs, ys)
    t = {d: {"terrain": truth[f"terrain_{d}"].to_numpy(),
             "canopy_top": truth[f"canopy_top_{d}"].to_numpy()}
         for d in ("egm96", "egm2008")}

    results = {"run_dir": run_dir, "n_truth_segments": len(truth),
               "n_truth_canopy_top": int(truth.canopy_ok.sum()), "dsms": {}}

    def score(name, dsm, datum, note, extra=None):
        results["dsms"][name] = {
            "datum": datum, "note": note,
            "vs_icesat2_terrain": error_stats(at(dsm), t[datum]["terrain"]),
            "vs_icesat2_canopy_top": error_stats(at(dsm), t[datum]["canopy_top"]),
            "surface": surface_stats(dsm, pixel_m),
            **(extra or {}),
        }

    # --- BEFORE: today's pipeline (main.py step 5): Theil-Sen rel -> SRTM ---
    before_runs = []
    for seed in range(N_SEEDS):
        dsm, slope, intercept, heldout = relative_depth_to_dsm(
            depth, transform, crs, p("srtm_dem.tif"), p(f"_tmp_before_{seed}.tif"),
            n_samples=2000, robust=True, rng=np.random.default_rng(seed))
        os.remove(p(f"_tmp_before_{seed}.tif"))
        before_runs.append((dsm, slope, intercept, heldout))
    before, slope0, intercept0, heldout0 = before_runs[0]
    write_geotiff(p("dsm_before_srtm_theilsen.tif"), before, transform, crs)
    per_seed = [error_stats(at(r[0]), t["egm96"]["canopy_top"])["rmse"] for r in before_runs]
    score("before: Theil-Sen(DA2 -> SRTM)", before, "egm96",
          "current main.py step 5, seed 0",
          {"slope": slope0, "intercept": intercept0,
           "legacy_heldout_vs_srtm": heldout0,
           "legacy_heldout_vs_srtm_rmse_over_seeds": [r[3]["rmse"] for r in before_runs],
           "canopy_top_rmse_over_seeds": per_seed})

    # --- Baselines: the DEMs alone, no Depth Anything at all -----------------
    score("SRTM alone", srtm, "egm96", "no DA2")
    score("Copernicus GLO-30 alone", cop, "egm2008", "no DA2; radar DSM")
    score("FABDEM alone", fabdem, "egm2008", "no DA2; bare earth")

    # --- AFTER: fusion via the library path, defaults s=1, offset=0 ---------
    fused, info = terrain_plus_detail_dsm(
        depth, transform, crs, p("terrain_fabdem.tif"), p("dsm_after_fabdem_fused.tif"),
        s=1.0, offset=0.0, detail_sigma_m=30.0, rng=np.random.default_rng(0))
    score("after: FABDEM + 1.0*detail", fused, "egm2008",
          "terrain_plus_detail_dsm defaults", {"fusion_info": info})

    # The main.py default: Copernicus GLO-30 base, same defaults and seed.
    fused_c, info_c = terrain_plus_detail_dsm(
        depth, transform, crs, p("terrain_copernicus.tif"), p("dsm_after_copernicus_fused.tif"),
        s=1.0, offset=0.0, detail_sigma_m=30.0, rng=np.random.default_rng(0))
    score("after: Copernicus + 1.0*detail (main.py default)", fused_c, "egm2008",
          "terrain_plus_detail_dsm defaults, seed 0", {"fusion_info": info_c})

    # Detail layer (meters) for the variants below -- same as inside
    # terrain_plus_detail_dsm: Theil-Sen slope vs. FABDEM, high-passed at 30m.
    slope_f = info["theil_sen_slope"]
    detail = relative_depth_detail_layer(slope_f * depth, 30.0 / pixel_m)

    # Variant: s, offset from the radar nDSM (Copernicus - FABDEM): no lidar
    # involved, so still a fair out-of-sample test.
    ndsm_radar = cop - fabdem
    s_r, off_r = fit_s_offset(detail, ndsm_radar)
    fused_r = fuse_terrain_and_ndsm(fabdem, detail, s=s_r, offset=off_r)
    write_geotiff(p("dsm_after_fabdem_fused_radar_scaled.tif"), fused_r, transform, crs)
    score("after: FABDEM + detail, s/offset fit to (COP - FABDEM)", fused_r, "egm2008",
          f"s={s_r:.3f}, offset={off_r:.2f} m", {"s": s_r, "offset": off_r})
    # Same offset, no DA2 detail: isolates what the detail term itself adds.
    score("FABDEM + same offset, s=0", fabdem + off_r, "egm2008",
          "control for the row above: no DA2")

    # --- Upper bound: s/offset tuned ON LIDAR, grouped CV by ICESat-2 pass --
    # If even lidar-tuned s can't beat the s=0 control, the DA2 detail layer
    # carries no usable height information at this image resolution.
    pass_id = truth.rgt.astype(str) + "_" + truth.cycle.astype(str)
    passes = np.array(sorted(pass_id.unique()))
    rng = np.random.default_rng(0)
    fold_of_pass = dict(zip(rng.permutation(passes), np.arange(len(passes)) % 5))
    fold = pass_id.map(fold_of_pass).to_numpy()
    d_at, f_at = at(detail), at(fabdem)
    for target in ("canopy_top", "terrain"):
        y = t["egm2008"][target]
        pred_s, pred_0, fitted = np.full(len(y), np.nan), np.full(len(y), np.nan), []
        for k in range(5):
            tr, te = fold != k, fold == k
            s_k, off_k = fit_s_offset(d_at[tr], (y - f_at)[tr])
            off0_k = float(np.nanmedian((y - f_at)[tr]))
            pred_s[te] = f_at[te] + s_k * d_at[te] + off_k
            pred_0[te] = f_at[te] + off0_k
            fitted.append((s_k, off_k))
        ok = np.isfinite(y) & np.isfinite(d_at) & np.isfinite(f_at)
        results[f"oracle_cv_{target}"] = {
            "with_detail": error_stats(pred_s, y),
            "offset_only_s0": error_stats(pred_0, y),
            "fold_s_offset": fitted,
            "corr_detail_vs_residual": float(np.corrcoef(d_at[ok], (y - f_at)[ok])[0, 1]),
        }

    # --- Scale sensitivity: does DA2 carry height signal at ANY scale? ------
    # corr(detail at high-pass scale sigma, lidar - FABDEM). "none" = the
    # unfiltered depth map.
    results["detail_scale_corr_vs_lidar_residual"] = {}
    for sigma_m in (30, 60, 150, 300, None):
        d = slope_f * depth if sigma_m is None else relative_depth_detail_layer(
            slope_f * depth, sigma_m / pixel_m)
        d_pts = at(d)
        row = {}
        for target in ("canopy_top", "terrain"):
            res = t["egm2008"][target] - f_at
            ok = np.isfinite(d_pts) & np.isfinite(res)
            row[target] = float(np.corrcoef(d_pts[ok], res[ok])[0, 1])
        results["detail_scale_corr_vs_lidar_residual"][str(sigma_m or "none")] = row

    # --- s sweep (diagnostic): sensitivity of the default fusion to s ------
    results["s_sweep_vs_canopy_top"] = {
        str(s): error_stats(at(fuse_terrain_and_ndsm(fabdem, detail, s=s, offset=off_r)),
                            t["egm2008"]["canopy_top"])["rmse"]
        for s in (-4, -2, -1, 0, 0.5, 1, 2, 4)
    }

    # --- Structural difference, before vs after ------------------------------
    both = np.isfinite(before) & np.isfinite(fused)
    diff = fused[both] - before[both]
    results["before_vs_after"] = {
        "pixel_corr": float(np.corrcoef(before[both], fused[both])[0, 1]),
        "rms_difference_m": float(np.sqrt(np.mean(diff ** 2))),
        "p5_p95_difference_m": [float(np.percentile(diff, 5)), float(np.percentile(diff, 95))],
        "corr_before_vs_srtm": float(np.corrcoef(before[both & np.isfinite(srtm)],
                                                 srtm[both & np.isfinite(srtm)])[0, 1]),
        "corr_after_vs_srtm": float(np.corrcoef(fused[both & np.isfinite(srtm)],
                                                srtm[both & np.isfinite(srtm)])[0, 1]),
    }

    with open(p("fusion_eval.json"), "w") as f:
        json.dump(results, f, indent=2)
    print_tables(results)


def print_tables(r):
    print(f"\nICESat-2 truth: {r['n_truth_segments']} ground segments, "
          f"{r['n_truth_canopy_top']} with canopy top\n")
    for target in ("canopy_top", "terrain"):
        print(f"== vs ICESat-2 {target} ==")
        print(f"{'DSM':58s} {'n':>5s} {'bias':>8s} {'RMSE':>8s} {'NMAD':>7s} {'corr':>7s}")
        for name, d in r["dsms"].items():
            e = d[f"vs_icesat2_{target}"]
            print(f"{name:58s} {e['n']:5d} {e['bias']:8.1f} {e['rmse']:8.1f} "
                  f"{e['nmad']:7.1f} {e['corr']:7.4f}")
        o = r[f"oracle_cv_{target}"]
        for label, key in (("oracle CV: FABDEM + lidar-tuned s*detail + offset", "with_detail"),
                           ("oracle CV: FABDEM + lidar-tuned offset only (s=0)", "offset_only_s0")):
            e = o[key]
            print(f"{label:58s} {e['n']:5d} {e['bias']:8.1f} {e['rmse']:8.1f} "
                  f"{e['nmad']:7.1f} {e['corr']:7.4f}")
        print(f"corr(detail, lidar - FABDEM) = {o['corr_detail_vs_residual']:.4f}\n")
    b = r["dsms"]["before: Theil-Sen(DA2 -> SRTM)"]
    print("before, legacy held-out vs SRTM (seed 0):", {k: round(v, 3) for k, v in
          b["legacy_heldout_vs_srtm"].items()})
    print("before, canopy-top RMSE over seeds:", [round(x, 1) for x in b["canopy_top_rmse_over_seeds"]])
    for name in ("after: FABDEM + 1.0*detail", "after: Copernicus + 1.0*detail (main.py default)"):
        print(f"fusion info [{name}]:", json.dumps(r["dsms"][name]["fusion_info"], indent=1))
    print("corr(detail, lidar - FABDEM) by high-pass scale (m):",
          json.dumps(r["detail_scale_corr_vs_lidar_residual"]))
    print("radar-fit s/offset:", {k: round(r["dsms"]["after: FABDEM + detail, s/offset fit to (COP - FABDEM)"][k], 3)
                                   for k in ("s", "offset")})
    print("oracle fold s/offset (canopy_top):", [(round(a, 2), round(b, 2)) for a, b in r["oracle_cv_canopy_top"]["fold_s_offset"]])
    print("s sweep (canopy-top RMSE):", {k: round(v, 2) for k, v in r["s_sweep_vs_canopy_top"].items()})
    print("before vs after:", json.dumps(r["before_vs_after"], indent=1))
    print("\nsurface stats:")
    for name, d in r["dsms"].items():
        print(f"  {name:58s} {d['surface']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
