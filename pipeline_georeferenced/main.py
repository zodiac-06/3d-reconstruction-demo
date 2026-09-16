"""
Georeferenced pipeline: upload an already-cropped GeoTIFF -> real absolute-
elevation DSM, calibrated against SRTM, viewable in both the 2D context map
and the 3D terrain flythrough.

Scope, deliberately: the uploaded GeoTIFF must already be cropped to the
area of interest (see README.md). This pipeline does not do scene search,
windowed download, or interactive cropping -- that's qgis_prep/01-02's job,
run separately, ahead of time, by whoever is preparing the AOI. Uploading
an uncropped multi-hundred-MB scene will work but will be slow and fetch
an SRTM tile sized to match it, not to any sensible smaller area.

Reuses, doesn't reimplement: track_a_depth/depth_estimator.py (a local
copy of the repo-root track_a_depth/, including checkpoints/, so this
whole folder is self-contained for a Docker build -- code unchanged),
qgis_prep/03_extract_metadata.py + 04_fetch_srtm.py (given callable entry
points, CLI behavior unchanged), dsm_calibration/srtm_calibration.py and
geotiff_to_viewer_assets.py (unchanged). This file is the orchestration
glue between them, not a reimplementation of any of them.
"""
import importlib.util
import json
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

QGIS_PREP_DIR = BASE_DIR / "qgis_prep"
DSM_CAL_DIR = BASE_DIR / "dsm_calibration"
VIZ_DIR = BASE_DIR / "3d_visualization"

DATA_DIR = QGIS_PREP_DIR / "data"
CROPPED_PATH = DATA_DIR / "aoi_cropped.tif"
META_PATH = DATA_DIR / "geo_metadata.json"
SRTM_ALIGNED_PATH = DATA_DIR / "srtm_dem_aligned.tif"
REAL_RUN_DIR = DSM_CAL_DIR / "real_run"
DSM_OUTPUT_PATH = REAL_RUN_DIR / "output_dsm.tif"

DATA_DIR.mkdir(parents=True, exist_ok=True)
REAL_RUN_DIR.mkdir(parents=True, exist_ok=True)

# track_a_depth/ is now a local copy inside this folder (see docstring
# above) so this pipeline has no dependency on anything outside it -- e.g.
# for a self-contained Docker build. It and dsm_calibration/ have
# plain-identifier module names, so a normal sys.path + import works for them.
sys.path.insert(0, str(BASE_DIR / "track_a_depth"))
sys.path.insert(0, str(DSM_CAL_DIR))
# qgis_prep/'s own scripts are numbered (03_extract_metadata.py etc) so
# they aren't valid Python identifiers to `import` directly -- load them
# by file path instead. Still need qgis_prep/ on sys.path too, since
# 04_fetch_srtm.py itself does `from aoi_config import AOI_BBOX`.
sys.path.insert(0, str(QGIS_PREP_DIR))


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract_metadata_mod = _load_module("extract_metadata_mod", QGIS_PREP_DIR / "03_extract_metadata.py")
fetch_srtm_mod = _load_module("fetch_srtm_mod", QGIS_PREP_DIR / "04_fetch_srtm.py")

from geotiff_to_viewer_assets import generate_viewer_assets  # noqa: E402
from srtm_calibration import relative_depth_to_dsm_from_geotiff  # noqa: E402

JOBS_DIR_NAME = os.environ.get("PIPELINE_JOBS_DIR", "jobs_meta")
JOBS_STORE_PATH = BASE_DIR / f"{JOBS_DIR_NAME}_store.json"
(BASE_DIR / JOBS_DIR_NAME).mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from depth_estimator import ensure_checkpoint
    ensure_checkpoint("vits")
    yield


app = FastAPI(title="Georeferenced DSM Pipeline", lifespan=lifespan)

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


@app.get("/api/health")
def health():
    return {"message": "Georeferenced DSM pipeline is running"}


@app.post("/jobs")
async def create_job(
    geotiff: UploadFile = File(..., description="An already-cropped GeoTIFF -- see README.md"),
):
    job_id = str(uuid4())
    jobs[job_id] = {"id": job_id, "status": "running", "error": None, "metrics": None,
                     "view_urls": None}
    _save_jobs()

    try:
        # 1. Save the upload as this MVP's single current AOI (overwrite,
        #    same synchronous single-job pattern as track_c_api).
        with open(CROPPED_PATH, "wb") as f:
            shutil.copyfileobj(geotiff.file, f)

        # 2. Real embedded bounds, straight from the upload -- not assumed.
        meta = extract_metadata_mod.extract_metadata(
            src_path=str(CROPPED_PATH), out_path=str(META_PATH)
        )

        # 3. SRTM tile matching those exact bounds (reads them back from
        #    META_PATH we just wrote -- see 04_fetch_srtm.py's
        #    get_aoi_bounds_4326()).
        fetch_srtm_mod.fetch_srtm_for_aoi(cropped_path=str(CROPPED_PATH))

        # 4. Real depth estimation on the uploaded GeoTIFF's RGB bands.
        from depth_estimator import DepthPipeline
        depth_pipeline = DepthPipeline(encoder="vits")
        depth_pipeline.process_image(str(CROPPED_PATH), str(REAL_RUN_DIR))

        # 5. Calibrate relative depth -> absolute elevation against SRTM.
        dsm, slope, intercept, metrics = relative_depth_to_dsm_from_geotiff(
            str(REAL_RUN_DIR / "depth.npy"),
            str(CROPPED_PATH),
            str(SRTM_ALIGNED_PATH),
            str(DSM_OUTPUT_PATH),
            n_samples=2000,
            robust=True,
        )

        # 6. Convert to what the viewers actually consume (PNG/JPG + real
        #    min/max metadata -- browsers can't load .tif as a texture).
        generate_viewer_assets(
            dsm_path=str(DSM_OUTPUT_PATH),
            satellite_path=str(CROPPED_PATH),
            assets_dir=str(VIZ_DIR / "assets"),
        )

        jobs[job_id].update(
            status="done",
            metrics={"slope": slope, "intercept": intercept, **metrics},
            view_urls={
                "context_map": "/leaflet_pitch/index.html",
                "terrain_3d": "/3d_visualization/index.html",
            },
            bounds_epsg4326=meta["bounds_epsg4326"],
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


# Serves qgis_prep/data/, leaflet_pitch/, 3d_visualization/, dsm_calibration/
# all at their existing relative paths to each other -- those pages already
# use paths like ../qgis_prep/data/geo_metadata.json and
# ../3d_visualization/index.html, so mounting this whole directory as one
# static root (instead of separate mounts per subfolder) is what makes
# those existing, already-tested relative paths keep working unchanged.
# Registered last so it never shadows /jobs* or /api/* above.
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="pipeline_static")
