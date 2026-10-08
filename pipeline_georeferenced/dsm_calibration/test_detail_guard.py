"""
Checks srtm_calibration.terrain_plus_detail_dsm's guard on the depth detail
layer (guard_detail_layer), on synthetic scenes:

1. Border spike: a depth map that tracks the DEM, plus a spike planted in the
   top-right corner (where the real jobs' worst detail sat: -121 m at
   Nainital) and a small interior bump. The DSM must stay within +-3 m of the
   DEM everywhere, equal the DEM exactly on the crop's edge, fade in over the
   outer 6 pixels, and keep the interior bump unchanged.
2. Degenerate fit: the Theil-Sen slope forced to 1e-9 on a depth map with huge
   values, so slope * depth is not negligible. The layer must be skipped
   (DSM == DEM) and reported as skipped.

    python dsm_calibration/test_detail_guard.py
"""
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

import srtm_calibration as sc

H, W, PX = 120, 150, 10.0
TRANSFORM = Affine(PX, 0, 350000, 0, -PX, 3255000)
UTM44 = CRS.from_epsg(32644)


def write_dem(path, dem):
    with rasterio.open(path, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32",
                       crs="EPSG:32644", transform=TRANSFORM, nodata=-9999) as ds:
        ds.write(dem.astype(np.float32), 1)


def main():
    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    rows, cols = np.mgrid[0:H, 0:W].astype(np.float64)
    dem = 1500 + 2.0 * rows + 1.0 * cols                     # smooth terrain, 10 m pixels
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dem_path = tmp / "dem.tif"
        write_dem(dem_path, dem)
        dem32 = dem.astype(np.float32).astype(np.float64)    # what the DEM file holds

        # 1. border spike + interior bump
        depth = (dem - 1500) / 100.0                         # relative depth tracking the DEM
        depth[0:3, W - 3:W] += 1.5                           # corner spike: ~150 m after the fit
        depth[60, 75] += 0.01                                # interior bump: ~1 m after the fit
        dsm, info = sc.terrain_plus_detail_dsm(depth, TRANSFORM, UTM44, dem_path, tmp / "dsm1.tif",
                                               n_samples=2000, rng=np.random.default_rng(0))
        det = dsm - dem32
        guard = info.get("detail_guard") or {}
        check(abs(info["theil_sen_slope"] - 100.0) < 1.0, f"fit recovers the depth scale (slope {info['theil_sen_slope']:.2f})")
        check(np.nanmax(np.abs(det)) <= 3.0 + 1e-9, f"|DSM - DEM| <= 3 m everywhere (max {np.nanmax(np.abs(det)):.2f} m)")
        edge = np.concatenate([det[0], det[-1], det[:, 0], det[:, -1]])
        check(np.all(edge == 0), f"DSM == DEM on the crop edge (max |detail| there {np.max(np.abs(edge)):.3g} m)")
        raw = sc.relative_depth_detail_layer(info["theil_sen_slope"] * depth, 30.0 / PX)
        k = 3  # 3 px in from the edge: weight 3/6
        check(abs(det[k, 75] - 0.5 * raw[k, 75]) < 1e-9, f"detail fades in over the outer 6 px (row {k}: {det[k, 75]:.4f} = 0.5 x {raw[k, 75]:.4f})")
        check(abs(det[60, 75] - raw[60, 75]) < 1e-9 and abs(det[60, 75]) > 0.5,
              f"interior bump kept unchanged ({det[60, 75]:.3f} m)")
        check(guard.get("skipped") is False and guard.get("border_px") == 6 and guard.get("clip_m") == 3.0,
              f"guard reported: {guard}")

        # 2. degenerate fit: slope 1e-9 on a depth map with huge values
        rng = np.random.default_rng(1)
        depth2 = rng.normal(0, 1e10, (H, W))
        real_fit = sc.fit_calibration
        sc.fit_calibration = lambda *a, **kw: (1e-9, 1500.0)
        try:
            dsm2, info2 = sc.terrain_plus_detail_dsm(depth2, TRANSFORM, UTM44, dem_path, tmp / "dsm2.tif",
                                                     n_samples=500, rng=np.random.default_rng(0))
        finally:
            sc.fit_calibration = real_fit
        det2 = dsm2 - dem32
        unguarded = sc.relative_depth_detail_layer(1e-9 * depth2, 30.0 / PX)
        check(np.nanstd(unguarded) > 1.0, f"without the guard this slope would add {np.nanstd(unguarded):.1f} m std of noise")
        check(np.all(det2 == 0), f"|slope| < 1e-6: layer skipped, DSM == DEM (max |detail| {np.max(np.abs(det2)):.3g} m)")
        check((info2.get("detail_guard") or {}).get("skipped") is True, f"skip reported: {info2.get('detail_guard')}")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
