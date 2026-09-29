"""
Checks geotiff_to_viewer_assets.convert_elevation's 16-bit encoding: every
height decoded from the PNG (min + v / 65535 * range) must be within half a
step of the DSM, with no bias. The old encoding truncated (astype alone),
reading every height up to a full step low; the same heights are run
through that formula to show the difference.

The DSM is random heights over Nainital's range (914.69-2631.81 m) on a
small grid, with a nodata rim like the real DSMs have.

    python dsm_calibration/test_viewer_assets.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geotiff_to_viewer_assets import convert_elevation  # noqa: E402

LO, HI = 914.6884765625, 2631.806884765625


def main():
    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    rng = np.random.default_rng(0)
    z = rng.uniform(LO, HI, (300, 400))
    z[0, 0], z[-1, -1] = LO, HI                       # pin the range
    z = z.astype(np.float32).astype(np.float64)       # what the GeoTIFF will hold
    rim = np.zeros(z.shape, bool); rim[:, :2] = True  # nodata rim, filled by the converter
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with rasterio.open(tmp / "dsm.tif", "w", driver="GTiff", width=400, height=300, count=1,
                           dtype="float32", crs="EPSG:32644", transform=from_origin(0, 3000, 10, 10),
                           nodata=-9999) as ds:
            ds.write(np.where(rim, -9999, z).astype(np.float32), 1)
        shape, lo, hi = convert_elevation(str(tmp / "dsm.tif"), str(tmp / "e.png"))
        png = np.array(Image.open(tmp / "e.png"), dtype=np.float64)

    step = (hi - lo) / 65535
    inner = ~rim & (z >= lo) & (z <= hi)
    check(abs(lo - z[inner].min()) < 1e-3 and abs(hi - z[inner].max()) < 1e-3 and lo > 0,
          f"min/max {lo:.3f}-{hi:.3f} m from valid pixels only (nodata rim ignored)")
    err = (lo + png / 65535 * (hi - lo) - z)[inner]
    check(np.abs(err).max() <= step / 2 + 1e-6,
          f"max |error| {np.abs(err).max() * 100:.3f} cm <= half a step ({step / 2 * 100:.3f} cm)")
    check(abs(err.mean()) < step / 20, f"unbiased: mean error {err.mean() * 100:+.4f} cm")

    old = np.clip((z - lo) / (hi - lo) * 65535, 0, 65535).astype(np.uint16)
    old_err = (lo + old / 65535 * (hi - lo) - z)[inner]
    print(f"     old truncating encoding on the same heights: max |error| {np.abs(old_err).max() * 100:.3f} cm, "
          f"mean {old_err.mean() * 100:+.3f} cm")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
