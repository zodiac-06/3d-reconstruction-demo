"""
DEM Court: every available terrain DEM for one AOI, on the AOI's exact
pixel grid and in one vertical datum, so sources can be compared pixel by
pixel (GET /jobs/{id}/dem-court in main.py; page: dem_court/index.html).

Sources: Copernicus GLO-30 (the reference -- the pipeline's default), SRTM
and NASADEM always; FABDEM only when asked for (heavy, CC BY-NC-SA 4.0).
Each is fetched with 04_fetch_srtm.fetch_terrain_dem_for_aoi(sources=(s,))
-- the pipeline's own fetch + align-to-grid path, one source at a time, no
fallback -- so every raster is bilinearly resampled onto the job's grid.
This never changes the pipeline's DEM or its fallback order.

Datum: everything is compared in EGM2008 (Copernicus/FABDEM's datum).
SRTM/NASADEM (EGM96) get H_2008 = H_96 + shift, with shift computed per
pixel by pyproj from EPSG:4326+5773 (EGM96) to EPSG:4326+3855 (EGM2008) --
the same geoid transformation that produced the per-datum heights in
fetch_icesat2_truth.py, which evaluate_dem_sources.py scores against. If
the geoid grids can't be loaded (no network to cdn.proj.org) the court
fails rather than compare raw heights.

Cache: one directory per AOI under dem_court/cache/<key>/, key = the AOI's
EPSG:4326 bounds (what a job records). The first visit snapshots the job's
grid (grid.tif, an all-zero uint8 raster with the crop's CRS/transform/size)
because the pipeline keeps only one current crop; later visits work even
after other jobs overwrite it. Sources are fetched once each; FABDEM is
added to an existing cache when first requested.

Per-source outputs (served statically to the page):
  <source>.f32          harmonised EGM2008 heights, float32 little-endian,
                        row-major, NaN = no data
  geoid_shift.f32       EGM96 -> EGM2008 shift (m) per pixel
  raw_<source>_aligned.tif   the source as fetched, on the grid, own datum
"""
import hashlib
import json
import os
import time

import numpy as np
import pandas as pd
import pyproj
import rasterio
from pyproj import Transformer
from rasterio.warp import transform_bounds

REFERENCE = "copernicus"
ALWAYS = ("copernicus", "srtm", "nasadem")
OPT_IN = ("fabdem",)
TARGET_DATUM = "EGM2008"
GEOID_CRS = {"EGM96": "EPSG:4326+5773", "EGM2008": "EPSG:4326+3855"}
AGREE_THRESHOLD_M = 5.0
LICENCE = {
    "copernicus": "Copernicus DEM licence (free use)",
    "srtm": "NASA, no restrictions",
    "nasadem": "NASA, no restrictions",
    "fabdem": "CC BY-NC-SA 4.0 (non-commercial)",
}
MANIFEST_VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(HERE, "cache")


def aoi_key(bbox):
    s = ",".join(f"{v:.6f}" for v in bbox)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def bounds_of(path):
    with rasterio.open(path) as src:
        return transform_bounds(src.crs, "EPSG:4326", *src.bounds)


def same_bounds(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _snapshot_grid(ref_path, grid_path):
    with rasterio.open(ref_path) as ref:
        profile = dict(driver="GTiff", crs=ref.crs, transform=ref.transform, width=ref.width,
                       height=ref.height, count=1, dtype="uint8", compress="deflate")
    with rasterio.open(grid_path, "w", **profile) as dst:
        dst.write(np.zeros((profile["height"], profile["width"]), np.uint8), 1)


def ensure_grid(bbox, crop_path):
    """Snapshot crop_path's pixel grid into this AOI's cache unless it's
    already there. Returns the grid path, or None when there is no cached
    grid and crop_path doesn't match bbox (replaced by a later job). main.py
    calls this when a job finishes, while it still owns the crop, so later
    court visits never read the pipeline's live crop."""
    cache = os.path.join(CACHE_ROOT, aoi_key(bbox))
    grid_path = os.path.join(cache, "grid.tif")
    if os.path.exists(grid_path):
        return grid_path
    if not (crop_path and os.path.exists(crop_path) and same_bounds(bounds_of(crop_path), bbox)):
        return None
    os.makedirs(cache, exist_ok=True)
    _snapshot_grid(crop_path, grid_path)
    return grid_path


def _pixel_lonlat(grid_path):
    with rasterio.open(grid_path) as g:
        rows, cols = np.mgrid[0:g.height, 0:g.width]
        xs, ys = rasterio.transform.xy(g.transform, rows.ravel(), cols.ravel())
        lon, lat = Transformer.from_crs(g.crs, "EPSG:4326", always_xy=True).transform(
            np.asarray(xs), np.asarray(ys))
        return lon.reshape(g.height, g.width), lat.reshape(g.height, g.width)


def geoid_shift(lon, lat, from_datum, to_datum=TARGET_DATUM):
    """Metres to add to a from_datum orthometric height to get to_datum."""
    if from_datum == to_datum:
        return np.zeros_like(lon)
    pyproj.network.set_network_enabled(True)
    t = Transformer.from_crs(GEOID_CRS[from_datum], GEOID_CRS[to_datum], always_xy=True)
    _, _, z = t.transform(lon.ravel(), lat.ravel(), np.zeros(lon.size))
    z = np.asarray(z).reshape(lon.shape)
    if not np.isfinite(z).all() or np.allclose(z, 0):
        raise RuntimeError(f"Geoid shift {from_datum}->{to_datum} unavailable (pyproj returned "
                           "zero/inf) -- check network access to cdn.proj.org. Refusing to "
                           "compare raw heights.")
    return z


def _write_f32(path, array):
    np.asarray(array, dtype="<f4").tofile(path)


def _stats(a):
    a = a[np.isfinite(a)]
    if not a.size:
        return None
    return {"n": int(a.size), "min": float(a.min()), "max": float(a.max()), "mean": float(a.mean()),
            "p5": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95))}


def _icesat_points(grid_path, search_dirs):
    """ICESat-2 truth for this grid, if an evaluation run on the same grid
    exists (a run dir with aoi_cropped.tif + icesat2_atl08_truth.csv)."""
    with rasterio.open(grid_path) as g:
        crs, transform, shape = g.crs, g.transform, g.shape
    for d in search_dirs:
        ref, csv = os.path.join(d, "aoi_cropped.tif"), os.path.join(d, "icesat2_atl08_truth.csv")
        if not (os.path.exists(ref) and os.path.exists(csv)):
            continue
        with rasterio.open(ref) as r:
            if not (r.crs == crs and r.transform == transform and r.shape == shape):
                continue
        t = pd.read_csv(csv)
        xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
            t.lon.to_numpy(), t.lat.to_numpy())
        cols, rows = ~transform * (np.asarray(xs), np.asarray(ys))  # pixel-edge coords
        inside = (cols >= 0) & (cols < shape[1]) & (rows >= 0) & (rows < shape[0])
        pts = [[round(float(c), 3), round(float(r), 3), round(float(lat), 6), round(float(lon), 6),
                round(float(tr), 2), None if not np.isfinite(ct) else round(float(ct), 2)]
               for c, r, lat, lon, tr, ct in zip(cols[inside], rows[inside], t.lat[inside],
                                                 t.lon[inside], t.terrain_egm2008[inside],
                                                 t.canopy_top_egm2008[inside])]
        return {"source_run": os.path.basename(os.path.normpath(d)), "datum": TARGET_DATUM,
                "fields": ["col", "row", "lat", "lon", "terrain_m", "canopy_top_m"], "points": pts}
    return None


def build_court(bbox, fetch_mod, current_crop=None, include_fabdem=False, icesat_dirs=()):
    """Returns (manifest, cache_dir). current_crop: path of the pipeline's
    current aoi_cropped.tif; used to snapshot the grid on first visit if its
    bounds match bbox. Raises LookupError if the AOI has no cache and its
    crop is gone."""
    t_start = time.perf_counter()
    key = aoi_key(bbox)
    cache = os.path.join(CACHE_ROOT, key)
    manifest_path = os.path.join(cache, "court.json")
    grid_path = os.path.join(cache, "grid.tif")
    wanted = ALWAYS + (OPT_IN if include_fabdem else ())

    manifest = None
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        if manifest.get("version") != MANIFEST_VERSION:
            manifest = None
    # a source that failed last time is retried, not cached as unavailable
    if manifest and all(manifest["sources"].get(s, {}).get("available") for s in wanted):
        manifest = dict(manifest, cache_hit=True, include_fabdem=include_fabdem,
                        load_time_s=round(time.perf_counter() - t_start, 3))
        manifest["sources"] = {s: v for s, v in manifest["sources"].items() if s in wanted}
        return _finish(manifest, include_fabdem), cache

    if ensure_grid(bbox, current_crop) is None:
        raise LookupError("This job's AOI crop has been replaced by a later job and the DEM "
                          "Court has no cache for it -- re-run the job, then open the court.")

    with rasterio.open(grid_path) as g:
        grid = {"crs": g.crs.to_string(), "epsg": g.crs.to_epsg(), "transform": list(g.transform)[:6],
                "width": g.width, "height": g.height, "pixel_m": abs(g.transform.a)}
    lon, lat = _pixel_lonlat(grid_path)

    sources = dict(manifest["sources"]) if manifest else {}
    fetch_times = {}
    for s in wanted:
        if sources.get(s, {}).get("available"):
            continue
        raw, aligned = os.path.join(cache, f"raw_{s}.tif"), os.path.join(cache, f"raw_{s}_aligned.tif")
        t0 = time.perf_counter()
        try:
            fetch_mod.fetch_terrain_dem_for_aoi(bbox=bbox, cropped_path=grid_path, sources=(s,),
                                                dem_path=raw, aligned_path=aligned)
        except Exception as e:  # a source being down shouldn't take the court down
            sources[s] = {"available": False, "error": str(e)}
            continue
        fetch_times[s] = round(time.perf_counter() - t0, 2)
        with rasterio.open(aligned) as src:
            h = src.read(1).astype(np.float64)
            datum = src.tags().get("VERTICAL_DATUM")
            h[h == src.nodata] = np.nan
        shift = geoid_shift(lon, lat, datum)
        if datum != TARGET_DATUM:
            _write_f32(os.path.join(cache, "geoid_shift.f32"), shift)
        _write_f32(os.path.join(cache, f"{s}.f32"), h + shift)
        sources[s] = {
            "available": True, "datum_tag": datum, "licence": LICENCE[s],
            "correction": {"from": datum, "to": TARGET_DATUM, "applied": datum != TARGET_DATUM,
                           **({k: round(v, 3) for k, v in _stats(shift).items() if k != "n"}
                              if datum != TARGET_DATUM else {"min": 0.0, "max": 0.0, "mean": 0.0})},
            "url": f"{s}.f32", "raw_geotiff": f"raw_{s}_aligned.tif", "fetch_time_s": fetch_times[s],
        }

    manifest = {
        "version": MANIFEST_VERSION, "aoi_key": key, "bbox_epsg4326": list(bbox), "grid": grid,
        "reference": REFERENCE, "target_datum": TARGET_DATUM, "agree_threshold_m": AGREE_THRESHOLD_M,
        "geoid_shift_url": "geoid_shift.f32" if os.path.exists(os.path.join(cache, "geoid_shift.f32")) else None,
        "sources": sources,
        "icesat2": _icesat_points(grid_path, icesat_dirs),
        "cache_url": f"/dem_court/cache/{key}/",
    }
    manifest["stats"] = _compare(cache, manifest)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)
    manifest = dict(manifest, cache_hit=False, include_fabdem=include_fabdem, fetch_times_s=fetch_times,
                    load_time_s=round(time.perf_counter() - t_start, 3))
    manifest["sources"] = {s: v for s, v in manifest["sources"].items() if s in wanted}
    return _finish(manifest, include_fabdem), cache


def _load(cache, manifest, s):
    g = manifest["grid"]
    return np.fromfile(os.path.join(cache, f"{s}.f32"), dtype="<f4").reshape(g["height"], g["width"])


def _compare(cache, manifest):
    """Stats for every subset the page can show: with and without FABDEM."""
    avail = [s for s, v in manifest["sources"].items() if v.get("available")]
    out = {}
    for label, members in (("without_fabdem", [s for s in avail if s != "fabdem"]), ("with_fabdem", avail)):
        if label == "with_fabdem" and "fabdem" not in avail:
            continue
        stack = np.stack([_load(cache, manifest, s) for s in members])
        spread = np.nanmax(stack, axis=0) - np.nanmin(stack, axis=0)
        spread[np.sum(np.isfinite(stack), axis=0) < 2] = np.nan
        ref = _load(cache, manifest, REFERENCE) if REFERENCE in members else None
        out[label] = {
            "members": members,
            "height_range": [float(np.nanmin(stack)), float(np.nanmax(stack))],
            "spread": _stats(spread),
            "spread_frac_over_threshold": float(np.nanmean(spread > AGREE_THRESHOLD_M)),
            "diff_vs_reference": {s: {**_stats(_load(cache, manifest, s) - ref),
                                      "abs_p95": float(np.nanpercentile(np.abs(_load(cache, manifest, s) - ref), 95))}
                                  for s in members if ref is not None and s != REFERENCE},
        }
    return out


def _finish(manifest, include_fabdem):
    manifest["compare"] = manifest["stats"]["with_fabdem" if include_fabdem and "with_fabdem" in manifest["stats"]
                                            else "without_fabdem"]
    return manifest
