import json
import os
import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pipeline import run_pipeline

BASE_DIR = Path(__file__).resolve().parent

# TRACK_C_JOBS_DIR lets verification/test runs point at an isolated directory
# (e.g. "test_jobs") instead of the real "jobs" -- so testing never touches
# real job data. jobs_store.json is named to match, so test runs get their
# own store file too and never clobber the real one.
JOBS_DIR_NAME = os.environ.get("TRACK_C_JOBS_DIR", "jobs")
JOBS_DIR = BASE_DIR / JOBS_DIR_NAME
JOBS_DIR.mkdir(exist_ok=True)
JOBS_STORE_PATH = BASE_DIR / f"{JOBS_DIR_NAME}_store.json"

app = FastAPI(title="Track C Calibration & Scale API")

# MVP-only: Track D's viewer runs from a different local origin (or file://)
# than this API, so the browser needs CORS headers to allow the fetch calls.
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


def _update_job(job_id: str, **fields):
    jobs[job_id].update(fields)
    _save_jobs()


@app.get("/")
def home():
    return {"message": "Track C API is running"}


@app.post("/jobs")
async def create_job(
    photo: UploadFile = File(...),
    reference_length_m: float = Form(
        ..., description="Real measured length (meters) of the mesh bbox axis named by reference_axis"
    ),
    reference_axis: Literal["width", "height", "depth"] = Form(
        "width", description="Which bbox axis reference_length_m measures (x/y/z respectively)"
    ),
):
    job_id = str(uuid4())
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "result_url": None,
        "error": None,
    }
    _save_jobs()

    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    photo_path = job_dir / photo.filename
    with open(photo_path, "wb") as buffer:
        shutil.copyfileobj(photo.file, buffer)

    _update_job(job_id, status="running")

    try:
        run_pipeline(
            job_dir,
            photo_path,
            reference_length_m=reference_length_m,
            reference_axis=reference_axis,
            progress_cb=lambda pct: _update_job(job_id, progress=pct),
        )
        _update_job(job_id, status="done", result_url=f"/jobs/{job_id}/result")
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))

    return jobs[job_id]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    j = jobs[job_id]
    return {
        "id": j["id"],
        "status": j["status"],
        "progress": j["progress"],
        "result_url": j["result_url"],
        "error": j["error"],
    }


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job is not done (status={job['status']})")

    mesh_path = JOBS_DIR / job_id / "mesh.obj"
    if not mesh_path.exists():
        raise HTTPException(status_code=404, detail="Result mesh not found")

    return FileResponse(mesh_path, media_type="text/plain", filename="mesh.obj")
