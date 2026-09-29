"""
Checks validation/evidence.py and GET /jobs/{id}/validation against values
computed independently here.

The DSM is a plane, z = A + B*x + C*y in UTM metres, on the committed
Nainital grid (qgis_prep/data/geo_metadata.json), so the correct DSM height
at any lidar point is the plane evaluated at that point: no raster sampling
involved, unlike the code under test (bilinear interpolation, which is
exact on a plane). Lidar heights are the plane plus known offsets, so the
expected per-point errors and RMSE/MAE/bias are known in closed form.

Checks the EGM2008/EGM96 column choice, the "no truth for this grid" case,
points outside the DSM, and the API endpoint.

    pip install numpy pandas rasterio pyproj fastapi httpx python-multipart
    python validation/test_evidence.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dsm_calibration"))
sys.path.insert(0, str(ROOT / "qgis_prep"))

A, B, C = 1500.0, 0.02, -0.03
DATUM_SHIFT = 3.0  # pretend EGM96 heights sit 3 m above EGM2008 here


def plane(x, y, meta):
    t = meta["transform"]
    return A + B * (x - t[2]) + C * (t[5] - y)


def write_grid(run_dir, meta, with_dsm=True):
    t = meta["transform"]
    transform = Affine(t[0], t[1], t[2], t[3], t[4], t[5])
    w, h = meta["width"], meta["height"]
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    xs, ys = transform * (cols, rows)
    profile = dict(driver="GTiff", width=w, height=h, count=1, crs=meta["crs"], transform=transform)
    run_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(run_dir / "aoi_cropped.tif", "w", dtype="uint8", **profile) as ds:
        ds.write(np.zeros((h, w), np.uint8), 1)
    if with_dsm:
        with rasterio.open(run_dir / "output_dsm.tif", "w", dtype="float32", nodata=-9999, **profile) as ds:
            ds.write(plane(xs, ys, meta).astype(np.float32), 1)


def write_truth(run_dir, meta, rng):
    """Returns (truth DataFrame, expected per-point DSM height, inside mask)."""
    b = meta["bounds_native_crs"]
    n = 400
    x = rng.uniform(b["left"] + 30, b["right"] - 30, n)
    y = rng.uniform(b["bottom"] + 30, b["top"] - 30, n)
    x[:5] = b["right"] + 500   # 5 points off the DSM: must be dropped
    to_ll = Transformer.from_crs(meta["crs"], "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(x, y)
    expected = plane(x, y, meta)
    canopy_off = rng.normal(-8, 12, n)       # DSM - canopy top, EGM2008
    terrain_off = rng.normal(6, 9, n)        # DSM - terrain, EGM2008
    canopy_ok = rng.random(n) > 0.1          # 10% of segments have no canopy height
    df = pd.DataFrame({
        "lon": lon, "lat": lat, "rgt": rng.integers(1, 1387, n), "cycle": rng.integers(1, 20, n),
        "canopy_ok": canopy_ok,
        "terrain_egm2008": expected - terrain_off,
        "canopy_top_egm2008": np.where(canopy_ok, expected - canopy_off, np.nan),
    })
    df["terrain_egm96"] = df.terrain_egm2008 + DATUM_SHIFT
    df["canopy_top_egm96"] = df.canopy_top_egm2008 + DATUM_SHIFT
    df.to_csv(run_dir / "icesat2_atl08_truth.csv", index=False)
    inside = np.arange(n) >= 5
    return df, expected, inside


def expected_stats(err):
    err = err[np.isfinite(err)]
    return {"n": len(err), "rmse": float(np.sqrt(np.mean(err ** 2))),
            "mae": float(np.mean(np.abs(err))), "bias": float(np.mean(err))}


def main():
    meta = json.loads((ROOT / "qgis_prep" / "data" / "geo_metadata.json").read_text())
    rng = np.random.default_rng(7)
    failures = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    import importlib.util
    spec = importlib.util.spec_from_file_location("evidence", ROOT / "validation" / "evidence.py")
    evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        run = tmp / "cal" / "real_run_synthetic"
        write_grid(run, meta)
        truth, expected, inside = write_truth(run, meta, rng)
        dsm = run / "output_dsm.tif"

        for datum, col, shift in (("EGM2008", "egm2008", 0.0), ("EGM96", "egm96", DATUM_SHIFT)):
            ev = evidence.build_evidence(str(dsm), datum, [str(tmp / "cal" / "other"), str(run)])
            check(ev is not None and ev["source_run"] == "real_run_synthetic", f"{datum}: truth run found")
            F = {f: i for i, f in enumerate(ev["fields"])}
            pts = np.array([[np.nan if v is None else v for v in p] for p in ev["points"]], dtype=float)
            check(ev["n_on_dsm"] == int(inside.sum()) and len(pts) == inside.sum(),
                  f"{datum}: {ev['n_on_dsm']} of {len(truth)} points on the DSM (5 placed off it are dropped)")
            dsm_err = np.max(np.abs(pts[:, F["dsm_m"]] - expected[inside]))
            check(dsm_err < 0.01, f"{datum}: sampled DSM matches the analytic plane (max |diff| {dsm_err:.2e} m)")
            for target in ("canopy_top", "terrain"):
                exp_err = expected[inside] - truth[f"{target}_{col}"].to_numpy()[inside]
                got_err = pts[:, F[f"error_{target}_m"]]
                both = np.isfinite(exp_err)
                check(np.array_equal(both, np.isfinite(got_err)), f"{datum} {target}: missing-canopy points match")
                d = np.max(np.abs(got_err[both] - exp_err[both]))
                check(d < 0.01, f"{datum} {target}: per-point error matches (max |diff| {d:.2e} m, rounded to cm)")
                e, g = expected_stats(exp_err), ev["stats"][target]
                check(g["n"] == e["n"] and all(abs(g[k] - e[k]) < 1e-3 for k in ("rmse", "mae", "bias")),
                      f"{datum} {target}: n {g['n']}, RMSE {g['rmse']:.3f} vs expected {e['rmse']:.3f}, "
                      f"bias {g['bias']:+.3f} vs {e['bias']:+.3f}")
            if datum == "EGM96":
                check(abs(ev["stats"]["terrain"]["bias"] - (expected_stats(
                    expected[inside] - truth.terrain_egm2008.to_numpy()[inside])["bias"] - shift)) < 1e-3,
                    "EGM96 bias is the EGM2008 bias minus the 3 m datum shift (right column used)")

        # A grid with no truth: shifted by one pixel -> no match
        other = tmp / "cal" / "other"
        m2 = dict(meta, transform=[10.0, 0.0, meta["transform"][2] + 10, 0.0, -10.0, meta["transform"][5]])
        write_grid(other, m2)
        check(evidence.build_evidence(str(other / "output_dsm.tif"), "EGM2008", [str(run)]) is None,
              "grid one pixel off: no evidence (None), not a wrong match")

        # The API endpoint, with the pipeline's paths pointed at the temp dirs
        import main
        from fastapi.testclient import TestClient
        main.DSM_CAL_DIR = tmp / "cal"
        main.DSM_OUTPUT_PATH = dsm
        main.BASE_DIR = tmp
        main.JOBS_DIR_NAME = "jobs"
        (tmp / "jobs").mkdir()
        client = TestClient(main.app)
        main.jobs["t1"] = {"id": "t1", "status": "done", "metrics": {"vertical_datum": "EGM2008"}}
        main.jobs["t1"]["validation"] = main._save_validation("t1", "EGM2008")
        r = client.get("/jobs/t1/validation")
        check(r.status_code == 200 and r.json()["n_on_dsm"] == int(inside.sum()),
              f"GET /jobs/t1/validation -> {r.status_code}, {r.json().get('n_on_dsm')} points")
        s = main.jobs["t1"]["validation"]
        check(s["available"] and "points" not in s, "job keeps only the summary; points live in their own file")

        main.DSM_OUTPUT_PATH = other / "output_dsm.tif"
        main.DSM_CAL_DIR = tmp / "cal_empty"; (tmp / "cal_empty").mkdir()
        main.jobs["t2"] = {"id": "t2", "status": "done"}
        main.jobs["t2"]["validation"] = main._save_validation("t2", "EGM2008")
        r = client.get("/jobs/t2/validation")
        check(r.status_code == 409 and "no ICESat-2 truth" in r.json()["detail"],
              f"job without truth -> {r.status_code}: {r.json()['detail']}")
        check(client.get("/jobs/nope/validation").status_code == 404, "unknown job -> 404")

    print("\nFAIL:\n  " + "\n  ".join(failures) if failures else "\nPASS")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
