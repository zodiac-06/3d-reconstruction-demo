"""
Checks the depth detail layer's high-pass width is 30 m on the ground for
any input CRS (srtm_calibration.pixel_size_metres).

1. Pixel sizes vs. pyproj's geodesic distances (independent of the
   formula under test): the Nainital UTM grid, and 0.0001-degree EPSG:4326
   grids at the equator, Nainital's latitude and 60 N.
2. terrain_plus_detail_dsm on one synthetic scene gridded two ways, UTM
   (10 m pixels) and EPSG:4326 (~10 m pixels): the detail layer's spread
   must agree, and match the bumps planted in the depth map. The old code
   took the pixel size as transform.a -- degrees on EPSG:4326 -- which asks
   for a ~300,000-pixel Gaussian: SciPy's filter then effectively never
   finishes, so a degrees upload hung the job. The test reports that width
   rather than running it.
3. On the UTM grid the new code's DSM is bit-identical to the old code's.

    python dsm_calibration/test_pixel_size.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import srtm_calibration as sc  # noqa: E402

GEOD = Geod(ellps="WGS84")
UTM = CRS.from_epsg(32644)
LL = CRS.from_epsg(4326)
N = 300                      # pixels per side, ~3 km
LON0, LAT0 = 79.44, 29.40    # Nainital
BUMP_M, BUMP_WAVELENGTH_M = 4.0, 25.0


def terrain(x_m, y_m):
    """Large-scale relief the 30 m DEM resolves (km wavelengths, ~300 m)."""
    return 1500 + 150 * np.sin(x_m / 900) + 120 * np.cos(y_m / 700)


def bumps(x_m, y_m):
    """Fine structure only the depth map has (25 m wavelength)."""
    k = 2 * np.pi / BUMP_WAVELENGTH_M
    return BUMP_M * np.sin(k * x_m) * np.sin(k * y_m)


def local_xy(lon, lat):
    """Metres east/north of (LON0, LAT0), via pyproj (not the code under test)."""
    to = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    x0, y0 = to.transform(LON0, LAT0)
    x, y = to.transform(lon, lat)
    return np.asarray(x) - x0, np.asarray(y) - y0


def scene(transform, crs, tmp, tag):
    """Depth map on this grid + a 30 m DEM file covering it."""
    cols, rows = np.meshgrid(np.arange(N) + 0.5, np.arange(N) + 0.5)
    X, Y = transform * (cols, rows)
    if crs.is_geographic:
        x, y = local_xy(X, Y)
    else:
        x0, y0 = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(LON0, LAT0)
        x, y = X - x0, Y - y0
    depth = 0.8 * (terrain(x, y) + bumps(x, y)) + 40.0   # relative depth: affine in height

    # 30 m DEM in UTM covering the scene with margin: terrain only
    x0, y0 = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(LON0, LAT0)
    dem_t = from_origin(x0 - 600, y0 + 600, 30, 30)
    dn = int((N * 10 + 1200) / 30) + 2
    dc, dr = np.meshgrid(np.arange(dn) + 0.5, np.arange(dn) + 0.5)
    DX, DY = dem_t * (dc, dr)
    dem_path = tmp / f"dem_{tag}.tif"
    with rasterio.open(dem_path, "w", driver="GTiff", width=dn, height=dn, count=1, dtype="float32",
                       crs=UTM, transform=dem_t) as ds:
        ds.write(terrain(DX - x0, DY - y0).astype(np.float32), 1)
    return depth, str(dem_path), bumps(x, y)


def run(depth, transform, crs, dem_path, out, old=False):
    if old:  # the pre-fix behaviour: pixel size = transform.a, whatever the units
        real = sc.pixel_size_metres
        sc.pixel_size_metres = lambda t, c, s: (abs(t.a), abs(t.a))
    try:
        dsm, info = sc.terrain_plus_detail_dsm(depth, transform, crs, dem_path, out,
                                               rng=np.random.default_rng(0))
    finally:
        if old:
            sc.pixel_size_metres = real
    return dsm, info


def main():
    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    # 1. pixel sizes vs geodesic distance
    t = from_origin(346619.217, 3255557.753, 10, 10)
    check(sc.pixel_size_metres(t, UTM, (787, 884)) == (10.0, 10.0), "Nainital UTM grid: 10 x 10 m")
    for lat in (0.0, LAT0, 60.0):
        d = 0.0001
        t = from_origin(LON0, lat + N * d / 2, d, d)
        dy, dx = sc.pixel_size_metres(t, LL, (N, N))
        _, _, gx = GEOD.inv(LON0, lat, LON0 + d, lat)
        _, _, gy = GEOD.inv(LON0, lat - d / 2, LON0, lat + d / 2)
        ex, ey = abs(dx - gx) / gx, abs(dy - gy) / gy
        check(ex < 1e-3 and ey < 1e-3,
              f"EPSG:4326 at {lat:4.1f}N: {dx:.3f} x {dy:.3f} m vs geodesic {gx:.3f} x {gy:.3f} m")

    # 2 + 3. the detail layer on one scene, two grids
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        x0, y0 = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(LON0, LAT0)
        t_utm = from_origin(x0, y0 + N * 10, 10, 10)
        d_lat = 10 / 110_850.0                                   # ~10 m
        d_lon = 10 / (111_320.0 * np.cos(np.radians(LAT0)))      # ~10 m
        t_ll = rasterio.Affine(d_lon, 0, LON0, 0, -d_lat, LAT0 + N * d_lat)

        depth_u, dem_u, bump_u = scene(t_utm, UTM, tmp, "u")
        depth_g, dem_g, bump_g = scene(t_ll, LL, tmp, "g")
        dsm_u, info_u = run(depth_u, t_utm, UTM, dem_u, str(tmp / "u.tif"))
        dsm_g, info_g = run(depth_g, t_ll, LL, dem_g, str(tmp / "g.tif"))
        planted = float(np.std(bump_u))
        print(f"     planted bump std {planted:.2f} m; detail std: UTM {info_u['detail_std_m']:.2f} m, "
              f"EPSG:4326 {info_g['detail_std_m']:.2f} m")
        check(abs(info_g["detail_std_m"] - info_u["detail_std_m"]) < 0.1 * info_u["detail_std_m"],
              "EPSG:4326 detail layer matches the UTM one (within 10%)")
        check(abs(info_u["detail_std_m"] - planted) < 0.25 * planted,
              "detail layer carries the planted fine bumps (within 25%)")
        old_sigma = 30.0 / abs(t_ll.a)
        new_sigma = [30.0 / v for v in sc.pixel_size_metres(t_ll, LL, (N, N))]
        check(max(new_sigma) < 5, f"EPSG:4326 high-pass width now {new_sigma[0]:.2f} x {new_sigma[1]:.2f} px "
              f"(old code: {old_sigma:,.0f} px)")

        # 3. UTM output unchanged by the fix
        _, _ = run(depth_u, t_utm, UTM, dem_u, str(tmp / "u_old.tif"), old=True)
        with rasterio.open(tmp / "u.tif") as a, rasterio.open(tmp / "u_old.tif") as b:
            same = np.array_equal(a.read(1), b.read(1))
        check(same, "UTM grid: DSM bit-identical to the old code")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
