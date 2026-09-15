# reconstruct.py — Track B: depth+intrinsics -> pointcloud.ply + mesh.obj
import json, os
import numpy as np

def backproject(depth, intrinsics, stride=2, max_depth_jump=0.35):
    fx, fy = intrinsics["fx"], intrinsics["fy"]
    cx, cy = intrinsics["cx"], intrinsics["cy"]
    h, w = depth.shape
    vs, us = np.arange(0, h, stride), np.arange(0, w, stride)
    gv, gu = np.meshgrid(vs, us, indexing="ij")
    d = depth[gv, gu]
    X, Y, Z = (gu - cx) * d / fx, (gv - cy) * d / fy, d
    points = np.stack([X, Y, Z], axis=-1)
    dn = (d - d.min()) / max(d.max() - d.min(), 1e-6)
    colors = np.stack([255*(1-dn), 255*0.4*np.ones_like(dn), 255*dn], axis=-1).astype(np.uint8)
    return points, colors

def write_ply(path, points, colors):
    pts, cols = points.reshape(-1,3), colors.reshape(-1,3)
    with open(path, "w") as f:
        f.write(f"ply\nformat ascii 1.0\nelement vertex {len(pts)}\n"
                "property float x\nproperty float y\nproperty float z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        for (x,y,z),(r,g,b) in zip(pts, cols):
            f.write(f"{x:.5f} {y:.5f} {z:.5f} {int(r)} {int(g)} {int(b)}\n")

def write_mesh_obj(path, points, max_depth_jump=0.35):
    rows, cols, _ = points.shape
    verts, z = points.reshape(-1,3), points[:,:,2]
    vidx = lambda r,c: r*cols + c + 1
    faces = []
    for r in range(rows-1):
        for c in range(cols-1):
            zs = [z[r,c], z[r,c+1], z[r+1,c], z[r+1,c+1]]
            if max(zs)-min(zs) > max_depth_jump:
                continue  # skip discontinuities so fg doesn't smear into bg
            a,b,cc,d = vidx(r,c), vidx(r,c+1), vidx(r+1,c), vidx(r+1,c+1)
            faces += [(a,b,cc), (b,d,cc)]
    with open(path, "w") as f:
        f.write("# grid-triangulated from depth back-projection\n")
        for x,y,zc in verts: f.write(f"v {x:.5f} {y:.5f} {zc:.5f}\n")
        for a,b,c in faces: f.write(f"f {a} {b} {c}\n")
    return len(verts), len(faces)

def run(job_dir, stride=2):
    depth = np.load(os.path.join(job_dir, "depth.npy"))
    intrinsics = json.load(open(os.path.join(job_dir, "intrinsics.json")))
    points, colors = backproject(depth, intrinsics, stride=stride)
    write_ply(os.path.join(job_dir, "pointcloud.ply"), points, colors)
    return write_mesh_obj(os.path.join(job_dir, "mesh.obj"), points)
