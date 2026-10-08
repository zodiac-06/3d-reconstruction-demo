"""
Regression test: a job whose metrics contain NaN / +-inf must still be
readable. A real 0.03 degree box at Nainital gives a Theil-Sen slope of
-0.0, so corr_fused_vs_theil_sen_dsm is NaN; before main._json_safe that job
answered GET /jobs/{id} with a 500 and left a bare NaN in the job store.

The pipeline is stubbed as in test_jobs_api.py: _run_pipeline writes a
synthetic DSM and returns metrics holding NaN and +-inf (Python and numpy
floats, also nested), next to finite values that must come through as is.

Checks, for wait=true and wait=false jobs:
- POST /jobs, GET /jobs/{id} and GET /jobs answer 200 with null in place
  of every non-finite value, finite values unchanged;
- the job store file is strict JSON (no bare NaN / Infinity);
- a store written before the fix (bare NaN in it) loads and is served.

    python test_nan_metrics.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT / "dsm_calibration"))
import test_evidence as te  # noqa: E402  (synthetic grid writer)

FINITE = {"s": 1.0, "offset": 0.0, "slope": -0.0, "rmse": 171.86, "detail_std_m": 0.0}


def strict_load(path):
    def reject(c):
        raise ValueError(f"bare {c}")
    return json.loads(Path(path).read_text(), parse_constant=reject)


def main():
    import main as app_main
    from fastapi.testclient import TestClient

    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    meta = json.loads((ROOT / "qgis_prep" / "data" / "geo_metadata.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        grid = tmp / "grid"
        te.write_grid(grid, meta)
        out = tmp / "real_run" / "output_dsm.tif"
        out.parent.mkdir()
        app_main.BASE_DIR, app_main.JOBS_DIR_NAME = tmp, "jobs"
        (tmp / "jobs").mkdir()
        app_main.DSM_CAL_DIR, app_main.DSM_OUTPUT_PATH = tmp / "cal", out
        (tmp / "cal").mkdir()
        app_main.JOBS_STORE_PATH = tmp / "jobs_store.json"
        app_main.CROPPED_PATH = grid / "aoi_cropped.tif"
        app_main.dem_court_mod.CACHE_ROOT = str(tmp / "court_cache")
        app_main.jobs.clear()

        def fake_prepare(mode, geotiff, bounds, timings):
            return {"requested_bounds": list(bounds), "scene_cloud_cover_pct": np.float64(7e-06)}

        def fake_pipeline(dem_source, sources, timings):
            with rasterio.open(grid / "output_dsm.tif") as src, rasterio.open(out, "w", **src.profile) as dst:
                dst.write(src.read(1), 1)
            return {"metrics": {"dem_source": "copernicus", "vertical_datum": "EGM2008", **FINITE,
                                "corr_fused_vs_theil_sen_dsm": float("nan"),
                                "correlation": np.float64("nan"),
                                "mae": float("inf"),
                                "nested": [1.5, float("-inf"), {"deep": np.float32("nan"), "ok": 2.5}]},
                    "view_urls": {}, "bounds_epsg4326": meta["bounds_epsg4326"]}

        app_main._prepare_cropped_input = fake_prepare
        app_main._run_pipeline = fake_pipeline
        client = TestClient(app_main.app, raise_server_exceptions=False)
        b = meta["bounds_epsg4326"]
        form = {k: str(b[k]) for k in ("west", "south", "east", "north")}

        def metrics_ok(m, label):
            check(m.get("corr_fused_vs_theil_sen_dsm") is None and m.get("correlation") is None and m.get("mae") is None,
                  f"{label}: NaN / inf metrics are null")
            check(m.get("nested") == [1.5, None, {"deep": None, "ok": 2.5}], f"{label}: nested NaN / inf are null: {m.get('nested')}")
            check(all(m.get(k) == v for k, v in FINITE.items()) and m.get("dem_source") == "copernicus",
                  f"{label}: finite values unchanged")

        # wait=true: the POST answers with the finished job
        r = client.post("/jobs?wait=true", data=form)
        check(r.status_code == 200, f"POST /jobs?wait=true -> {r.status_code}")
        job_id = r.json().get("id") if r.status_code == 200 else next(iter(app_main.jobs), None)
        if r.status_code == 200:
            metrics_ok(r.json().get("metrics") or {}, "POST response")
        g = client.get(f"/jobs/{job_id}")
        check(g.status_code == 200, f"GET /jobs/{{id}} -> {g.status_code}")
        if g.status_code == 200:
            metrics_ok(g.json().get("metrics") or {}, "GET /jobs/{id}")
            check(g.json().get("input", {}).get("scene_cloud_cover_pct") == 7e-06, "numpy finite value in the job input kept")
        lst = client.get("/jobs")
        check(lst.status_code == 200 and any(j["id"] == job_id for j in lst.json()), f"GET /jobs -> {lst.status_code}, job listed")
        try:
            stored = strict_load(app_main.JOBS_STORE_PATH)
            m = (stored.get(job_id) or {}).get("metrics") or {}
            check("corr_fused_vs_theil_sen_dsm" in m and m["corr_fused_vs_theil_sen_dsm"] is None,
                  "job store is strict JSON, the NaN metric stored as null")
        except ValueError as e:
            check(False, f"job store is strict JSON ({e})")

        # wait=false: polled to done, every poll answered
        r = client.post("/jobs?wait=false", data=form)
        check(r.status_code == 200, f"POST /jobs?wait=false -> {r.status_code}")
        job2 = r.json()["id"] if r.status_code == 200 else None
        codes = []
        for _ in range(200):
            if job2 is None:
                break
            p = client.get(f"/jobs/{job2}")
            codes.append(p.status_code)
            if p.status_code != 200 or p.json().get("status") in ("done", "failed"):
                break
            time.sleep(0.05)
        check(job2 is not None and codes and all(c == 200 for c in codes) and p.json().get("status") == "done",
              f"wait=false job polled to done, every poll 200 ({len(codes)} polls, codes {sorted(set(codes))})")
        if job2 and codes and codes[-1] == 200:
            metrics_ok(p.json().get("metrics") or {}, "polled job")

        # a store written before the fix: bare NaN in the file
        old = {"old1": {"id": "old1", "status": "done", "input_mode": "fetch_by_bounds",
                        "metrics": {"dem_source": "copernicus", "slope": -0.0, "corr_fused_vs_theil_sen_dsm": float("nan")}}}
        app_main.JOBS_STORE_PATH.write_text(json.dumps(old))  # json.dumps writes NaN, as the old _save_jobs did
        check("NaN" in app_main.JOBS_STORE_PATH.read_text(), "old-style store has a bare NaN")
        app_main.jobs.clear()
        app_main.jobs.update(app_main._load_jobs())
        g = client.get("/jobs/old1")
        check(g.status_code == 200 and g.json()["metrics"]["corr_fused_vs_theil_sen_dsm"] is None,
              f"job from an old store served: GET -> {g.status_code}")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
