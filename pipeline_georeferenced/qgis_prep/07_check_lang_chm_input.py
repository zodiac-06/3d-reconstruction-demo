"""
Step 7 -- Shape check: feed data/s2_l2a_stack.tif (from step 6) through the
Lang-CHM network to confirm the 15-channel input matches what
gchm.models.xception_sentinel2 expects. No trained weights and no training
statistics are used (randomly initialised model, mean 0 / std 1, as
gchm/deploy.py falls back to when train_input_mean.npy is absent), so the
outputs are meaningless numbers -- this only proves there are no shape or
channel mismatches.

Patching mirrors gchm.datasets.dataset_sentinel2_deploy.Sentinel2Deploy:
symmetric padding by `border`, overlapping patch_size x patch_size patches,
channels-first float32 tensors.

gchm isn't on PyPI; point --gchm-repo at a clone of
github.com/langnico/global-canopy-height-model (gchm.models only needs torch,
not GDAL).

Run:
    python 07_check_lang_chm_input.py --gchm-repo path/to/global-canopy-height-model
"""
import argparse
import math
import os
import sys

import numpy as np
import rasterio
import torch

STACK_PATH = os.path.join(os.path.dirname(__file__), "data", "s2_l2a_stack.tif")
EXPECTED_CHANNELS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12",
                     "lat", "lon_sin", "lon_cos")


def patch_coords(rows, cols, patch_size, border):
    """Same tiling as Sentinel2Deploy._get_patch_coords (on the padded image)."""
    step = patch_size - 2 * border
    coords = []
    for y in range(int(math.ceil(rows / step))):
        y0 = min(y * step, rows - patch_size)
        for x in range(int(math.ceil(cols / step))):
            coords.append((y0, min(x * step, cols - patch_size)))
    return coords


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gchm-repo", help="path to a global-canopy-height-model clone")
    ap.add_argument("--stack", default=STACK_PATH)
    ap.add_argument("--patch-size", type=int, default=128)
    ap.add_argument("--border", type=int, default=16)  # deploy.py uses border=16
    ap.add_argument("--batch-size", type=int, default=8)
    args = ap.parse_args()
    if args.gchm_repo:
        sys.path.insert(0, args.gchm_repo)
    from gchm.models.xception_sentinel2 import xceptionS2_08blocks_256

    with rasterio.open(args.stack) as src:
        image = src.read().astype(np.float32)  # (C, H, W)
        names = src.descriptions
    print(f"stack {args.stack}: shape {image.shape}, dtype {image.dtype}")
    assert names == EXPECTED_CHANNELS, f"channel order {names} != {EXPECTED_CHANNELS}"
    assert image.shape[0] == 15
    assert np.isfinite(image).all(), "non-finite values in stack"

    b = args.border
    padded = np.pad(image, ((0, 0), (b, b), (b, b)), mode="symmetric")
    coords = patch_coords(padded.shape[1], padded.shape[2], args.patch_size, b)
    print(f"{len(coords)} patches of {args.patch_size}x{args.patch_size} (border {b})")

    model = xceptionS2_08blocks_256(in_channels=15, out_channels=1, model_weights=None, returns="variances_exp")
    model.eval()
    print(f"model: XceptionS2 8 blocks/256 filters, "
          f"entry conv expects {model.entry_block.conv1.in_channels} input channels")

    ps = args.patch_size
    with torch.no_grad():
        for i in range(0, len(coords), args.batch_size):
            batch = np.stack([padded[:, y:y + ps, x:x + ps] for y, x in coords[i:i + args.batch_size]])
            preds, variances = model(torch.from_numpy(batch))
            assert preds.shape == variances.shape == (len(batch), 1, ps, ps), (preds.shape, variances.shape)
            assert torch.isfinite(preds).all() and torch.isfinite(variances).all()
            if i == 0:
                print(f"first batch: input {tuple(batch.shape)} -> predictions {tuple(preds.shape)}, "
                      f"variances {tuple(variances.shape)}")
    print(f"OK: forward() ran on all {len(coords)} patches with no shape errors")


if __name__ == "__main__":
    main()
