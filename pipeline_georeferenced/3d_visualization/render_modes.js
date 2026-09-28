/**
 * Viewer render modes (design doc §4.4): elevation heatmap, hillshade and
 * slope map, computed from the same 16-bit DSM the click probe reads. No
 * Three.js dependency, so it runs under node too
 * (dsm_calibration/test_render_modes.py).
 *
 * Gradients use the probe's ±30 m central difference, so the slope map and
 * the click readout agree pixel for pixel.
 */
import { SLOPE_SCALE_M } from './elevation_probe.js';

// Hillshade sun: the cartographic convention, light from the north-west.
export const SUN_AZIMUTH_DEG = 315;
export const SUN_ALTITUDE_DEG = 45;
// Slope colour saturates here; steeper pixels take the darkest step.
export const SLOPE_MAX_DEG = 60;

// One-hue sequential ramps, light -> dark (blue for elevation, orange for
// slope: two sequential scales in one view each get their own hue).
export const RAMPS = {
  elevation: ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'],
  slope: ['#fde6d4', '#f9c39c', '#f29d64', '#e67835', '#c65a1b', '#9a4213', '#6e2e0c'],
};

/**
 * dz/dx (east) and dz/dy (north) in m/m for every pixel, plus slope in
 * degrees. Needs the probe's pixel size; returns null without one.
 */
export function computeGradients(probe) {
  if (!probe.pixelSizeM) return null;
  const { width: W, height: H } = probe;
  const [px, py] = probe.pixelSizeM;
  const hx = Math.max(1, Math.round(SLOPE_SCALE_M / px));
  const hy = Math.max(1, Math.round(SLOPE_SCALE_M / py));
  const z = new Float64Array(W * H);
  for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) z[r * W + c] = probe.elevationAt(c, r);

  const dzdx = new Float32Array(W * H), dzdy = new Float32Array(W * H), slope = new Float32Array(W * H);
  for (let r = 0; r < H; r++) {
    const r0 = Math.max(0, r - hy), r1 = Math.min(H - 1, r + hy);
    for (let c = 0; c < W; c++) {
      const c0 = Math.max(0, c - hx), c1 = Math.min(W - 1, c + hx);
      const gx = (z[r * W + c1] - z[r * W + c0]) / ((c1 - c0) * px);
      // rows run north -> south, so north-positive dz/dy is (upper - lower)
      const gy = (z[r0 * W + c] - z[r1 * W + c]) / ((r1 - r0) * py);
      const i = r * W + c;
      dzdx[i] = gx; dzdy[i] = gy;
      slope[i] = Math.atan(Math.hypot(gx, gy)) * 180 / Math.PI;
    }
  }
  return { dzdx, dzdy, slope };
}

/** Lambertian hillshade in [0, 1] (1 = facing the sun). */
export function hillshade(grad, azimuthDeg = SUN_AZIMUTH_DEG, altitudeDeg = SUN_ALTITUDE_DEG) {
  const az = azimuthDeg * Math.PI / 180, alt = altitudeDeg * Math.PI / 180;
  // unit vector towards the sun: east, north, up
  const sx = Math.sin(az) * Math.cos(alt), sy = Math.cos(az) * Math.cos(alt), sz = Math.sin(alt);
  const out = new Float32Array(grad.dzdx.length);
  for (let i = 0; i < out.length; i++) {
    // surface normal (-dz/dx, -dz/dy, 1), normalised
    const nx = -grad.dzdx[i], ny = -grad.dzdy[i], n = Math.hypot(nx, ny, 1);
    out[i] = Math.max(0, (nx * sx + ny * sy + sz) / n);
  }
  return out;
}

const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

/** Colour at t in [0, 1] along a ramp (linear between its steps). */
export function rampColor(ramp, t) {
  const s = Math.max(0, Math.min(1, t)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(s)), f = s - i;
  const a = hexRgb(ramp[i]), b = hexRgb(ramp[i + 1]);
  return a.map((v, k) => Math.round(v + (b[k] - v) * f));
}

/**
 * RGBA pixels (Uint8ClampedArray, row-major, north up) for a mode:
 * 'elevation' | 'hillshade' | 'slope'. Returns null if the mode needs a
 * pixel size the metadata doesn't give.
 */
export function renderMode(mode, probe, grad) {
  const { width: W, height: H } = probe;
  const px = new Uint8ClampedArray(W * H * 4);
  const put = (i, [r, g, b]) => { px[4 * i] = r; px[4 * i + 1] = g; px[4 * i + 2] = b; px[4 * i + 3] = 255; };
  if (mode === 'elevation') {
    for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) put(r * W + c, rampColor(RAMPS.elevation, probe.normalizedAt(c, r)));
    return px;
  }
  if (!grad) return null;
  if (mode === 'slope') {
    for (let i = 0; i < W * H; i++) put(i, rampColor(RAMPS.slope, grad.slope[i] / SLOPE_MAX_DEG));
    return px;
  }
  if (mode === 'hillshade') {
    const hs = hillshade(grad);
    for (let i = 0; i < W * H; i++) { const v = Math.round(255 * hs[i]); put(i, [v, v, v]); }
    return px;
  }
  throw new Error(`Unknown render mode ${mode}`);
}
