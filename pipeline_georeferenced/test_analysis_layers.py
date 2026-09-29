"""
Checks the dashboard's analysis layers against values computed here:

- Confidence (dem_court/court.py): DEM spread on a synthetic court cache
  with known per-source offsets, NaN handling, class fractions, and the
  GET /jobs/{id}/confidence(.tif) endpoints (409 before it's computed,
  GeoTIFF pixels, grid and tags).
- Provenance (geotiff_to_viewer_assets.write_provenance_asset): the PNG
  decodes back to DSM - DEM within one 16-bit step, no-data marked 0.
- Viewer JS (3d_visualization/layers.js, under node): confidence colours
  per class, provenance decoding of that same PNG, and the measure tool on
  the committed Nainital assets against numpy (distance from pixel size,
  bilinear heights along the line).

    python test_analysis_layers.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import Affine

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "dsm_calibration"))
VIZ = ROOT / "3d_visualization"

fails = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)


def synthetic_court(cache, rng, w=60, h=40):
    """Three sources: copernicus = base, srtm = base + a, nasadem = base - b
    (a, b >= 0), so the spread is exactly a + b; plus NaN holes."""
    base = 1500 + rng.normal(0, 50, (h, w))
    a, b = rng.uniform(0, 12, (h, w)), rng.uniform(0, 6, (h, w))
    cop, srtm, nas = base.copy(), base + a, base - b
    srtm[0, :5] = np.nan                      # 2 sources left: spread = b
    srtm[1, :5] = np.nan; nas[1, :5] = np.nan  # 1 source left: NaN
    expected = a + b
    expected[0, :5] = b[0, :5]
    expected[1, :5] = np.nan
    cache.mkdir(parents=True)
    for name, arr in (("copernicus", cop), ("srtm", srtm), ("nasadem", nas)):
        arr.astype("<f4").tofile(cache / f"{name}.f32")
    transform = Affine(10, 0, 346000, 0, -10, 3255000)
    with rasterio.open(cache / "grid.tif", "w", driver="GTiff", width=w, height=h, count=1, dtype="uint8",
                       crs="EPSG:32644", transform=transform) as g:
        g.write(np.zeros((h, w), np.uint8), 1)
    manifest = {"version": 1, "grid": {"width": w, "height": h, "crs": "EPSG:32644", "epsg": 32644,
                                        "transform": list(transform)[:6], "pixel_m": 10.0},
                "target_datum": "EGM2008", "cache_url": f"/dem_court/cache/{cache.name}/",
                "sources": {s: {"available": True, "url": f"{s}.f32"} for s in ("copernicus", "srtm", "nasadem")}}
    (cache / "court.json").write_text(json.dumps(manifest))
    return manifest, expected.astype(np.float32), transform


def test_confidence(tmp):
    import main
    from fastapi.testclient import TestClient

    court = main.dem_court_mod
    rng = np.random.default_rng(11)
    bbox = (79.42, 29.35, 79.51, 29.42)
    court.CACHE_ROOT = str(tmp / "court")
    cache = Path(court.CACHE_ROOT) / court.aoi_key(bbox)
    manifest, expected, transform = synthetic_court(cache, rng)

    spread, members = court.confidence_spread(str(cache), manifest)
    both = np.isfinite(expected)
    check(np.array_equal(np.isfinite(spread), both) and np.abs(spread[both] - expected[both]).max() < 1e-3,
          f"spread = known offsets, NaN where < 2 DEMs (max |diff| {np.abs(spread[both] - expected[both]).max():.1e} m)")
    s = court.confidence_summary(spread, members)
    e = expected[both]
    want = [(e <= 5).mean(), ((e > 5) & (e <= 10)).mean(), (e > 10).mean()]
    got = [c["fraction"] for c in s["classes"]]
    check(np.allclose(got, want) and abs(sum(got) - 1) < 1e-9,
          f"class fractions high/medium/low {[round(g, 3) for g in got]} = expected {[round(w, 3) for w in want]}")
    try:
        court.confidence_spread(str(cache), {**manifest, "sources": {"copernicus": {"available": True}}})
        check(False, "one DEM only -> LookupError")
    except LookupError:
        check(True, "one DEM only -> LookupError, not a fake confidence")

    main.BASE_DIR, main.JOBS_DIR_NAME = tmp, "jobs"
    (tmp / "jobs").mkdir()
    main.jobs.clear()
    main.jobs["j1"] = {"id": "j1", "status": "done", "bounds_epsg4326": dict(zip(("west", "south", "east", "north"), bbox))}
    main.jobs["j2"] = {"id": "j2", "status": "done", "bounds_epsg4326": {"west": 1, "south": 1, "east": 2, "north": 2}}
    client = TestClient(main.app)
    r = client.get("/jobs/j1/confidence").json()
    check(r["n_scored"] == int(both.sum()) and r["spread_url"].endswith("/spread.f32") and r["sources"] == members,
          f"GET /jobs/j1/confidence: {r['n_scored']} pixels scored, spread at {r['spread_url']}")
    arr = np.fromfile(cache / "spread.f32", "<f4")
    check(main.is_public_path(r["spread_url"]) and np.array_equal(np.isfinite(arr), both.ravel()),
          "spread.f32 written to the court cache, on the static allow-list")
    r2 = client.get("/jobs/j2/confidence")
    check(r2.status_code == 409 and "not computed" in r2.json()["detail"],
          f"AOI without a DEM Court cache -> {r2.status_code}, no fetch triggered")
    check(client.get("/jobs/nope/confidence").status_code == 404, "unknown job -> 404")

    t = client.get("/jobs/j1/confidence.tif")
    path = tmp / "conf.tif"
    path.write_bytes(t.content)
    with rasterio.open(path) as ds:
        px, tags = ds.read(1), ds.tags()
        grid_ok = ds.crs.to_epsg() == 32644 and tuple(ds.transform)[:6] == tuple(transform)[:6]
    check(t.status_code == 200 and grid_ok and np.array_equal(np.isfinite(px), both)
          and np.abs(px[both] - expected[both]).max() < 1e-3,
          "confidence.tif: job grid, spread pixels, NaN kept")
    check(tags.get("UNITS") == "metre" and tags.get("SOURCES") == ",".join(members) and "high <= 5.0 m" in tags.get("CLASSES", ""),
          f"confidence.tif tagged: {tags.get('SOURCES')}; {tags.get('CLASSES')}")
    return spread, s


def test_provenance(tmp):
    from geotiff_to_viewer_assets import write_provenance_asset

    rng = np.random.default_rng(5)
    h, w = 50, 70
    dem = 1200 + rng.normal(0, 80, (h, w))
    detail = rng.normal(0, 0.4, (h, w))
    dsm = dem + detail
    dsm[3, 3] = np.nan      # no DSM
    dem[4, 4] = np.nan      # no DEM
    path = tmp / "dsm.tif"
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", nodata=-9999,
                       crs="EPSG:32644", transform=Affine(10, 0, 0, 0, -10, 0)) as ds:
        ds.write(np.where(np.isfinite(dsm), dsm, -9999).astype(np.float32), 1)
    png = tmp / "prov.png"
    meta = write_provenance_asset(str(path), dem, str(png))
    raw = np.array(Image.open(png)).astype(np.float64)
    got = np.where(raw == 0, np.nan, meta["detailMin"] + (raw - 1) / 65534 * (meta["detailMax"] - meta["detailMin"]))
    exp = dsm.astype(np.float32).astype(np.float64) - dem
    ok = np.isfinite(exp)
    step = (meta["detailMax"] - meta["detailMin"]) / 65534
    d = np.abs(got[ok] - exp[ok]).max()
    check(np.array_equal(raw == 0, ~ok) and meta["noDataPixels"] == 2, "provenance: the 2 no-data pixels are 0, nothing else")
    check(d <= step / 2 + 1e-9, f"provenance decodes to DSM - DEM (max |diff| {d:.2e} m, half a step {step / 2:.2e})")
    try:
        write_provenance_asset(str(path), dem[:-1], str(png))
        check(False, "grid mismatch raises")
    except ValueError:
        check(True, "provenance: DEM on a different grid is refused")
    return raw, meta, exp


def test_js(spread, summary, prov_raw, prov_meta, prov_exp):
    meta = json.loads((VIZ / "assets" / "metadata.json").read_text())
    elev = np.array(Image.open(VIZ / "assets" / "elevation_16bit.png")).astype(np.float64)
    h, w = elev.shape
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        np.nan_to_num(spread, nan=-1).astype("<f4").tofile(t / "spread.f32")
        prov_raw.astype("<u2").tofile(t / "prov.u16")
        elev.astype("<u2").tofile(t / "elev.u16")
        script = f"""
          import {{ readFileSync }} from 'node:fs';
          const L = await import({json.dumps((VIZ / 'layers.js').as_uri())});
          const P = await import({json.dumps((VIZ / 'elevation_probe.js').as_uri())});
          const f32 = new Float32Array(readFileSync({json.dumps(str(t / 'spread.f32'))}).buffer.slice(0));
          const spread = f32.map(v => v < 0 ? NaN : v);
          const conf = L.confidencePixels(spread, {json.dumps(summary['classes'])});
          const prov = new Uint16Array(readFileSync({json.dumps(str(t / 'prov.u16'))}).buffer.slice(0));
          const detail = L.decodeProvenance({{ data: prov }}, {json.dumps(prov_meta)});
          const e = new Uint16Array(readFileSync({json.dumps(str(t / 'elev.u16'))}).buffer.slice(0));
          const meta = {json.dumps(meta)};
          const probe = P.createElevationProbe({{ width: {w}, height: {h}, data: e }}, meta);
          const m = L.measure(probe, meta, {{ col: 120, row: 600 }}, {{ col: 700, row: 150 }}, 50);
          const range = L.symmetricRange(detail);
          console.log(JSON.stringify({{ conf: Array.from(conf), detail: Array.from(detail, v => Number.isNaN(v) ? null : v),
            m: {{ ...m, profile: m.profile.map(p => [p.col, p.row, p.z]) }}, range }}));
        """
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    r = json.loads(out.stdout)

    conf = np.array(r["conf"]).reshape(-1, 4)
    colours = {"high": (67, 160, 71), "medium": (251, 192, 45), "low": (229, 57, 53)}
    s = spread.ravel()
    ok = True
    for name, lo, hi in (("high", -1, 5), ("medium", 5, 10), ("low", 10, 1e9)):
        sel = np.isfinite(s) & (s > lo) & (s <= hi)
        ok &= bool(sel.any()) and (conf[sel, :3] == colours[name]).all() and (conf[sel, 3] == 255).all()
    ok &= (conf[~np.isfinite(s), 3] == 0).all()
    check(ok, "layers.js confidence: each class its colour, NaN transparent")

    det = np.array([np.nan if v is None else v for v in r["detail"]]).reshape(prov_exp.shape)
    both = np.isfinite(prov_exp)
    check(np.array_equal(np.isfinite(det), both) and np.abs(det[both] - prov_exp[both]).max() < 1e-4 + (prov_meta["detailMax"] - prov_meta["detailMin"]) / 65534,
          "layers.js provenance decode = DSM - DEM, no-data as NaN")
    check(r["range"] >= 0.1, f"provenance colour range ±{r['range']} m")

    m = r["m"]
    nb = meta["boundsNative"]
    px, py = (nb["right"] - nb["left"]) / w, (nb["top"] - nb["bottom"]) / h
    dist = np.hypot(580 * px, 450 * py)
    z = meta["minElevation"] + elev / 65535 * (meta["maxElevation"] - meta["minElevation"])

    def bilinear(c, rr):
        c0, r0 = int(np.floor(c)), int(np.floor(rr))
        c1, r1 = min(w - 1, c0 + 1), min(h - 1, r0 + 1)
        fc, fr = c - c0, rr - r0
        return (z[r0, c0] * (1 - fc) + z[r0, c1] * fc) * (1 - fr) + (z[r1, c0] * (1 - fc) + z[r1, c1] * fc) * fr

    prof = np.array([bilinear(c, rr) for c, rr, _ in m["profile"]])
    js = np.array([p[2] for p in m["profile"]])
    check(abs(m["horizontalM"] - dist) < 1e-6, f"measure: distance {m['horizontalM']:.2f} m = pixel size x offset {dist:.2f} m")
    check(abs(m["za"] - z[600, 120]) < 1e-6 and abs(m["zb"] - z[150, 700]) < 1e-6 and abs(m["dh"] - (z[150, 700] - z[600, 120])) < 1e-6,
          f"measure: A {m['za']:.2f} m, B {m['zb']:.2f} m, dh {m['dh']:+.2f} m = the DSM pixels")
    check(np.abs(prof - js).max() < 1e-6 and abs(m["climb"] - np.clip(np.diff(prof), 0, None).sum()) < 1e-6,
          f"measure: profile = bilinear DSM along the line; climb {m['climb']:.1f} m")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spread, summary = test_confidence(tmp)
        prov = test_provenance(tmp)
        test_js(spread, summary, *prov)
    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
