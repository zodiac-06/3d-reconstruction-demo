import cv2
import torch
import numpy as np
import json
import os
import math
from PIL import Image

class DepthPipeline:
    def __init__(self, encoder='vits', fov_deg=65.0):
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.fov_deg = fov_deg
        self.model_path = f'checkpoints/depth_anything_v2_{encoder}.pth'
        
        print(f"Loading model on {self.device}...")
        # TODO: Human to download DepthAnythingV2 weights and uncomment below
        # from depth_anything_v2.dpt import DepthAnythingV2
        # self.model = DepthAnythingV2(encoder=encoder, features=64, out_channels=[48, 96, 192, 384])
        # self.model.load_state_dict(torch.load(self.model_path, map_location='cpu'))
        # self.model = self.model.to(self.device).eval()

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
        os.makedirs(job_dir, exist_ok=True)
        
        raw_img = cv2.imread(image_path)
        if raw_img is None:
            raise ValueError(f"Could not load image at {image_path}")
        h, w = raw_img.shape[:2]
        
        print(f"Running inference on {image_path}...")
        # MOCKED INFERENCE: Replace with self.model.infer_image(raw_img) once weights are downloaded
        depth = np.random.rand(h, w).astype(np.float32) 
        
        # Save depth.npy matching contract shape (H, W)
        depth_path = os.path.join(job_dir, "depth.npy")
        np.save(depth_path, depth)
        
        # Compute and save intrinsics.json
        intrinsics = self.estimate_intrinsics(w, h)
        intrinsics_path = os.path.join(job_dir, "intrinsics.json")
        with open(intrinsics_path, "w") as f:
            json.dump(intrinsics, f, indent=4)
            
        print(f"Outputs saved to {job_dir}")

if __name__ == "__main__":
    pipeline = DepthPipeline()
    # pipeline.process_image("your_test_photo.jpg", "../shared/sample_jobs/job_real_001")
