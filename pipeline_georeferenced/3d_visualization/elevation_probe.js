/**
 * Click-to-probe math for the terrain viewer: lat/lon, elevation and slope
 * at a pixel of the DSM. No Three.js dependency, so it also runs under node
 * (see dsm_calibration/test_viewer_probe.py).
 *
 * Elevation comes from the 16-bit PNG + metadata min/max, never from the
 * displaced mesh, so it doesn't change with the vertical-exaggeration slider.
 * The PNG is decoded here rather than through a canvas: canvas decoding
 * flattens 16-bit PNGs to 8 bits (~6.7m steps over Nainital's range).
 */

// Slope is a central difference over +/- this many metres: the terrain DEM's
// ~30m posting, so the readout doesn't imply detail the DEM doesn't have.
export const SLOPE_SCALE_M = 30;

const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

/** Decode a 16-bit grayscale, non-interlaced PNG to {width, height, data: Uint16Array}. */
export async function decodeGray16Png(buffer) {
  const bytes = new Uint8Array(buffer);
  if (!PNG_SIGNATURE.every((b, i) => bytes[i] === b)) throw new Error('Not a PNG');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  let pos = 8, width = 0, height = 0;
  const idat = [];
  while (pos + 8 <= bytes.length) {
    const len = view.getUint32(pos);
    const type = String.fromCharCode(...bytes.subarray(pos + 4, pos + 8));
    const body = bytes.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = view.getUint32(pos + 8);
      height = view.getUint32(pos + 12);
      const [bitDepth, colorType, , , interlace] = body.subarray(8, 13);
      if (bitDepth !== 16 || colorType !== 0 || interlace !== 0) {
        throw new Error(`Expected a 16-bit grayscale non-interlaced PNG, got bit depth ${bitDepth}, ` +
                        `color type ${colorType}, interlace ${interlace}`);
      }
    } else if (type === 'IDAT') {
      idat.push(body);
    } else if (type === 'IEND') {
      break;
    }
    pos += 12 + len;
  }

  const inflated = new Uint8Array(await new Response(
    new Blob(idat).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer());

  const bpp = 2, stride = width * bpp;
  if (inflated.length !== height * (stride + 1)) {
    throw new Error(`PNG data is ${inflated.length} bytes, expected ${height * (stride + 1)}`);
  }
  const raw = new Uint8Array(height * stride);
  for (let r = 0; r < height; r++) {
    const filter = inflated[r * (stride + 1)];
    const src = inflated.subarray(r * (stride + 1) + 1, (r + 1) * (stride + 1));
    const out = raw.subarray(r * stride, (r + 1) * stride);
    const prev = r > 0 ? raw.subarray((r - 1) * stride, r * stride) : null;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? out[i - bpp] : 0;
      const b = prev ? prev[i] : 0;
      const c = prev && i >= bpp ? prev[i - bpp] : 0;
      let pred;
      switch (filter) {
        case 0: pred = 0; break;
        case 1: pred = a; break;
        case 2: pred = b; break;
        case 3: pred = (a + b) >> 1; break;
        case 4: {
          const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
          break;
        }
        default: throw new Error(`Bad PNG filter type ${filter} on row ${r}`);
      }
      out[i] = (src[i] + pred) & 0xff;
    }
  }

  const data = new Uint16Array(width * height);
  for (let i = 0; i < data.length; i++) data[i] = (raw[2 * i] << 8) | raw[2 * i + 1];
  return { width, height, data };
}

// WGS84 inverse transverse Mercator (Snyder, Map Projections, eqs. 8-18..8-25).
function utmToLatLon(x, y, zone, south) {
  const a = 6378137, f = 1 / 298.257223563, k0 = 0.9996;
  const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const lon0 = ((zone - 1) * 6 - 180 + 3) * Math.PI / 180;

  const M = (south ? y - 10000000 : y) / k0;
  const mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256));
  const phi1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu)
    + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + (151 * e1 ** 3 / 96) * Math.sin(6 * mu)
    + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu);

  const sin1 = Math.sin(phi1), cos1 = Math.cos(phi1), tan1 = Math.tan(phi1);
  const C1 = ep2 * cos1 ** 2, T1 = tan1 ** 2;
  const N1 = a / Math.sqrt(1 - e2 * sin1 ** 2);
  const R1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5;
  const D = (x - 500000) / (N1 * k0);

  const lat = phi1 - (N1 * tan1 / R1) * (D ** 2 / 2
    - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
    + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720);
  const lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
    + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120) / cos1;
  return { lat: lat * 180 / Math.PI, lon: lon * 180 / Math.PI };
}

/**
 * Pixel size (metres) and pixel -> lat/lon from metadata.crs + bounds, or
 * null if the metadata has no georeference this code understands (UTM
 * WGS84 zones, EPSG:326xx/327xx, or EPSG:4326). Bounds are pixel-edge
 * bounds of the whole image, as rasterio reports them.
 */
export function georeference(metadata, width, height) {
  const code = Number(String(metadata.crs || '').replace(/^EPSG:/i, ''));
  const nb = metadata.boundsNative;
  if (nb && ((code >= 32601 && code <= 32660) || (code >= 32701 && code <= 32760))) {
    const zone = code % 100, south = code >= 32701;
    const px = (nb.right - nb.left) / width, py = (nb.top - nb.bottom) / height;
    return {
      pixelSizeM: [px, py],
      latLonAt: (col, row) => utmToLatLon(nb.left + (col + 0.5) * px, nb.top - (row + 0.5) * py, zone, south),
    };
  }
  const gb = metadata.boundsEPSG4326;
  if (gb && code === 4326) {
    const dLon = (gb.east - gb.west) / width, dLat = (gb.north - gb.south) / height;
    const phi = ((gb.north + gb.south) / 2) * Math.PI / 180;
    // WGS84 meridional / prime-vertical radii at the AOI's centre latitude.
    const a = 6378137, e2 = 0.00669437999014, w = 1 - e2 * Math.sin(phi) ** 2;
    const mPerDegLat = (Math.PI / 180) * a * (1 - e2) / w ** 1.5;
    const mPerDegLon = (Math.PI / 180) * a * Math.cos(phi) / Math.sqrt(w);
    return {
      pixelSizeM: [dLon * mPerDegLon, dLat * mPerDegLat],
      latLonAt: (col, row) => ({ lat: gb.north - (row + 0.5) * dLat, lon: gb.west + (col + 0.5) * dLon }),
    };
  }
  return null;
}

/** Probe over a decoded grid ({width, height, data}) and its metadata.json. */
export function createElevationProbe(grid, metadata) {
  const { width, height, data } = grid;
  const { minElevation, maxElevation } = metadata;
  const geo = georeference(metadata, width, height);

  const clampCol = c => Math.min(width - 1, Math.max(0, c));
  const clampRow = r => Math.min(height - 1, Math.max(0, r));
  const normalizedAt = (col, row) => data[row * width + col] / 65535;
  const elevationAt = (col, row) => minElevation + normalizedAt(col, row) * (maxElevation - minElevation);

  function slopeDegAt(col, row) {
    if (!geo) return null;
    const [px, py] = geo.pixelSizeM;
    const hx = Math.max(1, Math.round(SLOPE_SCALE_M / px));
    const hy = Math.max(1, Math.round(SLOPE_SCALE_M / py));
    const c0 = clampCol(col - hx), c1 = clampCol(col + hx);
    const r0 = clampRow(row - hy), r1 = clampRow(row + hy);
    const dzdx = (elevationAt(c1, row) - elevationAt(c0, row)) / ((c1 - c0) * px);
    const dzdy = (elevationAt(col, r1) - elevationAt(col, r0)) / ((r1 - r0) * py);
    return Math.atan(Math.hypot(dzdx, dzdy)) * 180 / Math.PI;
  }

  function probePixel(col, row) {
    col = clampCol(Math.round(col));
    row = clampRow(Math.round(row));
    const ll = geo ? geo.latLonAt(col, row) : null;
    return {
      col, row,
      lat: ll ? ll.lat : null,
      lon: ll ? ll.lon : null,
      elevationM: elevationAt(col, row),
      slopeDeg: slopeDegAt(col, row),
    };
  }

  // Bilinear, pixel-centre convention (matches the GPU texture sampling).
  function normalizedBilinear(colf, rowf) {
    const c = Math.min(width - 1, Math.max(0, colf)), r = Math.min(height - 1, Math.max(0, rowf));
    const c0 = Math.floor(c), r0 = Math.floor(r);
    const c1 = Math.min(width - 1, c0 + 1), r1 = Math.min(height - 1, r0 + 1);
    const fc = c - c0, fr = r - r0;
    return (normalizedAt(c0, r0) * (1 - fc) + normalizedAt(c1, r0) * fc) * (1 - fr)
         + (normalizedAt(c0, r1) * (1 - fc) + normalizedAt(c1, r1) * fc) * fr;
  }

  return {
    width, height, pixelSizeM: geo ? geo.pixelSizeM : null,
    normalizedAt, elevationAt, slopeDegAt, probePixel, normalizedBilinear,
  };
}

/**
 * Where a ray first hits the displaced terrain, as a continuous pixel
 * position. Three.js raycasting ignores displacementMap (it's applied on
 * the GPU), so a raycast against the mesh would hit the flat plane instead.
 *
 * Plane layout matches terrain.js: centred at the origin, planeWidth along
 * +x (image columns), planeHeight along +z (image rows, north at -z),
 * height = displacementScale * normalized elevation along +y.
 * origin/dir are {x, y, z} in the mesh's local frame. Returns
 * {x, y, z, col, row} or null if the ray misses.
 */
export function intersectHeightfield(probe, origin, dir, planeWidth, planeHeight, displacementScale) {
  const len = Math.hypot(dir.x, dir.y, dir.z);
  const d = { x: dir.x / len, y: dir.y / len, z: dir.z / len };
  const lo = { x: -planeWidth / 2, y: 0, z: -planeHeight / 2 };
  // Top padded so a ray entering over the highest pixel starts above it.
  const hi = { x: planeWidth / 2, y: displacementScale * 1.001 + 1e-6, z: planeHeight / 2 };

  let tMin = 0, tMax = Infinity;
  for (const k of ['x', 'y', 'z']) {
    if (Math.abs(d[k]) < 1e-12) {
      if (origin[k] < lo[k] || origin[k] > hi[k]) return null;
      continue;
    }
    let t0 = (lo[k] - origin[k]) / d[k], t1 = (hi[k] - origin[k]) / d[k];
    if (t0 > t1) [t0, t1] = [t1, t0];
    tMin = Math.max(tMin, t0);
    tMax = Math.min(tMax, t1);
    if (tMin > tMax) return null;
  }

  const toPixel = (x, z) => ({
    colf: (x / planeWidth + 0.5) * probe.width - 0.5,
    rowf: (z / planeHeight + 0.5) * probe.height - 0.5,
  });
  const above = t => {
    const x = origin.x + t * d.x, y = origin.y + t * d.y, z = origin.z + t * d.z;
    const { colf, rowf } = toPixel(x, z);
    return y - displacementScale * probe.normalizedBilinear(colf, rowf);
  };

  const step = 0.5 * Math.min(planeWidth / probe.width, planeHeight / probe.height);
  let tPrev = tMin;
  if (above(tPrev) <= 0) return null; // ray starts inside the terrain
  for (let t = tMin + step; ; t += step) {
    const tc = Math.min(t, tMax);
    if (above(tc) <= 0) {
      let a = tPrev, b = tc;
      for (let i = 0; i < 30; i++) {
        const m = (a + b) / 2;
        if (above(m) > 0) a = m; else b = m;
      }
      const x = origin.x + b * d.x, y = origin.y + b * d.y, z = origin.z + b * d.z;
      const { colf, rowf } = toPixel(x, z);
      return { x, y, z, col: Math.round(colf), row: Math.round(rowf) };
    }
    if (tc >= tMax) return null;
    tPrev = tc;
  }
}
