# Person 1 track: QGIS / GeoTIFF prep -- DepthWizard (SIH26175)

The path, in order, for getting a real georeferenced AOI plus a matching
SRTM reference tile ready for the depth-estimation and mesh-reconstruction
tracks.

## Setup

    pip install -r requirements.txt

(QGIS itself already bundles GDAL, but these scripts use the standalone
`rasterio` package rather than QGIS's own Python console, so they run fine
outside QGIS too -- same command works in a plain Windows/PowerShell
terminal.)

## The path

1. **`python 01_fetch_geotiff.py`**
   Downloads a real, low-cloud Sentinel-2 GeoTIFF over the AOI (Nainital,
   Uttarakhand -- see `aoi_config.py`) from the public Earth Search STAC
   API, windowed straight out of the scene's cloud-optimized GeoTIFF. No
   account or API key needed. -> `data/raw_source.tif`

2. **Crop to the exact AOI** -- either:
   - in QGIS by hand: follow `02_manual_qgis_crop.md`, or
   - scripted: `python 02_crop_geotiff.py`

   Both paths produce the same `data/aoi_cropped.tif`; use whichever fits
   how you're splitting the work.

3. **`python 03_extract_metadata.py`**
   Pulls CRS, affine transform, resolution, and bounds (native CRS +
   EPSG:4326) out of `aoi_cropped.tif` via rasterio. -> `data/geo_metadata.json`
   This is the file the other tracks read to know exactly what ground the
   image covers, without touching GDAL/rasterio themselves.

4. **`python 04_fetch_srtm.py`**
   Fetches the SRTM tile(s) covering the *exact* AOI from step 3's bounds.
   Uses OpenTopography if `OPENTOPO_API_KEY` is set (free key at
   opentopography.org -> My Account -> myOpenTopo Authorizations and API
   Key), otherwise falls back automatically to the no-auth AWS SRTM
   mirror.
   -> `data/srtm_dem.tif` -- native SRTM grid, ~30m/px, EPSG:4326
   -> `data/srtm_dem_aligned.tif` -- SRTM resampled onto `aoi_cropped.tif`'s
      own grid, pixel-aligned with the optical image and ready for
      RMSE/MAE once the depth track produces a predicted DSM/DEM

### Lang-CHM input (canopy height model prep)

Step 1's `raw_source.tif` is the 3-band uint8 true-colour (TCI) product,
not reflectance, so it can't feed the Lang et al. global canopy height
model (github.com/langnico/global-canopy-height-model), which needs all 12
L2A bands plus lat/lon.

6. **`python 06_fetch_s2_l2a_stack.py`** (writes on `aoi_cropped.tif`'s grid; `--native-grid` to opt out)
   Windowed-reads all 12 L2A band COGs from the same scene step 1 selects,
   resamples the 20m/60m bands bicubically onto the 10m grid, and appends
   gchm's lat / sin(lon) / cos(lon) channels.
   -> `data/s2_l2a_stack.tif` -- 15-band float32, channel order
      B01 B02 B03 B04 B05 B06 B07 B08 B8A B09 B11 B12 lat lon_sin lon_cos
      (the input `Sentinel2Deploy` builds, before train-stat normalization)
   -> `data/s2_scl.tif` -- L2A scene classification on the same grid

   Earth Search's STAC declares the baseline >= 04.00 +1000 DN offset, but
   its COGs are already harmonized; the script checks the pixel values and
   only subtracts the offset if it's really there.

7. **`python 07_check_lang_chm_input.py --gchm-repo <clone>`**
   Runs a randomly initialised `xceptionS2_08blocks_256` forward pass over
   the stack in 128px patches -- a shape/channel check only, no weights.

Real inference with the published weights (GLOBAL_GEDI_MODEL_0) is
`../canopy_height/lang_chm.py` -> `data/chm_lang.tif` (+ `_std`, `_raw`);
DEM + CHM fusion scored against ICESat-2 is
`../dsm_calibration/evaluate_lang_chm.py`.

## Changing the AOI

Everything reads its bounding box from `aoi_config.py`'s `AOI_BBOX`, so to
point this whole pipeline at a different site, change it there and re-run
steps 1-4. If you crop manually in QGIS, `02_manual_qgis_crop.md`'s pasted
extent is the one place you'd need to update by hand to match.

## Manual QGIS vs. scripted

Step 1's download and step 2's crop can both be done entirely by hand in
QGIS (load a scene, draw or type the AOI extent, Clip Raster by Extent) --
the scripts just make the same operations reproducible and fast to re-run
if the AOI or source scene changes later. Both paths land on the same
files, so downstream steps don't care which one you used.

## A note on where this was written

These scripts were written and reviewed here but not executed end-to-end
in this environment, since this sandbox's network egress is restricted to
a small allowlist that doesn't include PyPI or the Sentinel-2/SRTM hosts
used here. They're built on stable, well-documented public APIs (the STAC
API spec, rasterio's windowed-read/reproject APIs, OpenTopography's
GlobalDEM endpoint, and the long-standing AWS SRTM "Skadi" mirror), but
budget a few minutes on your first run on your own machine to work through
any rough edges before your demo run.
