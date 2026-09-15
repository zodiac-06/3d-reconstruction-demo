import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pipeline import run_pipeline

BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Track C Calibration & Scale API")

# MVP-only: Track D's viewer runs from a different local origin (or file://)
# than this API, so the browser needs CORS headers to allow the fetch calls.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}


@app.get("/")
def home():
    return {"message": "Track C API is running"}


@app.post("/jobs")
async def create_job(photo: UploadFile = File(...)):
    job_id = str(uuid4())
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "result_url": None,
        "error": None,
    }

    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    photo_path = job_dir / photo.filename
    with open(photo_path, "wb") as buffer:
        shutil.copyfileobj(photo.file, buffer)

    jobs[job_id]["status"] = "running"

    try:
        run_pipeline(
            job_dir,
            photo_path,
            progress_cb=lambda pct: jobs[job_id].update(progress=pct),
        )
        jobs[job_id]["status"] = "done"
        jobs[job_id]["result_url"] = f"/jobs/{job_id}/result"
    except Exception as exc:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(exc)

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
