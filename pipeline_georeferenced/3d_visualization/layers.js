/**
 * Analysis layers for the 3D viewer, beside render_modes.js: confidence
 * (agreement between independent DEMs), provenance (where each height came
 * from) and the two-point measure tool. No Three.js dependency, so it runs
 * under node too (dsm_calibration/test_layers.py).
 */
import { rampColor } from './render_modes.js';

// ----- Confidence: DEM spread classes from GET /jobs/{id}/confidence -----

export const CONFIDENCE_COLORS = { high: '#43a047', medium: '#fbc02d', low: '#e53935' };

/**
 * RGBA pixels for the confidence view. spread: Float32Array (row-major, NaN =
 * fewer than two DEMs -> transparent). classes: the endpoint's classes, in
 * order, each {name, min_m, max_m (null = unbounded)}; a pixel takes the
 * first class whose max_m it doesn't exceed.
 */
export function confidencePixels(spread, classes) {
  const px = new Uint8ClampedArray(spread.length * 4);
  const cols = classes.map(c => hexRgb(CONFIDENCE_COLORS[c.name] || '#9e9e9e'));
  for (let i = 0; i < spread.length; i++) {
    const v = spread[i];
    if (!Number.isFinite(v)) continue;  // alpha stays 0: the satellite shows through
    let k = classes.findIndex(c => c.max_m === null || v <= c.max_m);
    if (k < 0) k = classes.length - 1;
    const [r, g, b] = cols[k];
    px[4 * i] = r; px[4 * i + 1] = g; px[4 * i + 2] = b; px[4 * i + 3] = 255;
  }
  return px;
}

// ----- Provenance: DEM base + Depth Anything detail (provenance_16bit.png) -----

// Diverging, blue = the depth layer lowered the DEM, red = raised it.
export const PROVENANCE_RAMP = ['#2166ac', '#67a9cf', '#d1e5f0', '#f7f7f7', '#fddbc7', '#ef8a62', '#b2182b'];
export const NO_DATA_COLOR = '#8e24aa';

/** Detail (DSM - DEM, metres) per pixel from the decoded PNG; NaN = no data. */
export function decodeProvenance(grid, prov) {
  const { data } = grid, out = new Float32Array(data.length);
  const span = prov.detailMax - prov.detailMin;
  for (let i = 0; i < data.length; i++) {
    out[i] = data[i] === prov.nodataValue ? NaN : prov.detailMin + (data[i] - 1) / 65534 * span;
  }
  return out;
}

/** Symmetric colour range: the larger of |p2| and |p98|, at least 0.1 m. */
export function symmetricRange(detail) {
  const v = Array.from(detail).filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return 0.1;
  const q = p => v[Math.min(v.length - 1, Math.max(0, Math.round(p * (v.length - 1))))];
  return Math.max(0.1, Math.ceil(Math.max(Math.abs(q(0.02)), Math.abs(q(0.98))) * 10) / 10);
}

export function provenancePixels(detail, range) {
  const px = new Uint8ClampedArray(detail.length * 4);
  const none = hexRgb(NO_DATA_COLOR);
  for (let i = 0; i < detail.length; i++) {
    const v = detail[i];
    const [r, g, b] = Number.isFinite(v) ? rampColor(PROVENANCE_RAMP, (v + range) / (2 * range)) : none;
    px[4 * i] = r; px[4 * i + 1] = g; px[4 * i + 2] = b; px[4 * i + 3] = 255;
  }
  return px;
}

// ----- Measure: distance, height difference and profile between two pixels -----

/**
 * a, b: {col, row} (continuous pixel positions are fine). Needs the probe's
 * pixel size for distances; returns null without one. Heights are the DSM,
 * bilinearly sampled (independent of vertical exaggeration).
 */
export function measure(probe, meta, a, b, samples = 200) {
  if (!probe.pixelSizeM) return null;
  const [px, py] = probe.pixelSizeM;
  const range = meta.maxElevation - meta.minElevation;
  const zAt = (c, r) => meta.minElevation + probe.normalizedBilinear(c, r) * range;
  const horizontalM = Math.hypot((b.col - a.col) * px, (b.row - a.row) * py);
  const profile = [];
  let climb = 0, descent = 0;
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const col = a.col + (b.col - a.col) * t, row = a.row + (b.row - a.row) * t;
    const z = zAt(col, row);
    if (i) { const d = z - profile[i - 1].z; if (d > 0) climb += d; else descent -= d; }
    profile.push({ t, d: horizontalM * t, col, row, z });
  }
  const za = profile[0].z, zb = profile[samples].z, dh = zb - za;
  const zs = profile.map(p => p.z);
  return {
    horizontalM, dh, slopeDistM: Math.hypot(horizontalM, dh),
    gradePct: horizontalM > 0 ? 100 * dh / horizontalM : null,
    angleDeg: Math.atan2(dh, horizontalM) * 180 / Math.PI,
    climb, descent, minZ: Math.min(...zs), maxZ: Math.max(...zs), za, zb, profile,
  };
}

function hexRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
