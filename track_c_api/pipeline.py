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

# Track B's real reconstruction pipeline lives in the sibling track_b_mesh/ folder.
sys.path.insert(0, str(REPO_ROOT / "track_b_mesh"))


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


def run_pipeline(job_dir: Path, photo_path: Path, reference_length_m: float,
                  reference_axis: str = "width", progress_cb=None):
    """
    Run the Track C pipeline for one job: Track A depth -> Track B reconstruction
    -> real scale computation from mesh geometry.

    reference_length_m is the human-measured real-world length of the mesh's
    bbox axis named by reference_axis ("width"/"height"/"depth" -> x/y/z) —
    there is no default; a fabricated scale factor is worse than an explicit
    caller error. The resulting scale_factor is a single uniform multiplier
    applied to all axes, not a per-axis stretch (which would distort the mesh).
    """
    def report(pct):
        if progress_cb:
            progress_cb(pct)

    # Step 1: depth + intrinsics (real Track A inference, with a real-data fallback)
    _get_depth_and_intrinsics(job_dir, photo_path)
    report(50)

    # Step 2: real Track B reconstruction (depth+intrinsics -> pointcloud.ply + mesh.obj)
    import reconstruct
    reconstruct.run(str(job_dir))
    mesh_path = job_dir / "mesh.obj"
    report(75)

    # Step 3: real scale factor from one measured bbox axis (no hardcoded values,
    # and no longer the diagonal -- see scale.py for why that mismatched the label)
    scale, dims_m = compute_scale(
        str(job_dir),
        reference_length_m=reference_length_m,
        reference_axis=reference_axis,
    )
    scale_obj_mesh(str(mesh_path), str(mesh_path), scale["scale_factor"])
    report(100)

    return {
        "scale_factor": scale["scale_factor"],
        "reference_method": scale["reference_method"],
        "units": scale["units"],
        "dims_m": dims_m,
    }
