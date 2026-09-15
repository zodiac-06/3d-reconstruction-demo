# Generates a synthetic depth.npy + intrinsics.json mock fixture matching
# 00_shared_contract.md §2/§4: a smooth gradient with a few depth "blobs",
# in the exact contract shapes, for tracks to develop against before a
# real depth model / real photo is available.
import json
import os

import numpy as np


def generate_depth(width, height):
    """Smooth left-to-right gradient (near -> far) with 3 synthetic blobs
    of distinct depth, matching Depth Anything V2's native (H, W) float32
    output shape (relative depth, not yet scaled)."""
    x = np.linspace(0.5, 5.0, width, dtype=np.float32)
    depth = np.tile(x, (height, 1))

    blobs = [
        (int(width * 0.25), int(height * 0.35), min(width, height) * 0.12, 1.0),
        (int(width * 0.55), int(height * 0.60), min(width, height) * 0.15, 3.5),
        (int(width * 0.80), int(height * 0.25), min(width, height) * 0.10, 0.6),
    ]
    yy, xx = np.mgrid[0:height, 0:width]
    for cx, cy, radius, blob_depth in blobs:
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2
        depth[mask] = blob_depth

    return depth.astype(np.float32)


def generate_intrinsics(width, height, fov_deg=65.0):
    fov_rad = np.radians(fov_deg)
    focal_length = (width / 2.0) / np.tan(fov_rad / 2.0)
    return {
        "fx": float(focal_length),
        "fy": float(focal_length),
        "cx": float(width / 2.0),
        "cy": float(height / 2.0),
        "width": int(width),
        "height": int(height),
        "estimated_fov_deg": float(fov_deg),
    }


def main(job_dir, width=320, height=240):
    os.makedirs(job_dir, exist_ok=True)
    depth = generate_depth(width, height)
    np.save(os.path.join(job_dir, "depth.npy"), depth)
    with open(os.path.join(job_dir, "intrinsics.json"), "w") as f:
        json.dump(generate_intrinsics(width, height), f, indent=4)
    print(f"Wrote mock depth.npy {depth.shape} {depth.dtype} + intrinsics.json to {job_dir}")


if __name__ == "__main__":
    main(os.path.join(os.path.dirname(__file__), "job_0001"))
