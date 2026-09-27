"""
Canopy height from Sentinel-2 with the pretrained Lang et al. global canopy
height model (github.com/langnico/global-canopy-height-model, "Lang-CHM"):
Lang, Jetz, Schindler & Wegner (2023), "A high-resolution canopy height
model of the Earth", Nature Ecology & Evolution.

Input: the 15-channel stack from qgis_prep/06_fetch_s2_l2a_stack.py
(12 L2A bands + lat, sin(lon), cos(lon)) and its SCL raster.

Mirrors gchm/deploy.py for one model of the published ensemble
(GLOBAL_GEDI_MODEL_0 by default):
  - inputs normalized with the model's train_input_mean/std
  - 512px patches, 16px border, symmetric padding (Sentinel2Deploy)
  - predictions denormalized with train_target_mean/std
    (args.json: normalize_targets=true); std = sqrt(variance) * target_std
  - Sentinel2Deploy.recompose_patches masks: empty pixels (B02+B03+B04 == 0),
    negative heights, SCL cloud-medium/high (8, 9), snow (11), water (6)
    -> NaN. Its cloud-probability mask is skipped: Earth Search has no CLD
    layer.

Weights: downloaded from the gchm GitHub release with the same URL and
torch.hub.download_url_to_file call as xceptionS2_08blocks_256(); that
function then shells out to `mkdir -p` / `unzip` / `rm`, which fail under
Windows cmd, so the zip is extracted here with zipfile into the same
layout. gchm's loaders (XceptionS2._load_model_weights, deploy.py) call
torch.load without map_location and the checkpoints hold CUDA tensors, so
the architecture is built with xceptionS2_08blocks_256(model_weights=None)
and checkpoint['model_state_dict'] is loaded here onto the CPU (strict).

gchm is not on PyPI: pass --gchm-repo (a clone of the repo above) or put it
on PYTHONPATH. gchm.models only needs torch.

Outputs (in --out-dir):
  chm_lang.tif         canopy height (m), Lang's masks applied (NaN = masked)
  chm_lang_std.tif     predictive std (m), same mask
  chm_lang_raw.tif     canopy height (m) before any masking

Run:
    python lang_chm.py --gchm-repo path/to/global-canopy-height-model
"""
import argparse
import json
import math
import os
import sys
import zipfile

import numpy as np
import rasterio
import torch
from torch.hub import download_url_to_file

HERE = os.path.dirname(os.path.abspath(__file__))
QGIS_DATA = os.path.join(HERE, "..", "qgis_prep", "data")
WEIGHTS_DIR = os.path.join(HERE, "trained_models")
WEIGHTS_URL = ("https://github.com/langnico/global-canopy-height-model/releases/download/"
               "v1.0-trained-model-weights/trained_models_GLOBAL_GEDI_2019_2020.zip")

SCL_NAN_CLASSES = (6, 8, 9, 11)  # Sentinel2Deploy.scl_exclude_labels


def ensure_weights(download_dir=WEIGHTS_DIR):
    parent = os.path.join(download_dir, "GLOBAL_GEDI_2019_2020")
    if os.path.isdir(parent):
        return parent
    os.makedirs(download_dir, exist_ok=True)
    zip_path = os.path.join(download_dir, "trained_models_GLOBAL_GEDI_2019_2020.zip")
    print(f"Downloading pretrained models: {WEIGHTS_URL}")
    download_url_to_file(url=WEIGHTS_URL, dst=zip_path, hash_prefix=None, progress=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(download_dir)
    os.remove(zip_path)
    return parent


def load_model(model_id=0, download_dir=WEIGHTS_DIR):
    """Returns (model in eval mode, model_dir holding args.json + train stats)."""
    from gchm.models.xception_sentinel2 import xceptionS2_08blocks_256

    ensure_weights(download_dir)
    model_dir = os.path.join(download_dir, "GLOBAL_GEDI_2019_2020", f"model_{model_id}", "FT_Lm_SRCB")
    model = xceptionS2_08blocks_256(in_channels=15, out_channels=1, model_weights=None,
                                    returns="variances_exp")
    checkpoint = torch.load(os.path.join(model_dir, "checkpoint.pt"), map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    model.eval()
    return model, model_dir


def load_stats(model_dir):
    with open(os.path.join(model_dir, "args.json")) as f:
        args = json.load(f)
    assert args["input_lat_lon"] and args["channels"] == 15, args
    stats = {k: np.load(os.path.join(model_dir, f"{k}.npy")).astype(np.float32)
             for k in ("train_input_mean", "train_input_std", "train_target_mean", "train_target_std")}
    return args, stats


def patch_coords(rows, cols, patch_size, border):
    """Sentinel2Deploy._get_patch_coords, on the padded image."""
    step = patch_size - 2 * border
    coords = []
    for y in range(int(math.ceil(rows / step))):
        y0 = min(y * step, rows - patch_size)
        for x in range(int(math.ceil(cols / step))):
            coords.append((y0, min(x * step, cols - patch_size)))
    return coords


def predict(model, stats, image, normalize_targets=True, patch_size=512, border=16, batch_size=2):
    """image: (15, H, W) float32 un-normalized stack. Returns (height_m, std_m),
    each (H, W), unmasked. Patches are recomposed as in recompose_patches:
    each patch contributes only its interior (minus border)."""
    c, h, w = image.shape
    patch_size = min(patch_size, h + 2 * border, w + 2 * border)
    x = (image - stats["train_input_mean"][:, None, None]) / stats["train_input_std"][:, None, None]
    x = np.pad(x, ((0, 0), (border, border), (border, border)), mode="symmetric").astype(np.float32)
    coords = patch_coords(x.shape[1], x.shape[2], patch_size, border)

    pred = np.full(x.shape[1:], np.nan, np.float32)
    std = np.full(x.shape[1:], np.nan, np.float32)
    inner = slice(border, patch_size - border)
    with torch.no_grad():
        for i in range(0, len(coords), batch_size):
            chunk = coords[i:i + batch_size]
            batch = torch.from_numpy(np.stack([x[:, y:y + patch_size, xx:xx + patch_size] for y, xx in chunk]))
            p, v = model(batch)
            p, s = p[:, 0].numpy(), torch.sqrt(v[:, 0]).numpy()
            for k, (y, xx) in enumerate(chunk):
                pred[y + border:y + patch_size - border, xx + border:xx + patch_size - border] = p[k, inner, inner]
                std[y + border:y + patch_size - border, xx + border:xx + patch_size - border] = s[k, inner, inner]
            print(f"  patches {i + len(chunk)}/{len(coords)}")
    pred, std = pred[border:-border, border:-border], std[border:-border, border:-border]
    if normalize_targets:
        pred = pred * stats["train_target_std"][0] + stats["train_target_mean"][0]
        std = std * stats["train_target_std"][0]
    return pred, std


def lang_masks(pred, image, scl):
    """recompose_patches(mask_empty, mask_negative, mask_with_scl); True = masked."""
    empty = image[1:4].sum(axis=0) == 0
    return {"empty": empty, "negative": pred < 0, "scl": np.isin(scl, SCL_NAN_CLASSES)}


def write_like(path, array, ref_path):
    with rasterio.open(ref_path) as ref:
        profile = dict(driver="GTiff", crs=ref.crs, transform=ref.transform, width=ref.width,
                       height=ref.height, count=1, dtype="float32", nodata=np.nan,
                       compress="deflate", predictor=3)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gchm-repo", help="path to a global-canopy-height-model clone")
    ap.add_argument("--stack", default=os.path.join(QGIS_DATA, "s2_l2a_stack.tif"))
    ap.add_argument("--scl", default=os.path.join(QGIS_DATA, "s2_scl.tif"))
    ap.add_argument("--out-dir", default=QGIS_DATA)
    ap.add_argument("--model-id", type=int, default=0, choices=range(5))
    args = ap.parse_args()
    if args.gchm_repo:
        sys.path.insert(0, args.gchm_repo)

    model, model_dir = load_model(args.model_id)
    train_args, stats = load_stats(model_dir)
    print(f"Loaded GLOBAL_GEDI_MODEL_{args.model_id} from {model_dir}")

    with rasterio.open(args.stack) as src:
        image = src.read().astype(np.float32)
    with rasterio.open(args.scl) as src:
        scl = src.read(1)
    print(f"stack {image.shape}")

    pred, std = predict(model, stats, image, normalize_targets=train_args["normalize_targets"])
    masks = lang_masks(pred, image, scl)
    masked = np.zeros(pred.shape, bool)
    for name, m in masks.items():
        print(f"  mask {name}: {int(m.sum())} px")
        masked |= m
    chm, chm_std = np.where(masked, np.nan, pred), np.where(masked, np.nan, std)

    os.makedirs(args.out_dir, exist_ok=True)
    out = {n: os.path.join(args.out_dir, f"{n}.tif") for n in ("chm_lang", "chm_lang_std", "chm_lang_raw")}
    write_like(out["chm_lang"], chm, args.stack)
    write_like(out["chm_lang_std"], chm_std, args.stack)
    write_like(out["chm_lang_raw"], pred, args.stack)
    v = chm[np.isfinite(chm)]
    print(f"canopy height (m), {v.size} valid px: min {v.min():.2f}  p5 {np.percentile(v, 5):.2f}  "
          f"median {np.median(v):.2f}  mean {v.mean():.2f}  p95 {np.percentile(v, 95):.2f}  max {v.max():.2f}")
    for p in out.values():
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
