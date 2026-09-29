"""
Checks the 3D viewer's click probe (3d_visualization/elevation_probe.js)
against values computed independently here from the committed Nainital
assets: elevation_16bit.png (decoded by Pillow, not the JS decoder) +
metadata.json, lat/lon by pyproj, slope by numpy.

Runs the JS under node (>= 18), then compares. Also checks that the probe's
values for a terrain point don't depend on vertical exaggeration: a ray
aimed at that point's displaced surface position is intersected at
exaggeration 0.5, 1.5 and 4.0 and must land on the same pixel.

    pip install numpy pillow pyproj
    python dsm_calibration/test_viewer_probe.py
"""
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer

VIZ_DIR = Path(__file__).resolve().parent.parent / "3d_visualization"
ASSETS = VIZ_DIR / "assets"
SLOPE_SCALE_M = 30
EXAGGERATIONS = [0.5, 1.5, 4.0]
# Must match terrain.js (baseSize 300, TARGET_RELIEF_FRACTION 0.18).
BASE_SIZE, RELIEF_FRACTION = 300, 0.18


def expected_values(points):
    meta = json.loads((ASSETS / "metadata.json").read_text())
    raw = np.array(Image.open(ASSETS / "elevation_16bit.png"), dtype=np.float64)
    assert raw.max() <= 65535 and raw.max() > 255, "PNG didn't decode as 16-bit"
    h, w = raw.shape
    elev = meta["minElevation"] + raw / 65535 * (meta["maxElevation"] - meta["minElevation"])

    nb = meta["boundsNative"]
    px, py = (nb["right"] - nb["left"]) / w, (nb["top"] - nb["bottom"]) / h
    to_ll = Transformer.from_crs(meta["crs"], "EPSG:4326", always_xy=True)
    hx, hy = max(1, round(SLOPE_SCALE_M / px)), max(1, round(SLOPE_SCALE_M / py))

    out = []
    for col, row in points:
        lon, lat = to_ll.transform(nb["left"] + (col + 0.5) * px, nb["top"] - (row + 0.5) * py)
        c0, c1 = max(col - hx, 0), min(col + hx, w - 1)
        r0, r1 = max(row - hy, 0), min(row + hy, h - 1)
        dzdx = (elev[row, c1] - elev[row, c0]) / ((c1 - c0) * px)
        dzdy = (elev[r1, col] - elev[r0, col]) / ((r1 - r0) * py)
        out.append(dict(col=col, row=row, lat=lat, lon=lon, elevationM=float(elev[row, col]),
                        slopeDeg=math.degrees(math.atan(math.hypot(dzdx, dzdy)))))
    return out, (px, py), (w, h), elev


def js_values(points, width, height):
    aspect = width / height
    plane_w, plane_h = (BASE_SIZE, BASE_SIZE / aspect) if aspect >= 1 else (BASE_SIZE * aspect, BASE_SIZE)
    script = f"""
      import {{ readFileSync }} from 'node:fs';
      const m = await import({json.dumps((VIZ_DIR / 'elevation_probe.js').as_uri())});
      const png = readFileSync({json.dumps(str(ASSETS / 'elevation_16bit.png'))});
      const meta = JSON.parse(readFileSync({json.dumps(str(ASSETS / 'metadata.json'))}, 'utf8'));
      const probe = m.createElevationProbe(await m.decodeGray16Png(png), meta);
      const points = {json.dumps(points)};
      const [W, H] = [{plane_w}, {plane_h}];
      const out = points.map(([col, row]) => {{
        const direct = probe.probePixel(col, row);
        // Ray from a fixed camera toward the point's displaced position at each exaggeration.
        const viaRay = {json.dumps(EXAGGERATIONS)}.map(ex => {{
          const scale = {BASE_SIZE * RELIEF_FRACTION} * ex;
          const target = {{ x: ((col + 0.5) / probe.width - 0.5) * W,
                            y: scale * probe.normalizedAt(col, row),
                            z: ((row + 0.5) / probe.height - 0.5) * H }};
          const origin = {{ x: target.x + 5, y: 400, z: target.z + 8 }};
          const dir = {{ x: target.x - origin.x, y: target.y - origin.y, z: target.z - origin.z }};
          const hit = m.intersectHeightfield(probe, origin, dir, W, H, scale);
          return {{ exaggeration: ex, ...(hit ? probe.probePixel(hit.col, hit.row) : {{ miss: true }}) }};
        }});
        return {{ direct, viaRay }};
      }});
      console.log(JSON.stringify({{ pixelSizeM: probe.pixelSizeM, out }}));
    """
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def pick_points(elev):
    """Three deterministic, differently-behaved points: the AOI centre, the
    highest pixel, and the steepest interior pixel (by 1-px gradient)."""
    h, w = elev.shape
    gy, gx = np.gradient(elev)
    steep = np.hypot(gx, gy)[5:-5, 5:-5]
    sr, sc = np.unravel_index(np.argmax(steep), steep.shape)
    hr, hc = np.unravel_index(np.argmax(elev), elev.shape)
    return [[w // 2, h // 2], [int(hc), int(hr)], [int(sc) + 5, int(sr) + 5]]


def main():
    _, _, (w, h), elev = expected_values([[0, 0]])
    points = pick_points(elev)
    expected, pixel_size, _, _ = expected_values(points)
    got = js_values(points, w, h)

    print(f"Pixel size: python {pixel_size[0]:.4f} x {pixel_size[1]:.4f} m, "
          f"JS {got['pixelSizeM'][0]:.4f} x {got['pixelSizeM'][1]:.4f} m")
    failures = []
    for exp, res in zip(expected, got["out"]):
        d = res["direct"]
        errs = {
            "elevation_m": abs(d["elevationM"] - exp["elevationM"]),
            "slope_deg": abs(d["slopeDeg"] - exp["slopeDeg"]),
            # degrees -> metres, roughly, for a readable error
            "latlon_m": math.hypot((d["lat"] - exp["lat"]) * 111_000,
                                   (d["lon"] - exp["lon"]) * 111_000 * math.cos(math.radians(exp["lat"]))),
        }
        print(f"\n(col {exp['col']}, row {exp['row']})")
        print(f"  python: lat {exp['lat']:.6f} lon {exp['lon']:.6f}  elev {exp['elevationM']:.3f} m  "
              f"slope {exp['slopeDeg']:.3f} deg")
        print(f"  JS    : lat {d['lat']:.6f} lon {d['lon']:.6f}  elev {d['elevationM']:.3f} m  "
              f"slope {d['slopeDeg']:.3f} deg")
        print(f"  abs error: elevation {errs['elevation_m']:.2e} m, slope {errs['slope_deg']:.2e} deg, "
              f"lat/lon {errs['latlon_m']:.3f} m")
        if errs["elevation_m"] > 1e-6 or errs["slope_deg"] > 1e-6 or errs["latlon_m"] > 0.5:
            failures.append(f"({exp['col']},{exp['row']}) direct {errs}")
        for r in res["viaRay"]:
            same = (r.get("col"), r.get("row")) == (exp["col"], exp["row"])
            print(f"  exaggeration {r['exaggeration']}: ray hit pixel ({r.get('col')}, {r.get('row')}) "
                  f"elev {r.get('elevationM', float('nan')):.3f} m slope {r.get('slopeDeg', float('nan')):.3f} deg"
                  f"{'' if same else '  <-- different pixel'}")
            if not same or r["elevationM"] != d["elevationM"] or r["slopeDeg"] != d["slopeDeg"]:
                failures.append(f"({exp['col']},{exp['row']}) exaggeration {r['exaggeration']}")

    print("\nFAIL:\n  " + "\n  ".join(failures) if failures else "\nPASS")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
