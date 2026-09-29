"""
Checks that a running job doesn't freeze the server, and that two jobs
never run the pipeline at the same time (it writes fixed paths).

Starts the real app under uvicorn on a local port with the heavy stages
stubbed: the stub pipeline takes PIPELINE_S seconds and records when it
starts and ends. Two uploads are sent 0.3 s apart; while the first runs,
a page and GET /jobs are timed and the job statuses read. Then the same
with POST /jobs?wait=false: the response must come back at once, the
upload's bytes must reach the pipeline after the request has closed, and
polling GET /jobs/{id} must end at "done". Meanwhile a watcher polls the
job and re-reads the job store as fast as it can: no response may say
"done" without the job's download link and timings, and every read of the
store must be complete JSON.

    python test_concurrency.py
"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT / "dsm_calibration"))
import test_evidence as te  # noqa: E402

PIPELINE_S = 1.5


def main():
    import httpx
    import uvicorn
    import main as app_main

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
        out = tmp / "output_dsm.tif"
        app_main.BASE_DIR, app_main.JOBS_DIR_NAME = tmp, "jobs"
        (tmp / "jobs").mkdir()
        app_main.DSM_CAL_DIR, app_main.DSM_OUTPUT_PATH = tmp / "cal", out
        (tmp / "cal").mkdir()
        app_main.JOBS_STORE_PATH = tmp / "jobs_store.json"
        app_main.jobs.clear()

        spans = []

        def fake_pipeline(dem_source, sources, timings):
            start = time.perf_counter()
            time.sleep(PIPELINE_S)
            with rasterio.open(grid / "output_dsm.tif") as src, rasterio.open(out, "w", **src.profile) as dst:
                dst.write(src.read(1), 1)
            spans.append((start, time.perf_counter()))
            return {"metrics": {"dem_source": "copernicus", "vertical_datum": "EGM2008"},
                    "view_urls": {}, "bounds_epsg4326": meta["bounds_epsg4326"]}

        received = []

        def fake_prepare(mode, geotiff, bounds, timings):
            received.append(geotiff.file.read() if geotiff is not None else None)
            return {"filename": geotiff.filename if geotiff is not None else None}

        app_main._prepare_cropped_input = fake_prepare
        app_main._run_pipeline = fake_pipeline

        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        server = uvicorn.Server(uvicorn.Config(app_main.app, host="127.0.0.1", port=port,
                                               lifespan="off", log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                httpx.get(base + "/index.html"); break
            except httpx.ConnectError:
                time.sleep(0.05)

        results = {}

        def post(name):
            r = httpx.post(base + "/jobs", files={"geotiff": ("a.tif", b"x", "image/tiff")}, timeout=30)
            results[name] = r.json()

        a = threading.Thread(target=post, args=("a",)); a.start()
        time.sleep(0.3)
        b = threading.Thread(target=post, args=("b",)); b.start()
        time.sleep(0.3)

        t0 = time.perf_counter(); page = httpx.get(base + "/index.html"); page_s = time.perf_counter() - t0
        t0 = time.perf_counter(); lst = httpx.get(base + "/jobs"); list_s = time.perf_counter() - t0
        statuses = sorted(j["status"] for j in lst.json())
        check(page.status_code == 200 and page_s < 0.5,
              f"page served in {page_s * 1000:.0f} ms while a {PIPELINE_S} s job runs")
        check(lst.status_code == 200 and list_s < 0.5, f"GET /jobs answered in {list_s * 1000:.0f} ms")
        check(statuses == ["queued", "running"], f"mid-run statuses: {statuses}")

        a.join(); b.join()
        check(results["a"]["status"] == "done" and results["b"]["status"] == "done", "both jobs finished")
        spans.sort()
        check(len(spans) == 2 and spans[0][1] <= spans[1][0],
              f"pipeline runs never overlapped (first ended {spans[0][1] - spans[1][0]:+.2f} s relative to second's start)")
        qb = results["b"]["timings_s"]["queued"]
        check(qb > PIPELINE_S - 0.5, f"second job waited {qb:.2f} s in the queue")
        with rasterio.open(tmp / "jobs" / f"{results['a']['id']}_dsm.tif") as d:
            check(d.tags().get("JOB_ID") == results["a"]["id"], "first job's kept DSM is its own")

        # --- wait=false on the same server: respond at once, run in the background, poll ---
        received.clear()
        payload = b"GeoTIFF bytes " * 1000
        t0 = time.perf_counter()
        r = httpx.post(base + "/jobs?wait=false", files={"geotiff": ("up.tif", payload, "image/tiff")}, timeout=30)
        post_s = time.perf_counter() - t0
        job = r.json()
        check(r.status_code == 200 and post_s < 0.5 and job["status"] in ("queued", "running"),
              f"wait=false answered in {post_s * 1000:.0f} ms with status '{job['status']}'")
        watch = {"gets": 0, "early_done": 0, "store_reads": 0, "store_bad": 0}

        def watcher(job_id):
            with httpx.Client() as c:
                while True:
                    j = c.get(base + f"/jobs/{job_id}").json()
                    watch["gets"] += 1
                    if j["status"] == "done" and not (j.get("dsm_download") and "timings_s" in j):
                        watch["early_done"] += 1
                    try:
                        json.loads(app_main.JOBS_STORE_PATH.read_text())
                        watch["store_reads"] += 1
                    except (ValueError, FileNotFoundError):
                        watch["store_bad"] += 1
                    if j["status"] in ("done", "failed"):
                        return

        w = threading.Thread(target=watcher, args=(job["id"],)); w.start()
        polls = 0
        while job["status"] in ("queued", "running") and polls < 100:
            time.sleep(0.2); polls += 1
            job = httpx.get(base + f"/jobs/{job['id']}").json()
        check(job["status"] == "done" and job["dsm_download"], f"polled to done after {polls} polls")
        w.join()
        check(watch["early_done"] == 0 and watch["store_bad"] == 0 and watch["gets"] > 20,
              f"watcher: {watch['gets']} polls, {watch['early_done']} said done before the job's record was complete; "
              f"{watch['store_reads']} store reads, {watch['store_bad']} incomplete")
        check(received == [payload], f"pipeline got the upload's {len(payload):,} bytes after the request closed")
        check(not (tmp / "jobs" / f"{job['id']}_upload.tif").exists(), "background upload copy removed")
        r = httpx.post(base + "/jobs?wait=false", data={"west": 79.42, "south": 29.35, "east": 79.51, "north": 29.42})
        job = r.json()
        while job["status"] in ("queued", "running"):
            time.sleep(0.2)
            job = httpx.get(base + f"/jobs/{job['id']}").json()
        check(job["status"] == "done" and received[-1] is None, "wait=false without a file (fetch by bounds) works")
        server.should_exit = True

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
