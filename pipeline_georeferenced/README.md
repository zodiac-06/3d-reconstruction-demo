# Georeferenced pipeline

A GeoTIFF for an area of interest (uploaded pre-cropped, uploaded and
cropped server-side, or fetched from Sentinel-2 by bounds alone) →
absolute-elevation DSM (a terrain DEM, Copernicus GLO-30 by default, plus a
Depth Anything V2 detail layer) → viewable in a 2D context map and a 3D
terrain flythrough.

**Read "Accuracy" below before relying on the output:** the DSM's accuracy
comes from the DEM. At Sentinel-2's 10m resolution, Depth Anything's
contribution is measurably zero.

## Input modes

`POST /jobs` (and the upload page's mode selector) accepts the area of
interest three ways. They differ only in how `qgis_prep/data/aoi_cropped.tif`
gets written; everything after that is one shared code path.

| Mode | Send | What the server does |
|---|---|---|
| **Upload cropped file** (original, default) | `geotiff` | Uses it as-is. |
| **Upload + crop by bounds** | `geotiff` + `west`, `south`, `east`, `north` | Crops the upload to the bounds with `02_crop_geotiff.crop_geotiff()`, the same code as running `02_crop_geotiff.py` by hand. |
| **Fetch by bounds only** | `west`, `south`, `east`, `north` | Finds the least-cloudy Sentinel-2 L2A scene on Earth Search STAC and windowed-reads the bounds plus a 0.03° buffer (`01_fetch_geotiff.find_scene` / `download_cropped`, the same code as running `01_fetch_geotiff.py`), then crops to the exact bounds as above. |

Bounds are EPSG:4326 degrees, at most 0.5° per side. Invalid,
partial, or oversized bounds get a 400 before any work starts. On the upload
page you can type the bounds or drag a box on the map. The job response
records `input_mode`, where the image came from (`input`, including the
STAC scene ID/date/cloud cover in fetch mode), and per-stage `timings_s`.

Measured on the live server for a ~9×8km AOI (884×787 px at 10m):

| Mode | Total | Where the time goes |
|---|---|---|
| Upload cropped file (Nainital) | 4.1s | DEM fetch 2.3s, Depth Anything 1.3s |
| Upload + crop by bounds (Nainital, 1475×1460 px scene) | 2.1s | crop 0.1s; DEM fetch 0.2s (Copernicus tile already cached in-process) |
| Fetch by bounds only (Nainital) | 10.6s | STAC search 0.8s, Sentinel-2 download 7.8s |
| Fetch by bounds only (Mussoorie, nothing cached) | 16.4s | STAC search 0.8s, Sentinel-2 download 11.3s, DEM fetch 2.6s |

All three Nainital runs produced byte-identical crops (to standalone
`02_crop_geotiff.py`, and in fetch mode to the hand-run `01` → `02`
result, since the scene search picked the same scene) and identical DSMs
and held-out numbers. Fetch-by-bounds only knows one scene per request: an
AOI straddling two Sentinel-2 tiles gets whatever part the chosen scene
covers.

## What's inside

| Folder | Role |
|---|---|
| `qgis_prep/` | AOI prep pipeline (moved here unchanged from the repo root). `01_fetch_geotiff.py` / `02_crop_geotiff.py` can still be run by hand, and are also what `main.py`'s bounds-based input modes call; `03_extract_metadata.py` and `04_fetch_srtm.py` are called directly by `main.py` per upload (via callable entry points added on top of their existing CLI behavior). `04_fetch_srtm.py` also fetches the terrain DEM (`--dem-source copernicus\|fabdem\|srtm`). |
| `dsm_calibration/` | `srtm_calibration.py` (terrain + detail fusion, plus the original SRTM-only Theil-Sen calibration, kept as the consistency check), `geotiff_to_viewer_assets.py` (GeoTIFF → PNG/JPG + real elevation metadata for the viewers), and the evaluation tools `fetch_icesat2_truth.py` + `evaluate_fusion.py`. |
| `3d_visualization/` | The Three.js terrain flythrough (Person 3's viewer). |
| `leaflet_pitch/` | The 2D context map (real AOI rectangle over OpenStreetMap) with a link into the 3D flythrough. |
| `main.py` | FastAPI orchestration: `POST /jobs` chains all of the above for one AOI (see "Input modes"). |

## Run it

```
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` — pick an input mode, and once processing
finishes you get links to the 2D context map and the 3D flythrough, both
pointed at the new result.

Terrain DEM: `DEM_SOURCE=copernicus|srtm|nasadem|fabdem` (env var, default
`copernicus`) sets the server default; a `dem_source` form field on
`POST /jobs` overrides it per upload. The chosen source is tried first, then
Copernicus, then SRTM, then NASADEM (redundancy only -- a different host).
NASADEM ranks below raw SRTM because it scores worse against ICESat-2
canopy top, the DSM target (RMSE: Nainital SRTM 15.2 m vs NASADEM 17.3 m;
Bangalore 10.1 m vs 12.5 m -- `dsm_calibration/evaluate_dem_sources.py`),
though better against bare ground. FABDEM is used only when asked for, never
as a fallback: it is **CC BY-NC-SA 4.0, non-commercial** (see `NOTICE.md`).

## Pipeline (per upload)

1. Write the current AOI (`qgis_prep/data/aoi_cropped.tif`) per the input
   mode above — synchronous, single-job MVP scope, same as `track_c_api`:
   each job overwrites the previous result, no per-job history.
2. Extract its real embedded bounds via rasterio (`03_extract_metadata.py`)
   — not assumed, read from the file's own georeference.
3. Fetch the terrain DEM for those exact bounds
   (`04_fetch_srtm.fetch_terrain_dem_for_aoi`): Copernicus GLO-30 by default
   (public AWS COGs, windowed read), falling back to SRTM. Output is tagged
   with its source and vertical datum.
4. Run Depth Anything V2 on the upload's RGB bands
   (`track_a_depth/depth_estimator.py`, unchanged).
5. Fuse (`srtm_calibration.terrain_plus_detail_dsm_from_geotiff`):
   `DSM = DEM + s·detail + offset` with `s = 1`, `offset = 0`. `detail` is
   Depth Anything's relative depth, put in meters by a Theil-Sen fit against
   the DEM and high-passed at the DEM's ~30m resolution, so it adds only
   what the DEM can't resolve instead of re-adding terrain. The Theil-Sen
   fit's held-out RMSE/MAE/correlation are reported as a **consistency
   check** (how well relative depth alone tracks the DEM), not as the DSM's
   accuracy. The fit uses a fixed seed, so an upload always gives the same
   DSM.
6. Convert the DSM + satellite crop into what the viewers need — PNG/JPG +
   real min/max elevation metadata (`geotiff_to_viewer_assets.py`; the DSM's
   thin nodata rim at the crop edge is filled from its nearest neighbour) —
   and refresh `leaflet_pitch/` + `3d_visualization/`'s fixed asset paths in
   place.

Until the `advanced-model` branch, step 3 fetched SRTM and step 5 was the
SRTM-only calibration: relative depth mapped straight onto elevation by a
Theil-Sen fit against SRTM. That function (`relative_depth_to_dsm`) is
still in `srtm_calibration.py` and is the "before" row below.

## Accuracy

The old pipeline was checked only against SRTM, the same DEM it was
calibrated to, so its numbers couldn't say whether the DSM was right. The
numbers below instead use **ICESat-2 ATL08 spaceborne lidar** (2018–present;
independent of SRTM, Copernicus/TanDEM-X and FABDEM): 2,531 ground segments
of 20m over the Nainital AOI, 2,286 with a canopy-height measurement.
"Canopy top" = lidar ground + canopy height, what a DSM should match;
"terrain" = lidar ground, what a bare-earth DTM should match. Each DEM is
compared in its own vertical datum (SRTM is EGM96; Copernicus and FABDEM
are EGM2008, ~3m apart here).

### Nainital (884×787 px at 10m, elevation 921–2,632m)

Error = DSM − lidar, meters.

| DSM | RMSE vs canopy top | bias | RMSE vs terrain | bias |
|---|---|---|---|---|
| **Before**: Theil-Sen(Depth Anything → SRTM), the old `main.py` | **312.3** | +4.1 | **315.6** | +25.6 |
| **After**: Copernicus + 1.0·detail, **current `main.py` default** | **14.8** | −9.5 | **12.9** | +7.1 |
| After, FABDEM base: FABDEM + 1.0·detail (`dem_source=fabdem`) | 18.4 | −14.8 | 9.2 | +1.9 |
| *Controls — the DEMs alone, no Depth Anything:* | | | | |
| Copernicus GLO-30 alone | 14.8 | −9.5 | 12.8 | +7.1 |
| FABDEM alone | 18.4 | −14.8 | 9.2 | +1.9 |
| SRTM alone | 15.2 | −7.6 | 15.3 | +9.2 |
| *Can any scaling of the detail layer help?* | | | | |
| FABDEM + detail, s/offset fit to (Copernicus − FABDEM) | 13.5 | −7.8 | 12.6 | +8.9 |
| ↳ same offset, s = 0 (no Depth Anything) | 13.5 | −7.8 | 12.6 | +8.9 |
| FABDEM + s·detail + offset, both tuned on lidar (5-fold CV by ICESat-2 pass) | 11.0 | −0.1 | 9.1 | +1.0 |
| ↳ same, s = 0 (no Depth Anything) | 11.0 | −0.1 | 9.1 | +1.0 |

What this says:

1. **The switch to DEM fusion is a ~21× accuracy improvement:** 312m → 15m
   RMSE against lidar canopy top, and 316m → 13m against terrain. The old
   DSM wasn't terrain. It compressed the real relief (p2–p98 range 677m vs.
   ~1,295m in every DEM; mean slope 5° vs. ~29°) and correlated only 0.46
   pixel-for-pixel with the new one. Its old held-out-vs-SRTM score (313.7m
   RMSE, r = 0.45; 312–313m across 10 random seeds) was circular, but it
   happened to match the lidar result, so it wasn't hiding a better DSM.
2. **All of that improvement comes from the DEM; Depth Anything
   contributes nothing measurable at this resolution.** Every
   "+ detail" row matches its no-Depth-Anything control to within 0.1m.
   The detail layer is tiny (std 0.34m) and uncorrelated with what the DEM
   gets wrong: its correlation with lidar − FABDEM is −0.06 (canopy top) and
   −0.09 (terrain) at the 30m high-pass scale, and stays within ±0.15, with
   the sign flipping, at 60, 150 and 300m and unfiltered. Even letting the
   lidar choose `s` doesn't help. A monocular model trained on ground-level
   photos doesn't resolve tree or building heights in 10m Sentinel-2
   pixels. The detail layer is kept in the pipeline because it's
   harmless and is the hook for a model that does (e.g. one trained on
   nadir imagery), not because it currently adds accuracy.
3. **Why Copernicus is the default, not FABDEM:** the output is a surface
   model, and Copernicus (radar DSM) is the closest to canopy top of the
   three (14.8m). FABDEM is the best bare-earth terrain (9.2m, +1.9m bias)
   but sits ~15m below the canopy by design. It's also non-commercial-only.
   Copernicus still reads ~10m low against canopy top: C-band radar
   penetrates partway into the canopy.

The held-out numbers the API still reports (Depth Anything vs. DEM,
r ≈ 0.45, RMSE ≈ 313m here) measure how well relative depth alone tracks
elevation, which is poorly. The UI shows them as a consistency check, not
as the DSM's accuracy.

**End-to-end check through the live API:** uploading the Nainital crop to
`POST /jobs` with the default settings fetched Copernicus remotely, ran
Depth Anything, and returned held-out RMSE 313.58m / MAE 257.06m /
r = 0.4496, identical to the standalone run. Its `output_dsm.tif` matches
the standalone Copernicus + detail DSM to within 0.0003m per pixel and
scores the same against ICESat-2 (14.84m vs canopy top, 12.86m vs
terrain). The upload page, the 2D map and the 3D viewer all render it. The
committed `3d_visualization/assets/` are that run's output (elevation
914.7–2,631.8m, vs. 1,444.8–2,381.0m from the old SRTM-only DSM).

Only Nainital has been evaluated against lidar. Bangalore (below) was run
only under the old pipeline.

## Landscape comparison under the old pipeline: Nainital vs. Bangalore

To check whether the old SRTM-only calibration's accuracy depended on
landscape type, it was re-run end-to-end against a second real AOI —
central Bangalore (77.5537, 12.9361, 77.6355, 13.0071), sized to the same
physical extent as Nainital (~8.9km x 8.0km) — by temporarily pointing
`qgis_prep/aoi_config.py` at it, fetching a real (uncached) Sentinel-2
scene and both SRTM tiles it spans (N12E077 + N13E077, the first real test
of the multi-tile mosaic path in `04_fetch_srtm.py`), and uploading the
resulting crop through this pipeline's `/jobs` endpoint. Config and data
were reverted to Nainital afterward — see `aoi_config.py`. Numbers are
held-out error against SRTM (the old, circular check).

| AOI | held-out RMSE vs SRTM | held-out MAE | correlation | elevation range across AOI |
|---|---|---|---|---|
| Nainital (lake + forested ridges) | ~299m (291.9m / 313.7m across runs) | ~239–258m | ~0.47 (0.478 / 0.469 / 0.448 across runs) | 921–2,632m (~1,710m) |
| Bangalore (dense urban core) | 12.6m | 9.8m | 0.418 | 856–948m (~92m) |

Earlier versions of this table gave Nainital's relief as "~600m". That's
roughly the lake-to-ridge relief right around the lake, not the AOI's
range. Across the whole crop the DEMs (Copernicus, FABDEM and SRTM agree
to within ~10m) span about 921–2,632m, and the p2–p98 range is ~1,300m.
Normalizing each old RMSE by its AOI's full elevation range:

- Nainital: ~299m / ~1,710m ≈ 17% of the relief (23% of the p2–p98 range)
- Bangalore: 12.6m / 92m ≈ 14% of the relief

So the old calibration's relative error was similar on both landscapes,
not 50% vs. 14% as previously stated. The correlations (0.42 vs. ~0.47)
agree: Depth Anything carried some real but weak elevation signal on both
scenes, and not an accurate DSM on either. The DEM fusion above is what
fixes that. Bangalore hasn't been re-run under it.

## Reproducing the evaluation

Outputs go to the gitignored `dsm_calibration/real_run_nainital_fusion/`.
The run directory needs `aoi_cropped.tif` (`02_crop_geotiff.py`'s crop of
the Nainital `raw_source.tif`), `depth.npy` (`DepthPipeline('vits')`), and
`srtm_dem.tif`, `terrain_fabdem.tif`, `terrain_copernicus.tif` (from
`04_fetch_srtm.py`'s functions with `srtm_path=` / `dem_path=` /
`sources=` pointed there). The lidar validation's extra dependencies
(pandas, pyproj, sliderule) are in `requirements-eval.txt`. Then:

```
pip install -r requirements.txt -r requirements-eval.txt
python dsm_calibration/fetch_icesat2_truth.py <run>/aoi_cropped.tif <run>/icesat2_atl08_truth.csv
cd dsm_calibration && python evaluate_fusion.py real_run_nainital_fusion
```
