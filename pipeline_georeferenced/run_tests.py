"""
Runs every test script in this pipeline and prints one line each.

    python run_tests.py

Needs requirements.txt plus httpx (FastAPI's test client), uvicorn, and
node >= 18 on PATH for the viewer tests. No model weights. test_map_select.py
also needs Playwright's Chromium (playwright install chromium) and network
access to unpkg.com for Leaflet.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = [
    "dsm_calibration/test_viewer_probe.py",
    "dsm_calibration/test_render_modes.py",
    "dsm_calibration/test_pixel_size.py",
    "dsm_calibration/test_viewer_assets.py",
    "validation/test_evidence.py",
    "test_jobs_api.py",
    "test_concurrency.py",
    "test_static_files.py",
    "test_analysis_layers.py",
    "test_map_select.py",
]


def main():
    failed = []
    for t in TESTS:
        start = time.perf_counter()
        r = subprocess.run([sys.executable, t], cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {t}  ({time.perf_counter() - start:.1f} s)")
        if not ok:
            failed.append(t)
            print("      " + "\n      ".join((r.stdout + r.stderr).strip().splitlines()[-15:]))
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
