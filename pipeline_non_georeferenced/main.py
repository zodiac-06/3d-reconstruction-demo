"""
Non-georeferenced pipeline: upload any PNG/JPG -> Depth Anything V2 relative
depth map -> terrain-style 3D viewer. No CRS, no SRTM, no scale calibration
-- per the shared contract's own note, relative depth can be used directly
when there's no real-world reference to calibrate against.

Structure follows track_c_api/main.py as a template (job dict + on-disk
store, synchronous single-job MVP scope, lifespan checkpoint hook), not
rewritten from scratch. The one real difference: track_c_api's endpoint
needs reference_length_m/reference_axis because it calibrates to a real
scale; this pipeline has no such step, so its one upload endpoint just
needs the photo.
"""
import json
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
FRONTEND_DIR = BASE_DIR / "frontend"

sys.path.insert(0, str(REPO_ROOT / "track_a_depth"))

JOBS_DIR_NAME = os.environ.get("PIPELINE_JOBS_DIR", "jobs")
JOBS_DIR = BASE_DIR / JOBS_DIR_NAME
JOBS_DIR.mkdir(exist_ok=True)
JOBS_STORE_PATH = BASE_DIR / f"{JOBS_DIR_NAME}_store.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from depth_estimator import ensure_checkpoint
    ensure_checkpoint("vits")
    yield


app = FastAPI(title="Non-Georeferenced Depth Terrain Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_jobs():
    if JOBS_STORE_PATH.exists():
        with open(JOBS_STORE_PATH) as f:
            return json.load(f)
    return {}


def _save_jobs():
    with open(JOBS_STORE_PATH, "w") as f:
        json.dump(jobs, f, indent=2)


jobs = _load_jobs()


def depth_to_heightmap_png(depth_npy_path, out_png_path):
    """Normalizes relative depth to a 0-65535 grayscale heightmap. No real
    units -- this is deliberately just relative height, per the contract's
    own note that non-georeferenced input doesn't need a scale calibration
    step (there's nothing real-world to calibrate it against)."""
    depth = np.load(depth_npy_path).astype(np.float64)
    lo, hi = float(depth.min()), float(depth.max())
    normalized = (depth - lo) / max(hi - lo, 1e-9)
    as_16bit = np.clip(normalized * 65535, 0, 65535).astype(np.uint16)
    Image.fromarray(as_16bit, mode="I;16").save(out_png_path)
    return lo, hi


@app.get("/api/health")
def health():
    return {"message": "Non-georeferenced pipeline is running"}


@app.post("/jobs")
async def create_job(photo: UploadFile = File(...)):
    job_id = str(uuid4())
    jobs[job_id] = {"id": job_id, "status": "running", "error": None}
    _save_jobs()

    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        photo_path = job_dir / photo.filename
        with open(photo_path, "wb") as f:
            shutil.copyfileobj(photo.file, f)

        from depth_estimator import DepthPipeline
        pipeline = DepthPipeline(encoder="vits")
        pipeline.process_image(str(photo_path), str(job_dir))

        lo, hi = depth_to_heightmap_png(
            job_dir / "depth.npy", job_dir / "heightmap_16bit.png"
        )

        jobs[job_id].update(
            status="done",
            texture_filename=photo.filename,
            relative_depth_range=[lo, hi],
            result_urls={
                "texture": f"/jobs/{job_id}/texture",
                "heightmap": f"/jobs/{job_id}/heightmap",
            },
        )
    except Exception as exc:
        jobs[job_id].update(status="failed", error=str(exc))

    _save_jobs()
    return jobs[job_id]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/jobs/{job_id}/heightmap")
def get_heightmap(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not found or not done")
    path = JOBS_DIR / job_id / "heightmap_16bit.png"
    return FileResponse(path, media_type="image/png")


@app.get("/jobs/{job_id}/texture")
def get_texture(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not found or not done")
    path = JOBS_DIR / job_id / jobs[job_id]["texture_filename"]
    return FileResponse(path)


# Registered last so it never shadows /jobs* or /api/* above.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
