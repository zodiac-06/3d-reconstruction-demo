# Georeferenced pipeline

Upload an already-cropped GeoTIFF → real absolute-elevation DSM, calibrated
against SRTM → viewable in a 2D context map and a 3D terrain flythrough.

## Scope requirement — read before uploading

**The uploaded GeoTIFF must already be cropped to your area of interest.**
This pipeline does not search for scenes or crop them for you — that is a
deliberate scope choice, not an oversight. Scene search and cropping are
`qgis_prep/01_fetch_geotiff.py` + `02_crop_geotiff.py` (or `02_manual_qgis_crop.md`
for the QGIS-GUI path), run separately, ahead of time, by whoever is
preparing the AOI. This same note is shown in the upload UI itself
(`index.html`).

Uploading an uncropped, multi-hundred-MB scene will still technically run,
but slowly, and will fetch an SRTM tile sized to whatever area you gave it —
not a sensibly small one.

## What's inside

| Folder | Role |
|---|---|
| `qgis_prep/` | AOI prep pipeline (moved here unchanged from the repo root). `01`/`02` are the manual pre-step above; `03_extract_metadata.py` and `04_fetch_srtm.py` are called directly by `main.py` per upload (via callable entry points added on top of their existing CLI behavior — nothing about running them standalone changed). |
| `dsm_calibration/` | `srtm_calibration.py` (the calibration math, mock- and real-verified separately) and `geotiff_to_viewer_assets.py` (GeoTIFF → PNG/JPG + real elevation metadata for the viewers). |
| `3d_visualization/` | The Three.js terrain flythrough (Person 3's viewer). |
| `leaflet_pitch/` | The 2D context map (real AOI rectangle over OpenStreetMap) with a link into the 3D flythrough. |
| `main.py` | FastAPI orchestration: `POST /jobs` chains all of the above for one uploaded GeoTIFF. |

## Run it

```
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` — upload a GeoTIFF, and once processing
finishes you get links to the 2D context map and the 3D flythrough, both
pointed at the new result.

## Pipeline (per upload)

1. Save the upload as the current AOI (`qgis_prep/data/aoi_cropped.tif`) —
   synchronous, single-job MVP scope, same as `track_c_api`: one upload
   overwrites the previous result, no per-job history.
2. Extract its real embedded bounds via rasterio (`03_extract_metadata.py`)
   — not assumed, read from the file's own georeference.
3. Fetch the matching SRTM tile for those exact bounds (`04_fetch_srtm.py`;
   OpenTopography if `OPENTOPO_API_KEY` is set, else the no-auth AWS Skadi
   mirror), resampled onto the upload's own pixel grid.
4. Run Depth Anything V2 on the upload's RGB bands
   (`track_a_depth/depth_estimator.py`, completely unchanged).
5. Calibrate relative depth → absolute elevation against the SRTM tile
   (`dsm_calibration/srtm_calibration.py`'s
   `relative_depth_to_dsm_from_geotiff`), with a held-out RMSE/MAE/
   correlation reported back, not training-set numbers.
6. Convert the DSM + satellite crop into what the viewers actually need —
   PNG/JPG + real min/max elevation metadata (`geotiff_to_viewer_assets.py`)
   — and refresh `leaflet_pitch/` + `3d_visualization/`'s fixed asset paths
   in place.

## Known limitation, inherited from earlier verification

Depth Anything V2 is a monocular model trained on ordinary photos, not
nadir satellite/aerial imagery. On the one real AOI this has been tested
against (Nainital, Uttarakhand), the calibrated DSM's held-out correlation
against true SRTM elevation was only ~0.48 — real signal, not zero, but
not an accurate DSM. The pipeline runs correctly end-to-end; how much to
trust the elevation values it produces is a separate, open question.

## Landscape comparison: Nainital (hilly) vs. Bangalore (dense urban)

To check whether accuracy depends on landscape type, the pipeline was
re-run end-to-end against a second real AOI — central Bangalore
(77.5537, 12.9361, 77.6355, 13.0071), sized to the same physical extent
as Nainital (~8.9km x 8.0km) — by temporarily pointing
`qgis_prep/aoi_config.py` at it, fetching a real (uncached) Sentinel-2
scene and both SRTM tiles it spans (N12E077 + N13E077, the first real
test of the multi-tile mosaic path in `04_fetch_srtm.py`), and uploading
the resulting crop through this pipeline's `/jobs` endpoint. Config and
data were reverted to Nainital afterward — see `aoi_config.py`.

| AOI | held-out RMSE | held-out MAE | correlation | true elevation range |
|---|---|---|---|---|
| Nainital (lake + forested ridges) | ~299m (291.9m this run) | ~239m | ~0.47 (0.478 / 0.469 across runs) | ~600m |
| Bangalore (dense urban core) | 12.6m | 9.8m | 0.418 | ~92m |

Bangalore's raw RMSE looks far better, but that's misleading on its own:
Bangalore's true SRTM relief across the AOI is only ~92m (856-948m,
consistent with the flat Deccan plateau under a dense urban grid),
against Nainital's ~600m of lake-to-ridge relief. Normalizing RMSE by
each AOI's true elevation range gives a fairer comparison:

- Nainital: 299m / 600m ≈ 50% of the true relief
- Bangalore: 12.6m / 92m ≈ 14% of the true relief

The two correlations (0.42 vs. 0.47) are much closer to each other than
the raw RMSEs suggest, and both land in the same "real but weak" band —
consistent with the limitation above: a monocular relative-depth model
not trained on nadir satellite imagery produces some real elevation
signal on both a hilly and a dense-urban scene, but not an accurate one
on either.
