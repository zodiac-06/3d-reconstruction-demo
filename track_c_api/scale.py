import json, os
import numpy as np

# x/y/z map to width/height/depth per reconstruct.py's back-projection:
# X = horizontal (width), Y = vertical (height), Z = depth-from-camera.
AXIS_INDEX = {"width": 0, "height": 1, "depth": 2}
AXIS_LABEL = {"width": "bbox_width_x", "height": "bbox_height_y", "depth": "bbox_depth_z"}

def mesh_bbox_size(obj_path):
    verts = []
    with open(obj_path) as f:
        for line in f:
            if line.startswith("v "):
                _, x, y, z = line.split()
                verts.append((float(x), float(y), float(z)))
    verts = np.array(verts)
    mins, maxs = verts.min(axis=0), verts.max(axis=0)
    return (maxs - mins), mins.tolist(), maxs.tolist()

def compute_scale(job_dir, reference_length_m, reference_axis="width"):
    if reference_axis not in AXIS_INDEX:
        raise ValueError(f"reference_axis must be one of {list(AXIS_INDEX)}, got {reference_axis!r}")

    obj_path = os.path.join(job_dir, "mesh.obj")
    size, mins, maxs = mesh_bbox_size(obj_path)
    measured = size[AXIS_INDEX[reference_axis]]

    # Uniform scale factor applied to all axes -- a single scalar derived from
    # one measured axis, not a per-axis stretch (which would distort the mesh).
    scale_factor = reference_length_m / max(measured, 1e-9)
    reference_method = f"{AXIS_LABEL[reference_axis]}:{reference_length_m}m"

    scale = {"scale_factor": scale_factor, "reference_method": reference_method, "units": "meters"}
    with open(os.path.join(job_dir, "scale.json"), "w") as f:
        json.dump(scale, f, indent=2)

    dims_m = [size[i] * scale_factor for i in range(3)]
    return scale, dims_m
