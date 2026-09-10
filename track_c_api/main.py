from fastapi import FastAPI, UploadFile, File, HTTPException
from pipeline import run_pipeline
from uuid import uuid4
from pathlib import Path
import shutil


app = FastAPI(title="Track C Calibration & Scale API")


jobs = {}


@app.get("/")
def home():
    return {
        "message": "Track C API is running"
    }


@app.post("/jobs")
async def create_job(
    photo: UploadFile = File(...)
):
    job_id = str(uuid4())

    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "filename": photo.filename
    }

    # Save uploaded photo
    jobs_folder = Path("jobs")
    jobs_folder.mkdir(exist_ok=True)

    photo_path = jobs_folder / f"{job_id}_{photo.filename}"

    with open(photo_path, "wb") as buffer:
        shutil.copyfileobj(photo.file, buffer)

    # Temporary calibration values for MVP testing
    known_length = 10.0
    measured_length = 5.0

    try:
        result = run_pipeline(
            known_length,
            measured_length
        )

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)

    return {
        "job_id": job_id,
        "status": jobs[job_id]["status"]
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):

    if job_id not in jobs:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return jobs[job_id]


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):

    if job_id not in jobs:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    job = jobs[job_id]

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail="Job is not completed"
        )

    return job["result"]