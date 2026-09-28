"""
Score each terrain DEM source on its own (no depth model, no fusion)
against ICESat-2 ATL08 lidar, with the same sampling and statistics as
evaluate_fusion.py: bilinear at segment centres, each DEM compared in its
own vertical datum (Copernicus/FABDEM EGM2008, NASADEM/SRTM EGM96), vs
canopy top and vs terrain.

Expects in the run directory: aoi_cropped.tif and icesat2_atl08_truth.csv
(fetch_icesat2_truth.py). Uses terrain_<source>.tif where present; with
--fetch, fetches the missing free-licence ones (copernicus, srtm, nasadem)
through 04_fetch_srtm.fetch_terrain_dem_for_aoi. FABDEM (non-commercial)
is fetched only with --fabdem.

Also reports which source main.py's default fallback chain would pick.

Writes dem_eval.json into the run directory.

Run:
    python evaluate_dem_sources.py real_run_nainital_fusion --fetch
"""
import argparse
import importlib.util
import json
import os
import sys

import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.warp import transform_bounds

from evaluate_fusion import error_stats, sample_at_points
from srtm_calibration import resample_srtm_to_grid

HERE = os.path.dirname(os.path.abspath(__file__))
QGIS_PREP_DIR = os.path.join(HERE, "..", "qgis_prep")
sys.path.insert(0, QGIS_PREP_DIR)  # 04_fetch_srtm.py imports aoi_config
_spec = importlib.util.spec_from_file_location(
    "fetch_srtm_mod", os.path.join(QGIS_PREP_DIR, "04_fetch_srtm.py"))
fetch_srtm_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_srtm_mod)

SOURCES = ("copernicus", "srtm", "nasadem", "fabdem")


def main(run_dir, fetch=False, fabdem=False):
    p = lambda name: os.path.join(run_dir, name)
    with rasterio.open(p("aoi_cropped.tif")) as ref:
        transform, crs, shape = ref.transform, ref.crs, ref.shape
        bbox = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)

    truth = pd.read_csv(p("icesat2_atl08_truth.csv"))
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        truth.lon.to_numpy(), truth.lat.to_numpy())

    results = {"run_dir": run_dir, "bbox_epsg4326": bbox, "n_truth_segments": len(truth),
               "n_truth_canopy_top": int(truth.canopy_ok.sum()),
               "default_chain": list(fetch_srtm_mod.terrain_dem_sources()), "dems": {}}
    for source in SOURCES:
        path = p(f"terrain_{source}.tif")
        if not os.path.exists(path) and fetch and (source != "fabdem" or fabdem):
            fetch_srtm_mod.fetch_terrain_dem_for_aoi(
                bbox=bbox, cropped_path=p("aoi_cropped.tif"), sources=(source,),
                dem_path=path, aligned_path=p(f"terrain_{source}_aligned.tif"))
        if not os.path.exists(path):
            print(f"(no {path}; skipping {source})")
            continue
        datum = fetch_srtm_mod.VERTICAL_DATUM[source].lower()
        dem = resample_srtm_to_grid(path, transform, crs, shape)
        at = sample_at_points(dem, transform, xs, ys)
        results["dems"][source] = {
            "datum": datum,
            "canopy_top": error_stats(at, truth[f"canopy_top_{datum}"].to_numpy()),
            "terrain": error_stats(at, truth[f"terrain_{datum}"].to_numpy()),
        }

    with open(p("dem_eval.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{run_dir}: {results['n_truth_segments']} ICESat-2 ground segments, "
          f"{results['n_truth_canopy_top']} with canopy top")
    print(f"default chain: {' -> '.join(results['default_chain'])}")
    for target in ("canopy_top", "terrain"):
        print(f"== vs ICESat-2 {target} ==")
        print(f"{'DEM':12s} {'datum':8s} {'n':>5s} {'bias':>7s} {'RMSE':>7s} {'MAE':>7s} {'NMAD':>6s} {'corr':>7s}")
        for source, d in results["dems"].items():
            e = d[target]
            print(f"{source:12s} {d['datum']:8s} {e['n']:5d} {e['bias']:7.2f} {e['rmse']:7.2f} "
                  f"{e['mae']:7.2f} {e['nmad']:6.2f} {e['corr']:7.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--fetch", action="store_true", help="fetch missing free-licence DEMs")
    ap.add_argument("--fabdem", action="store_true", help="also fetch FABDEM (CC BY-NC-SA 4.0)")
    args = ap.parse_args()
    main(args.run_dir, fetch=args.fetch, fabdem=args.fabdem)
