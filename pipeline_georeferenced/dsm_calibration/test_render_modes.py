"""
Checks the 3D viewer's render modes (3d_visualization/render_modes.js)
against values computed independently here from the committed Nainital
assets (elevation_16bit.png decoded by Pillow + metadata.json).

- Slope: numpy central differences over +/-30 m on the true pixel size.
- Hillshade: the standard GIS formula, cos(zenith)cos(slope) +
  sin(zenith)sin(slope)cos(azimuth - aspect) -- written differently from
  the JS (a surface-normal dot product), mathematically the same.
- Heatmap: the lowest pixel gets the ramp's first colour, the highest its
  last, and colour darkens monotonically with elevation.

Runs the JS under node (>= 18) on the whole grid and compares every pixel.

    python dsm_calibration/test_render_modes.py
"""
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

VIZ = Path(__file__).resolve().parent.parent / "3d_visualization"
ASSETS = VIZ / "assets"
SLOPE_SCALE_M, AZ, ALT = 30, 315, 45


def independent():
    meta = json.loads((ASSETS / "metadata.json").read_text())
    raw = np.array(Image.open(ASSETS / "elevation_16bit.png"), dtype=np.float64)
    h, w = raw.shape
    z = meta["minElevation"] + raw / 65535 * (meta["maxElevation"] - meta["minElevation"])
    nb = meta["boundsNative"]
    px, py = (nb["right"] - nb["left"]) / w, (nb["top"] - nb["bottom"]) / h
    hx, hy = max(1, round(SLOPE_SCALE_M / px)), max(1, round(SLOPE_SCALE_M / py))
    c = np.arange(w); r = np.arange(h)
    c0, c1 = np.clip(c - hx, 0, w - 1), np.clip(c + hx, 0, w - 1)
    r0, r1 = np.clip(r - hy, 0, h - 1), np.clip(r + hy, 0, h - 1)
    dzdx = (z[:, c1] - z[:, c0]) / ((c1 - c0) * px)
    dzdy = (z[r0, :] - z[r1, :]) / ((r1 - r0)[:, None] * py)      # north-positive
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    zen, azr, sl = math.radians(90 - ALT), math.radians(AZ), np.radians(slope)
    aspect = np.arctan2(-dzdx, -dzdy)                               # downslope, clockwise from north
    hs = np.clip(np.cos(zen) * np.cos(sl) + np.sin(zen) * np.sin(sl) * np.cos(azr - aspect), 0, None)
    return z, slope, hs, (w, h)


def from_js(tmp):
    script = f"""
      import {{ readFileSync, writeFileSync }} from 'node:fs';
      const p = await import({json.dumps((VIZ / 'elevation_probe.js').as_uri())});
      const m = await import({json.dumps((VIZ / 'render_modes.js').as_uri())});
      const probe = p.createElevationProbe(await p.decodeGray16Png(readFileSync({json.dumps(str(ASSETS / 'elevation_16bit.png'))})),
        JSON.parse(readFileSync({json.dumps(str(ASSETS / 'metadata.json'))}, 'utf8')));
      const g = m.computeGradients(probe);
      const hs = m.hillshade(g);
      writeFileSync({json.dumps(str(tmp / 'slope.f32'))}, Buffer.from(g.slope.buffer));
      writeFileSync({json.dumps(str(tmp / 'hs.f32'))}, Buffer.from(hs.buffer));
      for (const mode of ['elevation', 'hillshade', 'slope'])
        writeFileSync({json.dumps(str(tmp))} + '/' + mode + '.rgba', Buffer.from(m.renderMode(mode, probe, g).buffer));
      console.log(JSON.stringify({{ ramp: m.RAMPS.elevation, slopeRamp: m.RAMPS.slope, slopeMax: m.SLOPE_MAX_DEG }}));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def hex_rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def main():
    z, slope, hs, (w, h) = independent()
    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        info = from_js(tmp)
        js_slope = np.fromfile(tmp / "slope.f32", dtype=np.float32).reshape(h, w)
        js_hs = np.fromfile(tmp / "hs.f32", dtype=np.float32).reshape(h, w)
        rgba = {m: np.fromfile(tmp / f"{m}.rgba", dtype=np.uint8).reshape(h, w, 4)
                for m in ("elevation", "hillshade", "slope")}

    d = np.abs(js_slope - slope)
    check(d.max() < 1e-3, f"slope, all {w * h:,} pixels: max |JS - numpy| {d.max():.2e} deg "
                          f"(range {slope.min():.1f}-{slope.max():.1f} deg, mean {slope.mean():.1f})")
    d = np.abs(js_hs - hs)
    check(d.max() < 1e-5, f"hillshade, all pixels: max |JS - GIS formula| {d.max():.2e} (range {hs.min():.3f}-{hs.max():.3f})")
    # sanity on the physics: NW-facing slopes lit, SE-facing in shade
    dzdx_sign = np.sign(np.gradient(z, axis=1))
    east_rising, west_rising = hs[dzdx_sign > 0].mean(), hs[dzdx_sign < 0].mean()
    check(east_rising > west_rising,
          f"slopes rising to the east (facing west, toward the NW sun) are brighter on average "
          f"({east_rising:.2f} vs {west_rising:.2f})")

    lo, hi = np.unravel_index(np.argmin(z), z.shape), np.unravel_index(np.argmax(z), z.shape)
    check(list(rgba["elevation"][lo][:3]) == hex_rgb(info["ramp"][0]) and
          list(rgba["elevation"][hi][:3]) == hex_rgb(info["ramp"][-1]),
          f"heatmap: lowest pixel {info['ramp'][0]}, highest {info['ramp'][-1]}")
    lum = rgba["elevation"][..., :3].astype(float) @ [0.2126, 0.7152, 0.0722]
    order = np.argsort(z.ravel())
    check(np.all(np.diff(lum.ravel()[order]) <= 1e-9 + 3),   # allow rounding jitter of a few levels
          "heatmap darkens monotonically with elevation")
    g = rgba["hillshade"]
    grey_err = np.abs(g[..., 0].astype(int) - 255 * js_hs).max()
    check(grey_err <= 0.5 + 1e-3 and np.array_equal(g[..., 0], g[..., 1]) and np.array_equal(g[..., 0], g[..., 2]),
          f"hillshade pixels are grey, 255 x shade (max rounding {grey_err:.3f} level)")
    steep = slope >= info["slopeMax"]
    check(np.all(rgba["slope"][steep][:, :3] == hex_rgb(info["slopeRamp"][-1])),
          f"slope map: {int(steep.sum()):,} pixels at >= {info['slopeMax']} deg take the darkest step")
    check(np.all(rgba["elevation"][..., 3] == 255), "overlays are opaque (blending is done by the viewer)")

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
