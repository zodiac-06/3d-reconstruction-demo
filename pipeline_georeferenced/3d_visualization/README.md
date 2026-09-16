# Person 3 track: 3D Terrain Visualization — Handover

**Status:** viewer built and verified against placeholder data. **Not yet wired to real pipeline output** — see "Open blocker" below before assuming this connects to Person 1/2's real data.

## What this is

A standalone Three.js viewer that drapes a satellite orthophoto over a displaced terrain mesh, with orbit/fly navigation and a live vertical-exaggeration slider. Plain ES modules loaded via CDN `importmap` (Three.js r0.160) — **no build step, no npm/Node needed.** Open `index.html` through any static file server.

## Files

| File | Purpose |
|---|---|
| `terrain.js` | `createTerrainMesh(demUrl, metaUrl, satUrl, options)` — fetches metadata, loads both textures, builds a UV-mapped `PlaneGeometry` displaced by the DEM, returns `{mesh, setVerticalExaggeration, dispose}` |
| `controls.js` | `setupNavigation(camera, domElement)` — wires `OrbitControls` + `FlyControls` on one camera, toggles between them without losing where the camera is pointed |
| `index.html` | Full viewer: lighting, UI panel (mode toggle, exaggeration slider, nav hints), resize handling, error banner |
| `assets/` | **Placeholder/demo data** — a gradient test image, not real satellite/elevation data |

## Input contract (what real data needs to look like)

```
assets/elevation_16bit.png   — grayscale heightmap, same pixel dimensions as the satellite image
assets/satellite.jpg          — the orthophoto to drape on top
assets/metadata.json          — { "minElevation": <number>, "maxElevation": <number>, "units": "meters" }
```

## Verified working

Ran end-to-end against the placeholder assets via a local static server:
- Mesh loads and displaces correctly, no console errors.
- Orbit rotate/zoom, Fly mode toggle (camera keeps its view across the switch), and the exaggeration slider (tested 1.5x → 4.0x live) all work.
- Confirmed with real screenshots, not just code review.

## Known deviations / limitations — read before demo day

1. **Height scale is a visual heuristic, not true-to-scale.** `displacementScale` is currently `baseSize * 0.18 * (elevationRange / 1000) * exaggeration` — tuned to look like plausible relief on a 300-unit plane, **not** the real elevation-to-distance ratio. Don't describe the 3D model as "to scale" in the pitch as-is.

   **There's a real fix available now that we didn't have before:** Person 1's actual `geo_metadata.json` gives the AOI's real extent — **8840m × 7870m**. If the team wants a genuinely to-scale model, set `width`/`height` in `createTerrainMesh` to those real meter values and drop the heuristic in favor of `displacementScale = elevationRange * exaggeration` directly (real meters in, real meters out, with `exaggeration` then purely a deliberate dramatization slider). This is a design choice, not a bug fix — flag it to whoever owns the pitch narrative.

2. **16-bit PNG precision is browser-limited, not a bug in this code.** Canvas-based texture decoding (what `THREE.TextureLoader` relies on) flattens any 16-bit grayscale PNG to 8-bit before the GPU ever sees it. If the real DEM shows visible elevation banding, the fix has to happen upstream (bake in a hillshade, or move to an EXR-based heightmap), not here.

3. **Normals are computed once on the flat plane, before GPU-side displacement.** Displacement happens in the vertex shader, so lighting on steep slopes depends on segment density (currently 256×256) rather than per-vertex-accurate normals. Bump `segmentsX`/`segmentsY` if slopes look faceted with higher-res real data.

## Open blocker — real data won't plug in as-is

Checked Person 1's actual committed output (`qgis_prep/data/`) directly. It does **not** match this viewer's input contract:

| This viewer expects | Person 1's pipeline actually produces |
|---|---|
| `elevation_16bit.png` (PNG) | `srtm_dem_aligned.tif` (**GeoTIFF**) |
| `satellite.jpg` (JPG) | `aoi_cropped.tif` (**GeoTIFF**) |
| `metadata.json` → `{minElevation, maxElevation}` | `geo_metadata.json` → CRS, affine transform, bounds (**no elevation min/max**) |

Browsers cannot load `.tif` as a texture at all — this isn't a code fix, it's a missing conversion step. **Someone needs to own converting the two GeoTIFFs into a PNG + JPG, and computing real min/max elevation from the DEM's pixel data into the metadata schema above.** As of this handover, Person 2's branch (`srtm-calibration`) hasn't been pushed to GitHub, so it's unconfirmed whether this is their job or needs a separate owner.

## Repo state

- Branch: `dsm-pivot-3d-visualization`, pushed to `origin`.
- PR into `dsm-pivot` not yet opened: https://github.com/zodiac-06/3d-reconstruction-demo/pull/new/dsm-pivot-3d-visualization

## Next steps

1. Open the PR into `dsm-pivot`.
2. Get an explicit owner for the GeoTIFF → PNG/JPG + elevation-metadata conversion step.
3. Once real assets land: confirm they share the same aspect ratio as each other (mismatched crops between DEM and satellite will misalign contours), and raise `segmentsX`/`segmentsY` if resolution is higher than 256×256.
4. Team decision needed: keep the current "looks good" height heuristic, or switch to true-to-scale using the real 8840m×7870m AOI extent (see point 1 above).
