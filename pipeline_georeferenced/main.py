"""
Georeferenced pipeline: GeoTIFF for an area of interest -> real absolute-
elevation DSM (terrain DEM + Depth Anything detail layer; Copernicus GLO-30
by default), viewable in both the 2D context map and the 3D terrain
flythrough.

Three ways to supply the AOI image to POST /jobs (see _resolve_input_mode):

  cropped          `geotiff` only -- an already-cropped GeoTIFF, used as-is.
                   The original mode; unchanged.
  upload_and_crop  `geotiff` + bounds (west/south/east/north, EPSG:4326) --
                   a larger scene, cropped server-side by
                   qgis_prep/02_crop_geotiff.py's crop_geotiff().
  fetch_by_bounds  bounds only -- the server finds the least-cloudy
                   Sentinel-2 L2A scene via Earth Search STAC and
                   windowed-reads it (qgis_prep/01_fetch_geotiff.py's
                   find_scene + download_cropped, with the same buffer 01
                   uses), then crops to the exact bounds with crop_geotiff().

All three only differ in how data/aoi_cropped.tif gets written; everything
after that (_run_pipeline: metadata -> DEM -> depth -> fusion -> viewer
assets) is one shared code path.

Reuses, doesn't reimplement: track_a_depth/depth_estimator.py (a local
copy of the repo-root track_a_depth/, including checkpoints/, so this
whole folder is self-contained for a Docker build -- code unchanged),
qgis_prep/01_fetch_geotiff.py, 02_crop_geotiff.py, 03_extract_metadata.py
and 04_fetch_srtm.py (given callable entry points, CLI behavior unchanged), dsm_calibration/srtm_calibration.py and
geotiff_to_viewer_assets.py. This file is the orchestration glue between
them, not a reimplementation of any of them.

Terrain DEM choice: the DEM_SOURCE env var sets the server default
(copernicus | srtm | nasadem | fabdem; default copernicus), and a
`dem_source` form field on POST /jobs overrides it per upload. Either way the
chosen source is tried first, then Copernicus, then SRTM, then NASADEM (see
04_fetch_srtm.py's terrain_dem_sources) -- FABDEM is only used when asked
for, since it's CC BY-NC-SA 4.0 (non-commercial).
"""
import importlib.util
import json
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

QGIS_PREP_DIR = BASE_DIR / "qgis_prep"
DSM_CAL_DIR = BASE_DIR / "dsm_calibration"
VIZ_DIR = BASE_DIR / "3d_visualization"

DATA_DIR = QGIS_PREP_DIR / "data"
CROPPED_PATH = DATA_DIR / "aoi_cropped.tif"
# Uncropped sources for the two bounds-based input modes. Deliberately not
# data/raw_source.tif, which is the hand-prepared Nainital scene 01 writes.
UPLOAD_SOURCE_PATH = DATA_DIR / "upload_source.tif"
STAC_SOURCE_PATH = DATA_DIR / "stac_source.tif"
META_PATH = DATA_DIR / "geo_metadata.json"
TERRAIN_DEM_PATH = DATA_DIR / "terrain_dem.tif"
TERRAIN_DEM_ALIGNED_PATH = DATA_DIR / "terrain_dem_aligned.tif"
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


fetch_geotiff_mod = _load_module("fetch_geotiff_mod", QGIS_PREP_DIR / "01_fetch_geotiff.py")
crop_geotiff_mod = _load_module("crop_geotiff_mod", QGIS_PREP_DIR / "02_crop_geotiff.py")
extract_metadata_mod = _load_module("extract_metadata_mod", QGIS_PREP_DIR / "03_extract_metadata.py")
fetch_srtm_mod = _load_module("fetch_srtm_mod", QGIS_PREP_DIR / "04_fetch_srtm.py")
dem_court_mod = _load_module("dem_court_mod", BASE_DIR / "dem_court" / "court.py")
evidence_mod = _load_module("evidence_mod", BASE_DIR / "validation" / "evidence.py")

from aoi_config import SOURCE_BUFFER_DEG  # noqa: E402
from geotiff_to_viewer_assets import generate_viewer_assets  # noqa: E402
from srtm_calibration import terrain_plus_detail_dsm_from_geotiff  # noqa: E402

DEM_SOURCE = os.environ.get("DEM_SOURCE", fetch_srtm_mod.DEFAULT_TERRAIN_DEM_SOURCE)
fetch_srtm_mod.terrain_dem_sources(DEM_SOURCE)  # fail fast on a bad env value

# Fixed seed for the Theil-Sen point sampling, so the same upload always
# produces the same DSM (and API output can be diffed against a standalone run).
CALIBRATION_SEED = 0

# Largest AOI accepted for the bounds-based modes, per side, in degrees
# (~55km). Everything runs synchronously in one request, and Depth
# Anything / the DEM fetch scale with area -- this keeps a typo'd bbox from
# tying the server up for minutes or pulling a whole Sentinel-2 tile.
MAX_BOUNDS_SIDE_DEG = 0.5

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


def _tick(timings, stage):
    """Close the currently running stage (if any) and start `stage`."""
    now = time.perf_counter()
    running = timings.pop("_running", None)
    if running:
        timings[running[0]] = round(now - running[1], 2)
    if stage:
        timings["_running"] = (stage, now)


def _parse_bounds(west, south, east, north):
    """None if no bounds were given; else a validated (w, s, e, n) tuple."""
    values = (west, south, east, north)
    if all(v is None for v in values):
        return None
    if any(v is None for v in values):
        raise HTTPException(status_code=400, detail="Give all four bounds: west, south, east, north")
    w, s, e, n = values
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bounds {values}: need -180 <= west < east <= 180 and "
                   f"-90 <= south < north <= 90 (EPSG:4326 degrees)")
    if e - w > MAX_BOUNDS_SIDE_DEG or n - s > MAX_BOUNDS_SIDE_DEG:
        raise HTTPException(
            status_code=400,
            detail=f"AOI too large ({e - w:.3f} x {n - s:.3f} deg); max "
                   f"{MAX_BOUNDS_SIDE_DEG} deg per side")
    return (w, s, e, n)


def _resolve_input_mode(geotiff, bounds):
    has_file = geotiff is not None and bool(geotiff.filename)
    if has_file and bounds is None:
        return "cropped"
    if has_file:
        return "upload_and_crop"
    if bounds is not None:
        return "fetch_by_bounds"
    raise HTTPException(
        status_code=400,
        detail="Send a GeoTIFF, bounds (west/south/east/north), or both -- see README.md")


def _prepare_cropped_input(mode, geotiff, bounds, timings):
    """Write data/aoi_cropped.tif for the given input mode. Returns a dict
    describing where it came from (stored on the job)."""
    if mode == "cropped":
        # Save the upload as this MVP's single current AOI (overwrite, same
        # synchronous single-job pattern as track_c_api).
        _tick(timings, "save_upload")
        with open(CROPPED_PATH, "wb") as f:
            shutil.copyfileobj(geotiff.file, f)
        return {"filename": geotiff.filename}

    if mode == "upload_and_crop":
        _tick(timings, "save_upload")
        with open(UPLOAD_SOURCE_PATH, "wb") as f:
            shutil.copyfileobj(geotiff.file, f)
        _tick(timings, "crop")
        width, height = crop_geotiff_mod.crop_geotiff(
            src_path=str(UPLOAD_SOURCE_PATH), out_path=str(CROPPED_PATH), bbox=bounds)
        return {"filename": geotiff.filename, "requested_bounds": list(bounds),
                "cropped_size_px": [width, height]}

    # fetch_by_bounds: same buffered-scene-then-crop flow as running
    # 01_fetch_geotiff.py then 02_crop_geotiff.py by hand.
    w, s, e, n = bounds
    b = SOURCE_BUFFER_DEG
    buffered = (w - b, s - b, e + b, n + b)
    _tick(timings, "stac_search")
    scene = fetch_geotiff_mod.find_scene(buffered)
    _tick(timings, "stac_download")
    fetch_geotiff_mod.download_cropped(scene, buffered, out_path=str(STAC_SOURCE_PATH))
    _tick(timings, "crop")
    width, height = crop_geotiff_mod.crop_geotiff(
        src_path=str(STAC_SOURCE_PATH), out_path=str(CROPPED_PATH), bbox=bounds)
    return {
        "requested_bounds": list(bounds),
        "cropped_size_px": [width, height],
        "stac_scene_id": scene["id"],
        "scene_datetime": scene["properties"].get("datetime"),
        "scene_cloud_cover_pct": scene["properties"].get("eo:cloud_cover"),
    }


def _run_pipeline(dem_source, sources, timings):
    """Everything downstream of data/aoi_cropped.tif -- identical for all
    three input modes."""
    _tick(timings, "metadata")
    # 2. Real embedded bounds, straight from the cropped GeoTIFF -- not assumed.
    meta = extract_metadata_mod.extract_metadata(
        src_path=str(CROPPED_PATH), out_path=str(META_PATH)
    )

    _tick(timings, "dem_fetch")
    # 3. Terrain DEM for those exact bounds (reads them back from
    #    META_PATH we just wrote -- see 04_fetch_srtm.py's
    #    get_aoi_bounds_4326()), preferred source first, then fallbacks.
    dem = fetch_srtm_mod.fetch_terrain_dem_for_aoi(
        cropped_path=str(CROPPED_PATH), sources=sources,
        dem_path=str(TERRAIN_DEM_PATH), aligned_path=str(TERRAIN_DEM_ALIGNED_PATH),
    )

    _tick(timings, "depth_inference")
    # 4. Real depth estimation on the cropped GeoTIFF's RGB bands.
    from depth_estimator import DepthPipeline
    depth_pipeline = DepthPipeline(encoder="vits")
    depth_pipeline.process_image(str(CROPPED_PATH), str(REAL_RUN_DIR))

    _tick(timings, "fusion")
    # 5. DSM = terrain DEM + s * Depth Anything detail layer. The
    #    Theil-Sen fit (relative depth -> DEM) inside puts the detail in
    #    meters; its held-out metrics are a consistency check between
    #    Depth Anything and the DEM, NOT the DSM's accuracy -- the DSM's
    #    elevations come from the DEM (see README, "Terrain + detail
    #    fusion", for its accuracy against ICESat-2 lidar).
    dsm, info = terrain_plus_detail_dsm_from_geotiff(
        str(REAL_RUN_DIR / "depth.npy"),
        str(CROPPED_PATH),
        str(TERRAIN_DEM_PATH),
        str(DSM_OUTPUT_PATH),
        n_samples=2000,
        rng=np.random.default_rng(CALIBRATION_SEED),
    )

    _tick(timings, "viewer_assets")
    # 6. Convert to what the viewers actually consume (PNG/JPG + real
    #    min/max metadata -- browsers can't load .tif as a texture).
    generate_viewer_assets(
        dsm_path=str(DSM_OUTPUT_PATH),
        satellite_path=str(CROPPED_PATH),
        assets_dir=str(VIZ_DIR / "assets"),
    )
    _tick(timings, None)

    return dict(
        metrics={
            "method": "terrain_plus_detail",
            "dem_source": dem["source"],
            "dem_requested": dem_source,
            "dem_fallback_errors": dem["failures"],
            "vertical_datum": dem["vertical_datum"],
            "s": info["s"],
            "offset": info["offset"],
            "detail_sigma_m": info["detail_sigma_m"],
            "detail_std_m": info["detail_std_m"],
            # Theil-Sen consistency check (Depth Anything vs. DEM, held out):
            "slope": info["theil_sen_slope"],
            "intercept": info["theil_sen_intercept"],
            **info["theil_sen_heldout"],
            "corr_fused_vs_theil_sen_dsm": info["corr_fused_vs_theil_sen_dsm"],
        },
        view_urls={
            "context_map": "/leaflet_pitch/index.html",
            "terrain_3d": "/3d_visualization/index.html",
        },
        bounds_epsg4326=meta["bounds_epsg4326"],
    )


@app.post("/jobs")
async def create_job(
    geotiff: UploadFile | None = File(
        None, description="A GeoTIFF: already cropped (no bounds), or a larger scene to crop "
                          "to the bounds -- see README.md"),
    west: float | None = Form(None, description="AOI bounds, EPSG:4326 degrees"),
    south: float | None = Form(None),
    east: float | None = Form(None),
    north: float | None = Form(None),
    dem_source: str | None = Form(
        None, description="Preferred terrain DEM: copernicus (default), srtm, nasadem, or fabdem"),
):
    dem_source = dem_source or DEM_SOURCE
    try:
        sources = fetch_srtm_mod.terrain_dem_sources(dem_source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bounds = _parse_bounds(west, south, east, north)
    mode = _resolve_input_mode(geotiff, bounds)

    job_id = str(uuid4())
    jobs[job_id] = {"id": job_id, "status": "running", "error": None, "metrics": None,
                     "view_urls": None, "input_mode": mode,
                     "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _save_jobs()

    timings = {}
    started = time.perf_counter()
    try:
        jobs[job_id]["input"] = _prepare_cropped_input(mode, geotiff, bounds, timings)
        jobs[job_id].update(status="done", **_run_pipeline(dem_source, sources, timings))
    except Exception as exc:
        _tick(timings, None)
        jobs[job_id].update(status="failed", error=str(exc))
    if jobs[job_id]["status"] == "done":
        jobs[job_id]["validation"] = _save_validation(job_id, jobs[job_id]["metrics"]["vertical_datum"])
        jobs[job_id]["dsm_download"] = _save_job_dsm(job_id, jobs[job_id]["metrics"])
    timings["total"] = round(time.perf_counter() - started, 2)
    jobs[job_id]["timings_s"] = timings

    _save_jobs()
    return jobs[job_id]


def _dsm_path(job_id):
    return BASE_DIR / JOBS_DIR_NAME / f"{job_id}_dsm.tif"


def _save_job_dsm(job_id, metrics):
    """Keep this job's DSM (the pipeline overwrites output_dsm.tif on every
    job) and tag it so the file says what its heights are: without the
    vertical datum a downloaded DSM is ambiguous by metres (Copernicus's
    EGM2008 and SRTM's EGM96 differ by ~3 m at Nainital)."""
    import rasterio

    path = _dsm_path(job_id)
    shutil.copyfile(DSM_OUTPUT_PATH, path)
    with rasterio.open(path, "r+") as ds:
        ds.update_tags(
            UNITS="metre",
            VERTICAL_DATUM=metrics["vertical_datum"],
            DEM_SOURCE=metrics["dem_source"],
            METHOD="DEM + Depth Anything V2 high-pass detail layer (s = 1, 30 m)",
            PRODUCER="DepthWizard georeferenced pipeline",
            JOB_ID=job_id,
        )
        ds.set_band_description(1, f"surface elevation (m, {metrics['vertical_datum']})")
    return f"/jobs/{job_id}/dsm.tif"


def _truth_dirs():
    """Where ICESat-2 evaluation runs live (same search as DEM Court)."""
    return sorted(str(d) for d in DSM_CAL_DIR.iterdir() if d.is_dir())


def _validation_path(job_id):
    return BASE_DIR / JOBS_DIR_NAME / f"{job_id}_validation.json"


def _save_validation(job_id, vertical_datum):
    """Score this job's DSM against ICESat-2, if truth exists for its grid.
    The per-point evidence goes to its own file (it can be thousands of
    points); the job keeps only the summary. A scoring failure is recorded,
    not raised -- the DSM itself is still good."""
    try:
        evidence = evidence_mod.build_evidence(str(DSM_OUTPUT_PATH), vertical_datum, _truth_dirs())
    except Exception as exc:
        return {"available": False, "reason": f"validation failed: {exc}"}
    if evidence is None:
        return {"available": False,
                "reason": "no ICESat-2 truth for this AOI's grid (see dsm_calibration/fetch_icesat2_truth.py)"}
    with open(_validation_path(job_id), "w") as f:
        json.dump(evidence, f)
    return {"available": True, **{k: evidence[k] for k in
                                  ("source_run", "vertical_datum", "n_segments", "n_on_dsm", "stats")}}


@app.get("/jobs")
def list_jobs():
    """Every job, newest first, as the summary the history page needs."""
    keys = ("id", "status", "created_at", "input_mode", "error", "bounds_epsg4326", "dsm_download")
    out = []
    for order, job in enumerate(jobs.values()):  # insertion order breaks same-second ties
        m = job.get("metrics") or {}
        v = job.get("validation") or {}
        canopy = (v.get("stats") or {}).get("canopy_top") or {}
        out.append({**{k: job.get(k) for k in keys},
                    "dem_source": m.get("dem_source"), "vertical_datum": m.get("vertical_datum"),
                    "validation_rmse_canopy_top_m": canopy.get("rmse") if v.get("available") else None,
                    "validation_n": canopy.get("n") if v.get("available") else None,
                    "_order": order})
    # jobs from before created_at existed sort last
    out.sort(key=lambda j: (j["created_at"] or "", j.pop("_order")), reverse=True)
    return out


@app.get("/jobs/{job_id}/dsm.tif")
def download_dsm(job_id: str):
    """This job's DSM as a GeoTIFF, tagged with units, vertical datum and DEM source."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _dsm_path(job_id).exists():
        raise HTTPException(status_code=409, detail="No DSM kept for this job (failed, or run before downloads existed)")
    return FileResponse(_dsm_path(job_id), media_type="image/tiff",
                        filename=f"depthwizard_dsm_{job_id[:8]}.tif")


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/jobs/{job_id}/validation")
def get_validation(job_id: str):
    """Per-point ICESat-2 evidence for the job's DSM (validation/evidence.py)."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    summary = job.get("validation") or {}
    if not summary.get("available") or not _validation_path(job_id).exists():
        raise HTTPException(status_code=409, detail=summary.get("reason", "Job has no validation evidence"))
    with open(_validation_path(job_id)) as f:
        return dict(json.load(f), job_id=job_id)


@app.get("/jobs/{job_id}/dem-court")
def get_dem_court(job_id: str, fabdem: bool = False):
    """Every terrain DEM source for the job's AOI on its pixel grid, in one
    vertical datum (see dem_court/court.py). Read-only: doesn't change the
    job, the pipeline's DEM, or the fallback order. Cached per AOI."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "done" or not job.get("bounds_epsg4326"):
        raise HTTPException(status_code=409, detail="Job has no finished AOI")
    b = job["bounds_epsg4326"]
    try:
        manifest, _ = dem_court_mod.build_court(
            (b["west"], b["south"], b["east"], b["north"]), fetch_srtm_mod,
            current_crop=str(CROPPED_PATH), include_fabdem=fabdem,
            icesat_dirs=sorted(str(d) for d in DSM_CAL_DIR.iterdir() if d.is_dir()))
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return dict(manifest, job_id=job_id)


# Serves qgis_prep/data/, leaflet_pitch/, 3d_visualization/, dsm_calibration/
# all at their existing relative paths to each other -- those pages already
# use paths like ../qgis_prep/data/geo_metadata.json and
# ../3d_visualization/index.html, so mounting this whole directory as one
# static root (instead of separate mounts per subfolder) is what makes
# those existing, already-tested relative paths keep working unchanged.
# Registered last so it never shadows /jobs* or /api/* above.
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="pipeline_static")
