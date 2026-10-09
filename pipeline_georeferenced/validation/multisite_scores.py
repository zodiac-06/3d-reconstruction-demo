"""
Score one site's job DSM against ICESat-2 ATL08, overall and per land-cover
class, and store the result in a small JSON (validation/multisite_results.json
by default). Same method for every site:

- the DSM is sampled bilinearly at each ICESat-2 segment centre
  (dsm_calibration/evaluate_fusion.py sample_at_points), segments outside the
  site box are dropped;
- heights compared in the DSM's own vertical datum (its VERTICAL_DATUM tag;
  the truth file has both EGM96 and EGM2008 columns);
- land cover = ESA WorldCover 2021 (10 m) at the segment centre, read from the
  public AWS COGs;
- error = DSM - ICESat-2 (negative = DSM too low), against canopy top
  (terrain + ATL08 h_canopy) and ground (ATL08 terrain).

Stores n, bias, std, RMSE, MAE and correlation per target, overall and per
class, plus SHA-256 hashes of the DSM and truth file -- no raw data.

    python validation/multisite_scores.py --site Nainital --bbox W,S,E,N \\
        --dsm <job DSM .tif> --truth <icesat2_atl08_truth.csv>

See validation/MULTISITE.md for how the inputs were made.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.windows import from_bounds

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "dsm_calibration"))
from evaluate_fusion import sample_at_points  # noqa: E402

RESULTS = HERE / "multisite_results.json"
WORLDCOVER = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
              "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
CLASSES = {10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built-up",
           60: "bare/sparse", 70: "snow/ice", 80: "water", 90: "wetland", 95: "mangroves", 100: "moss/lichen"}
TARGETS = {"canopy_top": "canopy_top", "ground": "terrain"}


def worldcover_tile(lat, lon):
    """WorldCover tiles are 3 x 3 degrees, named by their south-west corner."""
    la, lo = math.floor(lat / 3) * 3, math.floor(lon / 3) * 3
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def worldcover_at(lon, lat):
    tiles = {worldcover_tile(a, o) for a, o in zip(lat, lon)}
    if len(tiles) != 1:
        raise ValueError(f"site spans WorldCover tiles {sorted(tiles)}; not supported")
    url = "/vsicurl/" + WORLDCOVER.format(tile=tiles.pop())
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIPLEX="NO"), rasterio.open(url) as src:
        win = from_bounds(lon.min() - 0.002, lat.min() - 0.002, lon.max() + 0.002, lat.max() + 0.002,
                          transform=src.transform).round_offsets().round_lengths()
        grid, tr = src.read(1, window=win), src.window_transform(win)
    cols, rows = ~tr * (lon, lat)
    r = np.clip(np.floor(rows).astype(int), 0, grid.shape[0] - 1)
    c = np.clip(np.floor(cols).astype(int), 0, grid.shape[1] - 1)
    return np.array([CLASSES.get(int(v), f"class {int(v)}") for v in grid[r, c]])


def stats(pred, truth):
    ok = np.isfinite(pred) & np.isfinite(truth)
    e = pred[ok] - truth[ok]
    if not e.size:
        return {"n": 0}
    out = {"n": int(e.size), "bias": float(e.mean()), "std": float(e.std()),
           "rmse": float(np.sqrt(np.mean(e ** 2))), "mae": float(np.mean(np.abs(e))), "corr": None}
    if e.size >= 3 and np.std(pred[ok]) > 0 and np.std(truth[ok]) > 0:
        out["corr"] = float(np.corrcoef(pred[ok], truth[ok])[0, 1])
    return out


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def score_site(site, bbox, dsm_path, truth_path):
    w, s, e, n = bbox
    t = pd.read_csv(truth_path)
    t = t[(t.lon >= w) & (t.lon <= e) & (t.lat >= s) & (t.lat <= n)].reset_index(drop=True)
    with rasterio.open(dsm_path) as ds:
        dsm = ds.read(1, masked=True).astype(np.float64).filled(np.nan)
        tr, crs, tags = ds.transform, ds.crs, ds.tags()
    datum = tags.get("VERTICAL_DATUM", "EGM2008").lower()
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(t.lon.values, t.lat.values)
    pred = sample_at_points(dsm, tr, np.asarray(xs), np.asarray(ys))
    cls = worldcover_at(t.lon.values, t.lat.values)
    result = {"site": site, "bbox_wsen": list(bbox), "vertical_datum": datum.upper(),
              "dsm": {"file": Path(dsm_path).name, "sha256": sha256(dsm_path), "job_id": tags.get("JOB_ID"),
                      "dem_source": tags.get("DEM_SOURCE")},
              "truth": {"file": Path(truth_path).name, "sha256": sha256(truth_path), "segments_in_box": len(t),
                        "with_canopy_top": int(t.canopy_ok.sum()),
                        "cycles": [int(t.cycle.min()), int(t.cycle.max())] if len(t) else None,
                        "passes": int(t.groupby(["rgt", "cycle"]).ngroups) if len(t) else 0},
              "targets": {}}
    for name, prefix in TARGETS.items():
        truth = t[f"{prefix}_{datum}"].values
        per_class = {c: stats(pred[cls == c], truth[cls == c]) for c in sorted(set(cls))}
        result["targets"][name] = {"overall": stats(pred, truth),
                                   "per_class": {c: v for c, v in per_class.items() if v["n"]}}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north (EPSG:4326)")
    ap.add_argument("--dsm", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(","))
    res = score_site(args.site, bbox, args.dsm, args.truth)
    out = Path(args.out)
    allres = json.loads(out.read_text()) if out.exists() else {}
    allres[args.site] = res
    out.write_text(json.dumps(allres, indent=1) + "\n")
    for name, tgt in res["targets"].items():
        o = tgt["overall"]
        print(f"{args.site} {name}: n {o['n']}, bias {o['bias']:+.2f}, std {o['std']:.2f}, RMSE {o['rmse']:.2f}, "
              f"MAE {o['mae']:.2f}, corr {o['corr']:.4f}")


if __name__ == "__main__":
    main()
