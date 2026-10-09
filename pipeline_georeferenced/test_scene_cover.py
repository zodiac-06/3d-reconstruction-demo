"""
Regression test for the Sentinel-2 scene choice on a bounds-only job, using
the Moga box (30.800N 75.170E, 0.08 degrees) that failed for real: searching
by the buffered bbox ranked 0%-cloud scenes of the neighbouring tiles 43RDP
and 43REP first, none of which overlaps the box, so the crop failed with
"Bounds ... don't overlap stac_source.tif". Tile 43REQ covers it.

The STAC search is stubbed with those three scenes' real footprints
(simplified) and answers like Earth Search: filter by the query's bbox or
intersects geometry, sort by cloud cover, cut to the limit. The download is
stubbed to write a raster over footprint & buffered box (like the real
windowed read); the crop is the real 02_crop_geotiff; the pipeline after the
crop is stubbed as in test_concurrency.py.

Checks:
- find_scene picks the least-cloudy scene that fully covers the box (43REQ);
- a real POST /jobs?wait=true for the Moga box finishes, using 43REQ;
- when the scenes reaching the box each cover only part of it (a tile
  boundary): wait=true -> the job fails with a clear message (box crosses
  a Sentinel-2 tile boundary, choose a different box), wait=false -> 400
  with that message and no job queued;
- when no scene reaches the box at all, wait=false still queues the job
  (no 500) and it fails with the "no scenes found" message, as before.

    python test_scene_cover.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "validation"))
import test_evidence as te  # noqa: E402  (synthetic grid writer)

MOGA = (75.13, 30.76, 75.21, 30.84)  # 30.800N 75.170E, 0.08 deg
SCENES = [  # real footprints (Earth Search, simplified to 0.0005 deg), real cloud cover
    {"id": "S2A_43RDP_20260607_0_L2A", "cloud": 0.0,
     "coords": [[73.9551, 30.7289], [73.9655, 29.7382], [75.1009, 29.7422], [75.1019, 30.733], [73.9551, 30.7289]]},
    {"id": "S2C_43REP_20251127_0_L2A", "cloud": 0.0,
     "coords": [[74.9998, 30.7331], [74.9998, 29.7422], [76.1352, 29.7373], [76.1466, 30.728], [74.9998, 30.7331]]},
    {"id": "S2C_43REQ_20260215_0_L2A", "cloud": 0.000146,
     "coords": [[74.9998, 31.6355], [74.9998, 30.6448], [76.1456, 30.6398], [76.1576, 31.6303], [74.9998, 31.6355]]},
]
COVERING = "S2C_43REQ_20260215_0_L2A"
# A box across a tile boundary: the 43REQ scene cut to stop half-way up the box,
# so every scene that reaches the box covers only part of it.
HALF_43REQ = {"id": "S2C_43REQ_20260215_0_L2A", "cloud": 0.000146,
              "coords": [[74.9998, 31.6355], [74.9998, 30.80], [76.1456, 30.80], [76.1576, 31.6303], [74.9998, 31.6355]]}


def feature(s):
    return {"type": "Feature", "id": s["id"], "geometry": {"type": "Polygon", "coordinates": [s["coords"]]},
            "properties": {"eo:cloud_cover": s["cloud"], "datetime": "2026-02-15T05:40:00Z"},
            "assets": {"visual": {"href": f"https://example.invalid/{s['id']}/TCI.tif"}}}


class FakeStac:
    """requests stand-in: answers a STAC /search POST like Earth Search does."""

    def __init__(self, scenes):
        self.scenes, self.queries = scenes, []

    def post(self, url, json=None, timeout=None):
        self.queries.append(json)
        area = shape(json["intersects"]) if "intersects" in json else box(*json["bbox"])
        hits = sorted((s for s in self.scenes if shape(feature(s)["geometry"]).intersects(area)), key=lambda s: s["cloud"])
        body = {"features": [feature(s) for s in hits[:json.get("limit", 10)]]}

        class Resp:
            status_code, text = 200, "{...}"

            @staticmethod
            def json():
                return body

            @staticmethod
            def raise_for_status():
                pass
        return Resp()


def main():
    import main as app_main
    from fastapi.testclient import TestClient

    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    fetch = app_main.fetch_geotiff_mod
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
        app_main.STAC_SOURCE_PATH, app_main.CROPPED_PATH = tmp / "stac_source.tif", tmp / "aoi_cropped.tif"
        app_main.dem_court_mod.CACHE_ROOT = str(tmp / "court_cache")
        app_main.jobs.clear()

        downloaded = []

        def fake_download(scene, bbox, out_path=None):
            """Like the real windowed read: only footprint & buffered box has data."""
            downloaded.append(scene["id"])
            area = shape(scene["geometry"]).intersection(box(*bbox))
            w, s, e, n = area.bounds
            with rasterio.open(out_path, "w", driver="GTiff", width=40, height=40, count=3, dtype="uint8",
                               crs="EPSG:4326", transform=from_bounds(w, s, e, n, 40, 40)) as dst:
                dst.write(np.full((3, 40, 40), 120, np.uint8))

        def fake_pipeline(dem_source, sources, timings):
            with rasterio.open(grid / "output_dsm.tif") as src, rasterio.open(out, "w", **src.profile) as dst:
                dst.write(src.read(1), 1)
            return {"metrics": {"dem_source": "copernicus", "vertical_datum": "EGM2008"},
                    "view_urls": {}, "bounds_epsg4326": dict(zip(("west", "south", "east", "north"), MOGA))}

        real_requests = fetch.requests
        fetch.download_cropped = fake_download
        app_main._run_pipeline = fake_pipeline
        client = TestClient(app_main.app, raise_server_exceptions=False)
        form = dict(zip(("west", "south", "east", "north"), map(str, MOGA)))
        b = app_main.SOURCE_BUFFER_DEG
        buffered = (MOGA[0] - b, MOGA[1] - b, MOGA[2] + b, MOGA[3] + b)
        try:
            # 1. find_scene: least-cloudy scene that covers the box
            fetch.requests = FakeStac(SCENES)
            try:
                got = fetch.find_scene(buffered, cover=MOGA)["id"]
            except Exception as e:
                got = f"{type(e).__name__}: {e}"
            check(got == COVERING, f"find_scene picks the scene covering the Moga box: {got}")

            # 2. a real bounds-only job for the Moga box finishes on that scene
            fetch.requests = FakeStac(SCENES)
            downloaded.clear()
            r = client.post("/jobs?wait=true", data=form)
            job = r.json() if r.status_code == 200 else {}
            check(r.status_code == 200 and job.get("status") == "done",
                  f"Moga job finishes: HTTP {r.status_code}, status {job.get('status')}, error {job.get('error')!r}")
            check((job.get("input") or {}).get("stac_scene_id") == COVERING and downloaded == [COVERING],
                  f"job used {(job.get('input') or {}).get('stac_scene_id')}, downloaded {downloaded}")

            # 3. tile boundary: scenes reach the box but none covers it -> clear message, 400 for wait=false
            fetch.requests = FakeStac([s for s in SCENES if s["id"] != COVERING] + [HALF_43REQ])
            r = client.post("/jobs?wait=true", data=form)
            job = r.json() if r.status_code == 200 else {}
            err = job.get("error") or ""
            check(job.get("status") == "failed" and "crosses a Sentinel-2 tile boundary" in err and "Choose a different box" in err,
                  f"wait=true, no covering scene: job failed with a clear message: {err!r}")
            n_jobs = len(app_main.jobs)
            r = client.post("/jobs?wait=false", data=form)
            detail = (r.json() or {}).get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
            check(r.status_code == 400 and "crosses a Sentinel-2 tile boundary" in str(detail),
                  f"wait=false, no covering scene: HTTP {r.status_code}, {str(detail)[:110]!r}")
            check(len(app_main.jobs) == n_jobs, "no job queued for the rejected box")

            # 4. no scene reaches the box at all: unchanged behaviour, no 500
            fetch.requests = FakeStac([s for s in SCENES if s["id"] != COVERING])
            r = client.post("/jobs?wait=false", data=form)
            jid = r.json().get("id") if r.status_code == 200 else None
            for _ in range(200):
                if jid is None or app_main.jobs.get(jid, {}).get("status") in ("done", "failed"):
                    break
                __import__("time").sleep(0.05)
            job = app_main.jobs.get(jid, {}) if jid else {}
            check(r.status_code == 200 and job.get("status") == "failed" and "No Sentinel-2 scenes found" in (job.get("error") or ""),
                  f"no scene at all: wait=false queues (HTTP {r.status_code}), job fails as before: {job.get('error')!r}")
        finally:
            fetch.requests = real_requests

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
