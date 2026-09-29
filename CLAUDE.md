# DepthWizard (SIH26175) - context for Claude Code

Single-view optical image -> DSM + navigable 3D terrain. Team StarOps.

Two pipelines:
- `pipeline_georeferenced/` - GeoTIFF -> absolute DSM in metres. Active work.
- `pipeline_non_georeferenced/` - PNG/JPG -> relative DSM. Leave it alone.

## Current verified state (georeferenced)
- DSM = DEM + Depth Anything V2 detail layer, kept on purpose because the
  brief requires a pre-trained monocular depth backbone. main.py adds it at
  s = 1, offset 0: relative depth put in metres by a Theil-Sen fit against the
  DEM, high-passed at 30 m. It changes results by < 0.03 m at 10 m resolution,
  so accuracy comes from the DEM. Code, README, deck and the design doc all
  say this; do not describe the DSM as "DEM alone".
- DEM chain: Copernicus GLO-30 (default) -> SRTM -> NASADEM. FABDEM only when
  explicitly requested (CC BY-NC-SA, never automatic). Decided: keep SRTM
  ahead of NASADEM (the design doc and deck were corrected to match).
- Why that order: the output is a DSM, so accuracy against canopy top is the
  metric that counts. NASADEM sits closer to bare ground and scores 2.1-2.4 m
  worse than SRTM against canopy top on both AOIs.
- Validation vs ICESat-2, RMSE, offline scripts:
  Nainital 14.8 m canopy top / 12.8 m ground.
  Bangalore 10.8 m canopy top / 6.0 m ground.
  Correlation is not comparable across AOIs (Bangalore has ~48 m of relief).
- Lang-CHM canopy model was tested and made the DSM worse. Branch
  `lang-chm-integration` is kept unmerged on purpose. Do not merge it.
- Input modes: upload cropped GeoTIFF; upload + bounds; bounds only
  (Sentinel-2 via STAC).
- Viewer: Three.js in `3d_visualization/`. 2D map: Leaflet in `leaflet_pitch/`.

## Rules
1. Never add Co-Authored-By or any Claude/AI attribution to commits or PRs.
2. Work on a new branch. Never commit to main, force-push or rewrite history.
   Do not merge unless the prompt says to.
3. Verify numerically against an independent source (direct file reads,
   ICESat-2), not by eye. Report real numbers, including bad ones. Do not tune
   until a result looks nicer.
4. Read the code, not the README, for input formats and conventions.
5. Do not change the DEM order, DSM fusion, or pipeline_non_georeferenced
   unless asked.
6. Datums: Copernicus/FABDEM are EGM2008; SRTM/NASADEM are EGM96. Compare in a
   common datum.
7. Never commit data, weights or run outputs (they are gitignored). Say what
   you could not test instead of guessing.
8. Keep changes small. State what you did not do.

## Environment
- The owner develops on Windows (PowerShell). Cloud sessions run Linux and see
  only committed files. Data folders (`qgis_prep/data`, `real_run*`, cached
  DEMs, ICESat-2 outputs) and model weights must be re-fetched, which needs
  network access to those hosts.
- Serving is local (uvicorn + Cloudflare tunnel). A cloud session cannot test
  or restart it.
- The standalone Windows build must be done locally on Windows.

## Open work
- Done and on main: DEM Court page (`dem_court/`), in-app validation panel
  (`validation/`), viewer height/slope click probe and render modes, per-job
  DSM downloads and job history (PR #5).
- `dem-court-polish`: uncommitted local work; commit, push, then merge.
- Real Depth Anything runs on Nainital and Bangalore, scored with
  `dsm_calibration/evaluate_fusion.py` against the ICESat-2 truth CSVs, to
  confirm the "adds no signal" line and the precomputed headlines.
- Standalone packaging.
- Sparse and forested validation. ICESat-2 works outside the US; 3DEP is
  US-only.
- Sub-meter imagery + pubgeo Monocular Geocentric Pose (untested). NAIP via
  Planetary Computer; the AWS naip-* buckets are Requester Pays.
