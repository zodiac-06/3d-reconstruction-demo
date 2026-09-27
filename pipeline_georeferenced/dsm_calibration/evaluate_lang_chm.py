"""
Does real canopy height (Lang et al. global canopy height model, run on
Sentinel-2 by canopy_height/lang_chm.py) improve a DEM-based DSM?

    DSM = DEM + canopy term

scored against the same ICESat-2 ATL08 segments and with the same sampling
and statistics as evaluate_fusion.py (bilinear at segment centres,
EGM2008 heights, vs canopy top and vs terrain).

Canopy term = Lang-CHM height, set to 0 where
  - the CHM is NaN (Lang's own masks: empty, negative, SCL cloud/snow/water)
  - SCL is no-data/defective (0, 1), not-vegetated (5 -- SCL has no
    separate built-up class; 5 covers built-up and bare soil), water (6),
    cloud/cirrus (8, 9, 10) or snow (11).
There is no cloud-probability layer on Earth Search, so SCL is the only
cloud mask.

Bases: Copernicus GLO-30 (main.py's default base) and FABDEM. Note
Copernicus is a radar DSM -- it already contains part of the canopy --
while FABDEM is the bare-earth DEM, so FABDEM + CHM is the physically
consistent sum; both are scored.

Also reports, as in evaluate_fusion.py, a lidar-tuned upper bound
(DEM + s * CHM + offset, 5-fold CV grouped by ICESat-2 pass) against the
offset-only control, to separate "CHM is biased" from "CHM carries no
usable signal".

Expects in the run directory: aoi_cropped.tif, terrain_copernicus.tif,
terrain_fabdem.tif, icesat2_atl08_truth.csv, fusion_eval.json (Phase 6
results, for the DA-V2 baseline). The CHM/SCL must be on aoi_cropped.tif's
grid (06_fetch_s2_l2a_stack.py's default).

Run:
    python evaluate_lang_chm.py real_run_nainital_fusion \\
        ../qgis_prep/data/chm_lang.tif ../qgis_prep/data/s2_scl.tif
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from evaluate_fusion import error_stats, fit_s_offset, sample_at_points
from srtm_calibration import resample_srtm_to_grid, write_geotiff

CANOPY_ZERO_SCL = (0, 1, 5, 6, 8, 9, 10, 11)


def canopy_term(chm, scl):
    zero_scl = np.isin(scl, CANOPY_ZERO_SCL)
    term = np.where(np.isfinite(chm) & ~zero_scl, chm, 0.0)
    counts = {"chm_nan": int((~np.isfinite(chm)).sum()),
              **{f"scl_{c}": int((scl == c).sum()) for c in CANOPY_ZERO_SCL if (scl == c).any()},
              "zeroed_total": int((term == 0).sum()), "pixels": int(term.size)}
    return term, counts


def main(run_dir, chm_path, scl_path):
    p = lambda name: os.path.join(run_dir, name)
    with rasterio.open(p("aoi_cropped.tif")) as ref:
        transform, crs, shape = ref.transform, ref.crs, ref.shape
    with rasterio.open(chm_path) as src:
        assert src.transform == transform and src.shape == shape, "CHM not on aoi_cropped.tif's grid"
        chm = src.read(1).astype(np.float64)
    with rasterio.open(scl_path) as src:
        assert src.transform == transform and src.shape == shape, "SCL not on aoi_cropped.tif's grid"
        scl = src.read(1)

    cop = resample_srtm_to_grid(p("terrain_copernicus.tif"), transform, crs, shape)
    fabdem = resample_srtm_to_grid(p("terrain_fabdem.tif"), transform, crs, shape)
    canopy, mask_counts = canopy_term(chm, scl)

    truth = pd.read_csv(p("icesat2_atl08_truth.csv"))
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        truth.lon.to_numpy(), truth.lat.to_numpy())
    at = lambda arr: sample_at_points(arr, transform, xs, ys)
    t = {"terrain": truth.terrain_egm2008.to_numpy(), "canopy_top": truth.canopy_top_egm2008.to_numpy()}
    h_canopy = np.where(truth.canopy_ok, truth.h_canopy, np.nan)

    results = {"run_dir": run_dir, "chm": chm_path, "canopy_mask_counts": mask_counts,
               "chm_stats_m": {q: float(np.nanpercentile(chm, q)) for q in (0, 5, 25, 50, 75, 95, 100)},
               "chm_vs_icesat2_h_canopy": error_stats(at(canopy), h_canopy),
               "dsms": {}}

    def score(name, dsm, write_as=None):
        results["dsms"][name] = {tg: error_stats(at(dsm), t[tg]) for tg in t}
        if write_as:
            write_geotiff(p(write_as), dsm, transform, crs)

    score("Copernicus GLO-30 alone", cop)
    score("FABDEM alone", fabdem)
    score("Copernicus + Lang-CHM", cop + canopy, "dsm_copernicus_plus_lang_chm.tif")
    score("FABDEM + Lang-CHM", fabdem + canopy, "dsm_fabdem_plus_lang_chm.tif")

    # Lidar-tuned upper bound vs. offset-only control, grouped CV by pass
    # (same folds as evaluate_fusion.py).
    pass_id = truth.rgt.astype(str) + "_" + truth.cycle.astype(str)
    passes = np.array(sorted(pass_id.unique()))
    rng = np.random.default_rng(0)
    fold_of_pass = dict(zip(rng.permutation(passes), np.arange(len(passes)) % 5))
    fold = pass_id.map(fold_of_pass).to_numpy()
    c_at = at(canopy)
    for base_name, base in (("Copernicus", cop), ("FABDEM", fabdem)):
        b_at = at(base)
        for target in ("canopy_top", "terrain"):
            y = t[target]
            pred_s, pred_0, fitted = np.full(len(y), np.nan), np.full(len(y), np.nan), []
            for k in range(5):
                tr, te = fold != k, fold == k
                s_k, off_k = fit_s_offset(c_at[tr], (y - b_at)[tr])
                pred_s[te] = b_at[te] + s_k * c_at[te] + off_k
                pred_0[te] = b_at[te] + float(np.nanmedian((y - b_at)[tr]))
                fitted.append((s_k, off_k))
            ok = np.isfinite(y) & np.isfinite(b_at)
            results[f"oracle_cv_{base_name}_{target}"] = {
                "with_chm": error_stats(pred_s, y),
                "offset_only_s0": error_stats(pred_0, y),
                "fold_s_offset": fitted,
                "corr_chm_vs_residual": float(np.corrcoef(c_at[ok], (y - b_at)[ok])[0, 1]),
            }

    phase6 = json.load(open(p("fusion_eval.json")))["dsms"]
    da2 = phase6["before: Theil-Sen(DA2 -> SRTM)"]
    results["phase6_da_v2_baseline"] = {"canopy_top": da2["vs_icesat2_canopy_top"],
                                        "terrain": da2["vs_icesat2_terrain"]}

    with open(p("lang_chm_eval.json"), "w") as f:
        json.dump(results, f, indent=2)
    print_tables(results)


def print_tables(r):
    print(f"CHM (m) percentiles: { {k: round(v, 1) for k, v in r['chm_stats_m'].items()} }")
    print(f"canopy term zeroed: {r['canopy_mask_counts']}")
    e = r["chm_vs_icesat2_h_canopy"]
    print(f"canopy term vs ICESat-2 h_canopy: n={e['n']} bias={e['bias']:.2f} rmse={e['rmse']:.2f} "
          f"mae={e['mae']:.2f} corr={e['corr']:.3f}\n")
    rows = [("Phase 6: Theil-Sen(DA2 -> SRTM), EGM96", r["phase6_da_v2_baseline"])] + list(r["dsms"].items())
    for target in ("canopy_top", "terrain"):
        print(f"== vs ICESat-2 {target} ==")
        print(f"{'DSM':44s} {'n':>5s} {'bias':>7s} {'RMSE':>7s} {'MAE':>7s} {'NMAD':>6s} {'corr':>7s}")
        for name, d in rows:
            e = d[target]
            print(f"{name:44s} {e['n']:5d} {e['bias']:7.2f} {e['rmse']:7.2f} {e['mae']:7.2f} "
                  f"{e['nmad']:6.2f} {e['corr']:7.4f}")
        for base in ("Copernicus", "FABDEM"):
            o = r[f"oracle_cv_{base}_{target}"]
            for label, key in (("lidar-tuned s*CHM + offset", "with_chm"), ("lidar-tuned offset only", "offset_only_s0")):
                e = o[key]
                print(f"{'oracle CV: ' + base + ' + ' + label:44s} {e['n']:5d} {e['bias']:7.2f} {e['rmse']:7.2f} "
                      f"{e['mae']:7.2f} {e['nmad']:6.2f} {e['corr']:7.4f}")
            print(f"  corr(CHM, lidar - {base}) = {o['corr_chm_vs_residual']:.4f}; "
                  f"fold s = {[round(s, 3) for s, _ in o['fold_s_offset']]}")
        print()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
