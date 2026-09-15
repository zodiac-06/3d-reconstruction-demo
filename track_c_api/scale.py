import json, os
import numpy as np

def mesh_bbox_diagonal(obj_path):
    verts = []
    with open(obj_path) as f:
        for line in f:
            if line.startswith("v "):
                _, x, y, z = line.split()
                verts.append((float(x), float(y), float(z)))
    verts = np.array(verts)
    mins, maxs = verts.min(axis=0), verts.max(axis=0)
    return float(np.linalg.norm(maxs - mins)), mins.tolist(), maxs.tolist()

def compute_scale(job_dir, reference_length_m=1.0, reference_method="bbox_diagonal_mock"):
    obj_path = os.path.join(job_dir, "mesh.obj")
    measured, mins, maxs = mesh_bbox_diagonal(obj_path)
    scale_factor = reference_length_m / max(measured, 1e-9)
    scale = {"scale_factor": scale_factor, "reference_method": reference_method, "units": "meters"}
    with open(os.path.join(job_dir, "scale.json"), "w") as f:
        json.dump(scale, f, indent=2)
    dims_m = [(maxs[i] - mins[i]) * scale_factor for i in range(3)]
    return scale, dims_m
