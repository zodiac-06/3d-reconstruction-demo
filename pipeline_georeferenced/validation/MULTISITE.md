# Multi-site validation vs ICESat-2

`multisite_results.json` holds the accuracy of the pipeline's DSM against
ICESat-2 ATL08 lidar at five sites, overall and per ESA WorldCover land-cover
class, for two targets: canopy top (ATL08 terrain + `h_canopy`) and ground
(ATL08 terrain). Error is DSM minus ICESat-2, so a negative bias means the DSM
is too low. Every site is scored by the same script, `multisite_scores.py`.

## Results

| Site | Box (W, S, E, N) | ICESat-2 segments | with canopy top | passes | canopy top: n, bias, RMSE (m) | ground: n, bias, RMSE (m) |
|---|---|---|---|---|---|---|
| Nainital | 79.419, 29.349, 79.511, 29.421 | 2,529 | 2,285 | 21 | 2,255, -9.52, 14.84 | 2,494, +7.08, 12.85 |
| Bangalore | 77.553, 12.935, 77.636, 13.008 | 7,635 | 6,264 | 20 | 6,082, -9.25, 10.83 | 7,425, +4.30, 5.96 |
| Jaisalmer | 70.859, 26.879, 70.941, 26.961 | 36,231 | 7,273 | 33 | 7,216, -4.81, 5.71 | 35,049, +0.27, 0.98 |
| Agumbe | 75.050, 13.470, 75.130, 13.550 | 1,055 | 817 | 19 | 813, -8.67, 10.68 | 1,051, +6.07, 8.79 |
| Delhi | 77.177, 28.591, 77.261, 28.673 | 4,715 | 3,064 | 16 | 2,838, -7.86, 9.48 | 4,375, +3.85, 5.40 |

The JSON adds std, MAE and correlation, the same figures per land-cover
class, and SHA-256 hashes of the DSM and truth file each row was computed
from. It holds no point data.

## Caveats

- **Agumbe has few points.** It is dense rainforest: only 1,051 ground
  segments and 813 with a canopy top, from 19 passes. Per-class figures there
  rest on small samples (cropland 25 canopy-top points, built-up 6).
- **Jaisalmer's low ground error is the DEM on flat desert**, not the depth
  model. The site is mostly bare and sparse ground with little relief, where
  the Copernicus DEM is already within about 1 m of ICESat-2. The depth
  detail layer there has a std of 0.017 m. Its canopy-top truth is also
  doubtful: the median ATL08 canopy height is 4.96 m over desert scrub and
  bare ground.
- Classes with fewer than about 100 points per site should not be trusted
  on their own.
- Moga (30.800N 75.170E) is missing: its job failed on this code because
  the Sentinel-2 scene picked for it did not cover the box (fixed on the
  scene-cover-fix branch).

## How to reproduce

For each site:

1. **DSM.** Run a job through the pipeline. Nainital and Bangalore used their
   cropped GeoTIFFs (uploaded as-is), the other three a bounds-only job for a
   0.08 degree box centred on Jaisalmer 26.920N 70.900E, Agumbe 13.510N
   75.090E and Delhi 28.632N 77.219E. Download the job's DSM from
   `GET /jobs/{id}/dsm.tif`.
2. **ICESat-2 truth.** `python dsm_calibration/fetch_icesat2_truth.py <job DSM> <truth.csv>`
   (needs `sliderule` and `pyproj`, see `requirements-eval.txt`).
3. **Score.** Use the DSM's own extent in EPSG:4326 as the box:

   ```
   python validation/multisite_scores.py --site <name> --bbox W,S,E,N \
       --dsm <job DSM> --truth <truth.csv>
   ```

   This adds or replaces the site's entry in `multisite_results.json`. Land
   cover is read from the public ESA WorldCover 2021 tiles on AWS, so it needs
   network access.

The DSMs were made with the code at commit 62fb5e2. Results for a different
scene date or code version will differ; the hashes in the JSON identify the
exact inputs.
