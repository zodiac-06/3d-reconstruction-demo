"""
Drives POST /jobs end to end with the two heavy stages stubbed
(_prepare_cropped_input and _run_pipeline -- no Depth Anything, no DEM
fetch): the stub writes a known synthetic DSM where the pipeline writes
its output, then everything after it runs for real -- validation scoring,
keeping and tagging the job's DSM, the job list and the download.

Checks:
- each finished job keeps its own DSM, pixel-identical to what the
  pipeline wrote, tagged with units / vertical datum / DEM source;
- a second job doesn't overwrite the first job's download;
- GET /jobs lists newest first, jobs without a timestamp last;
- a failed job has no download (409), an unknown job 404;
- a job whose grid has ICESat-2 truth gets a validation summary through
  the real create_job path.

    python test_jobs_api.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT / "dsm_calibration"))
import test_evidence as te  # noqa: E402  (synthetic grid + ICESat-2 truth writers)


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
        truth_run = tmp / "cal" / "real_run_synthetic"
        te.write_grid(truth_run, meta)
        te.write_truth(truth_run, meta, np.random.default_rng(3))
        planted = rasterio.open(truth_run / "output_dsm.tif").read(1)

        out = tmp / "real_run" / "output_dsm.tif"
        out.parent.mkdir()
        app_main.BASE_DIR, app_main.JOBS_DIR_NAME = tmp, "jobs"
        (tmp / "jobs").mkdir()
        app_main.DSM_CAL_DIR, app_main.DSM_OUTPUT_PATH = tmp / "cal", out
        app_main.JOBS_STORE_PATH = tmp / "jobs_store.json"
        app_main.jobs.clear()
        app_main.jobs["old"] = {"id": "old", "status": "done", "metrics": {}, "input_mode": "cropped"}  # pre-timestamps

        runs = {"n": 0}

        def fake_prepare(mode, geotiff, bounds, timings):
            return {"filename": "a.tif"}

        def fake_pipeline(dem_source, sources, timings):
            runs["n"] += 1
            if runs["n"] == 3:
                raise RuntimeError("DEM fetch failed (stub)")
            # job 1: the planted plane; job 2: the same plane + 100 m, to tell them apart
            with rasterio.open(truth_run / "output_dsm.tif") as src:
                profile = src.profile
            with rasterio.open(out, "w", **profile) as dst:
                dst.write(planted + (100.0 if runs["n"] == 2 else 0.0), 1)
            return {"metrics": {"dem_source": "copernicus", "vertical_datum": "EGM2008"},
                    "view_urls": {}, "bounds_epsg4326": meta["bounds_epsg4326"]}

        app_main._prepare_cropped_input = fake_prepare
        app_main._run_pipeline = fake_pipeline
        client = TestClient(app_main.app)
        post = lambda: client.post("/jobs", files={"geotiff": ("a.tif", b"x", "image/tiff")}).json()

        j1 = post()
        check(j1["status"] == "done" and j1["dsm_download"] == f"/jobs/{j1['id']}/dsm.tif" and j1["created_at"],
              f"job 1 done, created_at {j1['created_at']}, download {j1['dsm_download']}")
        v = j1["validation"]
        check(v["available"] and v["stats"]["canopy_top"]["n"] > 300,
              f"job 1 scored through create_job: {v['stats']['canopy_top']['n']} ICESat-2 points, "
              f"RMSE {v['stats']['canopy_top']['rmse']:.2f} m")
        j2 = post()
        j3 = post()
        check(j3["status"] == "failed" and "dsm_download" not in j3, "job 3 failed: no DSM kept")

        for job, offset in ((j1, 0.0), (j2, 100.0)):
            r = client.get(job["dsm_download"])
            check(r.status_code == 200 and r.headers["content-type"] == "image/tiff" and
                  f"depthwizard_dsm_{job['id'][:8]}.tif" in r.headers.get("content-disposition", ""),
                  f"download {job['id'][:8]}: 200, image/tiff, named file")
            p = tmp / f"dl_{job['id']}.tif"
            p.write_bytes(r.content)
            with rasterio.open(p) as ds:
                same = np.array_equal(ds.read(1), planted + offset)
                tags, desc = ds.tags(), ds.descriptions[0]
                geo_ok = ds.crs.to_epsg() == 32644 and tuple(ds.transform)[:6] == tuple(meta["transform"])
            check(same, f"download {job['id'][:8]}: pixels are that job's DSM (offset {offset:+.0f} m)")
            check(tags.get("VERTICAL_DATUM") == "EGM2008" and tags.get("UNITS") == "metre" and
                  tags.get("DEM_SOURCE") == "copernicus" and tags.get("JOB_ID") == job["id"] and "EGM2008" in desc,
                  f"download {job['id'][:8]}: tagged {tags.get('UNITS')}, {tags.get('VERTICAL_DATUM')}, "
                  f"{tags.get('DEM_SOURCE')}; band '{desc}'")
            check(geo_ok, f"download {job['id'][:8]}: CRS and transform kept")

        lst = client.get("/jobs").json()
        check([j["id"] for j in lst] == [j3["id"], j2["id"], j1["id"], "old"],
              "GET /jobs: newest first, the job without a timestamp last")
        row1 = next(j for j in lst if j["id"] == j1["id"])
        check(row1["validation_n"] == v["stats"]["canopy_top"]["n"] and row1["dem_source"] == "copernicus",
              "GET /jobs row carries DEM and validation summary")
        check(client.get(f"/jobs/{j3['id']}/dsm.tif").status_code == 409, "failed job's DSM -> 409")
        check(client.get("/jobs/old/dsm.tif").status_code == 409, "pre-download job's DSM -> 409")
        check(client.get("/jobs/nope/dsm.tif").status_code == 404, "unknown job -> 404")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
