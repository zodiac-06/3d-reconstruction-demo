# Step 2 -- Crop `raw_source.tif` to the AOI in QGIS

`data/raw_source.tif` (from step 1) is a real Sentinel-2 GeoTIFF with a
buffer margin around the AOI. This step trims it down to the exact AOI so
its extent matches the SRTM reference tile pulled in step 4.

## In QGIS (GUI)

1. **Layer -> Add Layer -> Add Raster Layer** -> select `data/raw_source.tif`.
2. The AOI bounding box (EPSG:4326), from `aoi_config.py`'s `AOI_BBOX`:
   - west `79.420`, south `29.350`, east `79.510`, north `29.420`
   (if you change the AOI in `aoi_config.py`, update these numbers too --
   they're the one place the QGIS path and the scripted path can drift
   apart).
3. **Raster -> Extraction -> Clip Raster by Extent.**
   - Input layer: `raw_source.tif`
   - Clipping extent: click the "..." button next to the extent field and
     choose **"Enter extent"**, or type it straight in as
     `79.420,79.510,29.350,29.420 [EPSG:4326]`
     (QGIS's extent field order is `xmin,xmax,ymin,ymax`).
   - Output file: `data/aoi_cropped.tif`
   - Run.
4. Sanity check: right-click the new layer -> **Properties -> Information**,
   and confirm the extent roughly matches the bbox above.

## Equivalent script (if QGIS isn't installed on this machine, or you want
a reproducible/headless version of the same clip)

Run `python 02_crop_geotiff.py` instead -- it performs the same "Clip
Raster by Extent" operation with rasterio and writes the same
`data/aoi_cropped.tif`. Either path is fine; steps 3 and 4 only care that
the file exists.
