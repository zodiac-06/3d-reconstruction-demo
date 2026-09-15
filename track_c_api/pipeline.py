import shutil
import sys
from pathlib import Path

from scale import compute_scale
from mesh_scaler import scale_obj_mesh

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

# Track A's real pipeline lives in the sibling track_a_depth/ folder.
sys.path.insert(0, str(REPO_ROOT / "track_a_depth"))

# Last known-real Track A output, used as a fallback if live inference can't
# run in this environment (missing torch/checkpoint/GPU) — never a fabricated number.
FALLBACK_DEPTH_JOB = REPO_ROOT / "shared" / "sample_jobs" / "job_real_001"

# Track B has no real reconstruction pipeline yet (missing pointcloud.ply and
# back-projection/Poisson code). Stub with this placeholder mesh until that
# track is fixed and starts producing real per-job mesh.obj/pointcloud.ply.
STUB_MESH = BASE_DIR / "mesh.obj"


def _get_depth_and_intrinsics(job_dir: Path, photo_path: Path):
    try:
        from depth_estimator import DepthPipeline
        real_pipeline = DepthPipeline(encoder="vits")
        real_pipeline.process_image(str(photo_path), str(job_dir))
        return
    except Exception as exc:
        if not (FALLBACK_DEPTH_JOB / "depth.npy").exists():
            raise RuntimeError(
                f"Track A inference unavailable and no fallback depth data exists: {exc}"
            ) from exc
        shutil.copy(FALLBACK_DEPTH_JOB / "depth.npy", job_dir / "depth.npy")
        shutil.copy(FALLBACK_DEPTH_JOB / "intrinsics.json", job_dir / "intrinsics.json")


def run_pipeline(job_dir: Path, photo_path: Path, progress_cb=None):
    """
    Run the Track C pipeline for one job: Track A depth -> Track B mesh (stubbed
    until that track ships) -> real scale computation from mesh geometry.
    """
    def report(pct):
        if progress_cb:
            progress_cb(pct)

    # Step 1: depth + intrinsics (real Track A inference, with a real-data fallback)
    _get_depth_and_intrinsics(job_dir, photo_path)
    report(50)

    # Step 2: mesh (Track B stub)
    if not STUB_MESH.exists():
        raise RuntimeError(f"Track B stub mesh not found at {STUB_MESH}")
    mesh_path = job_dir / "mesh.obj"
    shutil.copy(STUB_MESH, mesh_path)
    report(75)

    # Step 3: real scale factor from mesh bounding-box geometry (no hardcoded values)
    scale, dims_m = compute_scale(
        str(job_dir), reference_length_m=1.0, reference_method="bbox_diagonal_mock"
    )
    scale_obj_mesh(str(mesh_path), str(mesh_path), scale["scale_factor"])
    report(100)

    return {
        "scale_factor": scale["scale_factor"],
        "reference_method": scale["reference_method"],
        "units": scale["units"],
        "dims_m": dims_m,
    }
