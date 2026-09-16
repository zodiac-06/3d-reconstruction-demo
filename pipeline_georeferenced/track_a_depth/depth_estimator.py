import cv2
import torch
import numpy as np
import json
import os
import math
import sys

# Add the cloned repo to the path so we can import its architecture
sys.path.append(os.path.join(os.path.dirname(__file__), 'Depth-Anything-V2'))
from depth_anything_v2.dpt import DepthAnythingV2

# Official Depth-Anything-V2 Hugging Face repos, one per encoder size.
# Verified via https://huggingface.co/depth-anything/Depth-Anything-V2-Small
# (depth_anything_v2_vits.pth, 99.2MB) -- same file this repo already has
# checked out locally as depth_anything_v2_vits.pth.1.
HF_REPO_BY_ENCODER = {
    'vits': 'depth-anything/Depth-Anything-V2-Small',
    'vitb': 'depth-anything/Depth-Anything-V2-Base',
    'vitl': 'depth-anything/Depth-Anything-V2-Large',
}


def ensure_checkpoint(encoder='vits'):
    """Download the checkpoint from the official Hugging Face repo if it isn't
    already present locally. Deploy targets like Render check out this repo
    fresh and won't have the (gitignored-by-size) .pth file, so this needs to
    run once before the server starts accepting requests, not lazily on the
    first request (which would make that request slow and racy under
    concurrent startup traffic)."""
    checkpoints_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    os.makedirs(checkpoints_dir, exist_ok=True)
    filename = f'depth_anything_v2_{encoder}.pth'
    candidates = [
        os.path.join(checkpoints_dir, filename),
        os.path.join(checkpoints_dir, filename + '.1'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path

    if encoder not in HF_REPO_BY_ENCODER:
        raise ValueError(f"No known Hugging Face repo for encoder '{encoder}'")

    from huggingface_hub import hf_hub_download
    repo_id = HF_REPO_BY_ENCODER[encoder]
    print(f"Checkpoint not found locally; downloading {filename} from "
          f"https://huggingface.co/{repo_id} ...")
    downloaded_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=checkpoints_dir,
    )
    print(f"Checkpoint downloaded to {downloaded_path}")
    return downloaded_path


class DepthPipeline:
    def __init__(self, encoder='vits', fov_deg=65.0):
        # Fall back to CPU automatically for Kali
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.fov_deg = fov_deg
        self.model_path = self._resolve_checkpoint_path(encoder)

        print(f"Loading Depth Anything V2 ({encoder}) on {self.device}...")
        
        # Model configuration specific to the downloaded checkpoint
        model_configs = {
            'vits': {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]},
            'vitb': {'encoder': 'vitb', 'features': 128, 'out_channels': [96, 192, 384, 768]}
        }
        
        self.model = DepthAnythingV2(**model_configs[encoder])
        
        # Load the downloaded weights
        state_dict = torch.load(self.model_path, map_location=self.device)
        self.model.load_state_dict(state_dict)
        self.model = self.model.to(self.device).eval()

    def _resolve_checkpoint_path(self, encoder):
        """Locate the checkpoint relative to this file (not the caller's cwd),
        downloading it from Hugging Face if it's missing (e.g. running the
        standalone script directly on a fresh checkout without the API's
        startup hook having already ensured it)."""
        return ensure_checkpoint(encoder)

    def estimate_intrinsics(self, width, height):
        """Calculates pinhole camera intrinsics based on an assumed FOV."""
        fov_rad = math.radians(self.fov_deg)
        focal_length = (width / 2.0) / math.tan(fov_rad / 2.0)
        
        return {
            "fx": float(focal_length),
            "fy": float(focal_length),
            "cx": float(width / 2.0),
            "cy": float(height / 2.0),
            "width": int(width),
            "height": int(height),
            "estimated_fov_deg": float(self.fov_deg)
        }

    def process_image(self, image_path, job_dir):
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Input image not found: {image_path}")
            
        os.makedirs(job_dir, exist_ok=True)
        
        # 1. Load image using OpenCV
        raw_img = cv2.imread(image_path)
        h, w = raw_img.shape[:2]
        
        print(f"Running inference on {image_path} (Size: {w}x{h})...")
        
        # 2. Run actual model inference
        with torch.no_grad():
            depth = self.model.infer_image(raw_img) 
        
        # 3. Save depth.npy matching contract shape (H, W)
        depth_path = os.path.join(job_dir, "depth.npy")
        np.save(depth_path, depth)

        # 3b. Save an inferno-colormapped visualization for humans to sanity-check
        depth_norm = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        depth_vis = cv2.applyColorMap(depth_norm, cv2.COLORMAP_INFERNO)
        cv2.imwrite(os.path.join(job_dir, "depth_vis.png"), depth_vis)

        # 4. Compute and save intrinsics.json
        intrinsics = self.estimate_intrinsics(w, h)
        intrinsics_path = os.path.join(job_dir, "intrinsics.json")
        with open(intrinsics_path, "w") as f:
            json.dump(intrinsics, f, indent=4)
            
        print(f"Success! Outputs saved to {job_dir}")
if __name__ == "__main__":
    pipeline = DepthPipeline(encoder='vits')
    
    # Test against a real photo (ensure you place a file named house.jpg in this folder)
    pipeline.process_image("house.jpeg", "../shared/sample_jobs/job_real_001")
