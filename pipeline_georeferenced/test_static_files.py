"""
Checks the static mount serves exactly what the pages load and nothing
else (main.PublicStaticFiles): source, the job store, uploaded / fetched
GeoTIFFs, kept DSMs, lidar truth and model weights all live under the same
folder and must 404 on the public demo URL.

Creates the runtime files it probes (they're all gitignored) and removes
them afterwards.

    python test_static_files.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SERVED = [
    "/", "/index.html", "/history.html", "/dashboard.html", "/3d_visualization/layers.js",
    "/3d_visualization/", "/3d_visualization/index.html", "/3d_visualization/terrain.js",
    "/3d_visualization/controls.js", "/3d_visualization/elevation_probe.js", "/3d_visualization/render_modes.js",
    "/3d_visualization/assets/elevation_16bit.png", "/3d_visualization/assets/satellite.jpg",
    "/3d_visualization/assets/metadata.json",
    "/leaflet_pitch/index.html", "/leaflet_pitch/",
    "/dem_court/index.html", "/dem_court/cache/testaoi/copernicus.f32",
    "/validation/index.html", "/validation/headline.js", "/validation/icesat2_precomputed.json",
    "/site/nav.js",
    "/qgis_prep/data/geo_metadata.json",
]
BLOCKED = [
    "/main.py", "/requirements.txt", "/README.md", "/Dockerfile", "/test_static_files.py",
    "/jobs_meta_store.json", "/jobs_meta/abc_dsm.tif", "/jobs_meta/abc_validation.json",
    "/qgis_prep/data/aoi_cropped.tif", "/qgis_prep/data/upload_source.tif", "/qgis_prep/aoi_config.py",
    "/dsm_calibration/srtm_calibration.py", "/dsm_calibration/real_run/output_dsm.tif",
    "/dsm_calibration/real_run_nainital_fusion/icesat2_atl08_truth.csv",
    "/dem_court/court.py", "/dem_court/cache/testaoi/raw_copernicus_aligned.tif", "/dem_court/cache/testaoi/grid.tif",
    "/validation/evidence.py", "/validation/test_evidence.py",
    "/3d_visualization/README.md", "/track_a_depth/depth_estimator.py",
    "/track_a_depth/checkpoints/depth_anything_v2_vits.pth",
    # traversal and dotfiles
    "/3d_visualization/../main.py", "/3d_visualization/%2e%2e/main.py", "/3d_visualization/..%2fmain.py",
    "/dem_court/cache/../court.py", "/.dockerignore", "/3d_visualization/.hidden.js",
]
MADE = [
    "jobs_meta_store.json", "jobs_meta/abc_dsm.tif", "jobs_meta/abc_validation.json",
    "qgis_prep/data/aoi_cropped.tif", "qgis_prep/data/upload_source.tif",
    "dsm_calibration/real_run/output_dsm.tif", "dsm_calibration/real_run_nainital_fusion/icesat2_atl08_truth.csv",
    "dem_court/cache/testaoi/copernicus.f32", "dem_court/cache/testaoi/raw_copernicus_aligned.tif",
    "dem_court/cache/testaoi/grid.tif", "track_a_depth/checkpoints/depth_anything_v2_vits.pth",
    "3d_visualization/.hidden.js",
]


def main():
    made, dirs = [], []
    for rel in MADE:
        p = ROOT / rel
        if not p.exists():
            for parent in reversed(p.relative_to(ROOT).parents[:-1]):
                d = ROOT / parent
                if not d.exists():
                    d.mkdir(); dirs.append(d)
            p.write_bytes(b'{"secret": {"id": "secret"}}' if p.suffix == ".json" else b"secret")
            made.append(p)
    fails = []
    try:
        import main as app_main
        from fastapi.testclient import TestClient
        client = TestClient(app_main.app)
        for url in SERVED:
            r = client.get(url)
            ok = r.status_code == 200
            print(("ok   " if ok else "FAIL ") + f"served  {url} -> {r.status_code}")
            if not ok:
                fails.append(url)
        for url in BLOCKED:
            r = client.get(url)
            ok = r.status_code == 404 and b"secret" not in r.content
            print(("ok   " if ok else "FAIL ") + f"blocked {url} -> {r.status_code}")
            if not ok:
                fails.append(url)
        r = client.get("/jobs")
        print(("ok   " if r.status_code == 200 else "FAIL ") + f"API still routed: GET /jobs -> {r.status_code}")
        if r.status_code != 200:
            fails.append("/jobs")
    finally:
        for p in made:
            p.unlink()
        for d in reversed(dirs):
            d.rmdir()
    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
