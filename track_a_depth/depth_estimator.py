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

class DepthPipeline:
    def __init__(self, encoder='vits', fov_deg=65.0):
        # Fall back to CPU automatically for Kali
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.fov_deg = fov_deg
        self.model_path = f'checkpoints/depth_anything_v2_{encoder}.pth'
        
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
