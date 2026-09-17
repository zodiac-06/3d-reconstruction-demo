"""
Shared AOI (Area of Interest) definition for the QGIS / GeoTIFF prep track
(DepthWizard -- SIH26175). Every other script in this folder imports the
bounding box from here, so the source image, the crop, the metadata, and
the SRTM reference tile all describe exactly the same patch of ground.

AOI: Nainital, Uttarakhand -- a lake ringed by forested ridges rising
~600m above the lake surface within about 5km. Picked because it has
real, visible relief (good for demonstrating a DSM/DEM output against
SRTM) and sits entirely inside a single 1-degree SRTM tile (N29E079),
which keeps the pipeline simple.

This is the default demo AOI -- leaflet_pitch/index.html and other viewer
pages hardcode "Nainital" in their labels, so don't swap AOI_BBOX to a
different site without also updating those labels. For a second-landscape
comparison test (dense urban Bangalore vs. this hilly/lake terrain), see
the "Landscape comparison" section in README.md -- that was run by
temporarily pointing AOI_BBOX at Bangalore, verified, and reverted here
rather than left as the default.

Coordinates are WGS84 / EPSG:4326 (lon, lat) -- the convention both the
STAC API and OpenTopography expect.

To point this whole pipeline at a different site, change AOI_BBOX below
and re-run 01 through 04. If you crop manually in QGIS (02_manual_qgis_crop.md)
you'll also need to paste the new numbers into QGIS by hand.
"""

AOI_NAME = "nainital"

# (west, south, east, north) in EPSG:4326
AOI_BBOX = (79.420, 29.350, 79.510, 29.420)

# Extra margin used only for the *source* image download (step 1), so the
# raw scene you crop in step 2 has real room around the final AOI instead
# of arriving pre-cropped to the exact edge.
SOURCE_BUFFER_DEG = 0.03  # ~3km


def buffered_bbox():
    w, s, e, n = AOI_BBOX
    b = SOURCE_BUFFER_DEG
    return (w - b, s - b, e + b, n + b)
