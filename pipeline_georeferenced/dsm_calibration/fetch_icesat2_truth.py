"""
Independent elevation ground truth for an AOI: ICESat-2 ATL08 lidar
(terrain + canopy height), fetched through SlideRule Earth's public
processing service -- no NASA Earthdata login needed.

Why this exists: srtm_calibration.py's held-out RMSE is measured against
SRTM, the same DEM the calibration was fit to, so it can't say whether a
DSM is actually *right*. ICESat-2 is spaceborne photon-counting lidar,
2018-present, independent of SRTM, Copernicus/TanDEM-X and FABDEM, and
gives both bare-ground height and canopy height per 20m segment -- i.e.
truth for a DTM (terrain) and for a DSM (terrain + canopy top).

Writes a CSV with, per 20m along-track segment that has enough ground
photons:
  terrain_egm96 / terrain_egm2008   ground height, orthometric (SRTM uses
                                    EGM96; FABDEM/Copernicus use EGM2008)
  canopy_top_egm96 / _egm2008       terrain + h_canopy (ATL08's 98th-
                                    percentile canopy height), NaN where the
                                    segment has too few canopy photons
  rgt, cycle, spot, solar_elevation for track-wise splits / filtering

Needs `sliderule` + `pyproj` (+ network access to cdn.proj.org for the
geoid grids) -- deliberately NOT in requirements.txt, since this is an
evaluation tool, not part of the pipeline. Install separately:
    pip install sliderule pyproj

Run:
    python fetch_icesat2_truth.py <aoi_cropped.tif> <out.csv>
"""
import sys

import numpy as np
import pandas as pd
import pyproj
import rasterio
from rasterio.warp import transform_bounds

MIN_GROUND_PHOTONS = 5
MIN_CANOPY_PHOTONS = 5
MAX_CANOPY_HEIGHT_M = 60.0  # ATL08 h_canopy above this here is noise/cloud, not trees


def fetch_atl08(bbox):
    from sliderule import icesat2, sliderule

    sliderule.init("slideruleearth.io", verbose=False)
    west, south, east, north = bbox
    poly = [{"lon": west, "lat": south}, {"lon": east, "lat": south},
            {"lon": east, "lat": north}, {"lon": west, "lat": north},
            {"lon": west, "lat": south}]
    parms = {
        "poly": poly, "cnf": 0, "srt": -1, "len": 20, "res": 20, "pass_invalid": True,
        "atl08_class": ["atl08_ground", "atl08_canopy", "atl08_top_of_canopy"],
        # use_abs_h=False: canopy heights relative to the ground surface.
        # h_te_median stays an absolute (ellipsoidal) height either way.
        "phoreal": {"binsize": 1.0, "geoloc": "center", "use_abs_h": False,
                    "send_waveform": False},
    }
    return icesat2.atl08p(parms)


def ellipsoid_to_orthometric_offset(lon, lat, geoid_epsg):
    """Return H - h for each point (i.e. -N, the negated geoid undulation)."""
    pyproj.network.set_network_enabled(True)
    t = pyproj.Transformer.from_crs("EPSG:4979", f"EPSG:4326+{geoid_epsg}", always_xy=True)
    _, _, z = t.transform(lon, lat, np.zeros_like(lon))
    if np.allclose(z, 0):
        raise RuntimeError("Geoid grid not applied (pyproj returned zero offset) -- "
                           "check network access to cdn.proj.org")
    return np.asarray(z)


def build_truth(gdf):
    lon, lat = gdf.geometry.x.to_numpy(), gdf.geometry.y.to_numpy()
    ground_ok = (gdf.gnd_ph_count >= MIN_GROUND_PHOTONS) & (gdf.h_te_median > 0)
    canopy_ok = (ground_ok & (gdf.veg_ph_count >= MIN_CANOPY_PHOTONS)
                 & (gdf.h_canopy >= 0) & (gdf.h_canopy <= MAX_CANOPY_HEIGHT_M))

    df = pd.DataFrame({
        "lon": lon, "lat": lat,
        "rgt": gdf.rgt.to_numpy(), "cycle": gdf.cycle.to_numpy(), "spot": gdf.spot.to_numpy(),
        "solar_elevation": gdf.solar_elevation.to_numpy(),
        "gnd_ph": gdf.gnd_ph_count.to_numpy(), "veg_ph": gdf.veg_ph_count.to_numpy(),
        "h_canopy": gdf.h_canopy.to_numpy(),
        "terrain_ellipsoid": gdf.h_te_median.to_numpy(),
        "ground_ok": ground_ok.to_numpy(), "canopy_ok": canopy_ok.to_numpy(),
    })
    df = df[df.ground_ok].reset_index(drop=True)
    for name, epsg in (("egm96", 5773), ("egm2008", 3855)):
        df[f"terrain_{name}"] = df.terrain_ellipsoid + ellipsoid_to_orthometric_offset(
            df.lon.to_numpy(), df.lat.to_numpy(), epsg)
        df[f"canopy_top_{name}"] = np.where(df.canopy_ok, df[f"terrain_{name}"] + df.h_canopy, np.nan)
    return df


def main(aoi_path, out_csv):
    with rasterio.open(aoi_path) as ds:
        bbox = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
    gdf = fetch_atl08(bbox)
    df = build_truth(gdf)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv}: {len(df)} ground segments, {int(df.canopy_ok.sum())} with canopy "
          f"top, {df.rgt.nunique()} reference ground tracks, cycles {df.cycle.min()}-{df.cycle.max()}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
