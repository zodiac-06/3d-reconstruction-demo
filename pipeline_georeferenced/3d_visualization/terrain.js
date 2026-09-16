import * as THREE from 'three';

/**
 * Builds a displaced terrain mesh from a DEM heightmap + satellite orthophoto.
 * @param {string} demPngUrl - URL of the normalized 16-bit grayscale elevation PNG.
 * @param {string} metaJsonUrl - URL of the metadata JSON ({ minElevation, maxElevation, units }).
 * @param {string} satelliteJpgUrl - URL of the RGB orthophoto to drape on top.
 * @param {object} [options]
 * @param {number} [options.baseSize=300] - Longest side of the plane in Three.js world units.
 * @param {number} [options.segmentsX=256] - Grid subdivisions along X.
 * @param {number} [options.segmentsY=256] - Grid subdivisions along Y (Z after rotation).
 * @param {number} [options.verticalExaggeration=1.5] - Visual height emphasis multiplier.
 * @param {THREE.WebGLRenderer} [options.renderer] - WebGLRenderer instance for anisotropy check.
 * @returns {Promise<{mesh: THREE.Mesh, setVerticalExaggeration: (factor:number)=>void, dispose: ()=>void}>}
 */
export async function createTerrainMesh(demPngUrl, metaJsonUrl, satelliteJpgUrl, options = {}) {
  const {
    baseSize = 300,
    segmentsX = 256,
    segmentsY = 256,
    verticalExaggeration = 1.5,
    renderer = null,
  } = options;

  // 1. Fetch metadata
  const metaResponse = await fetch(metaJsonUrl);
  if (!metaResponse.ok) {
    throw new Error(`Failed to load ${metaJsonUrl}: ${metaResponse.status} ${metaResponse.statusText}`);
  }
  const metadata = await metaResponse.json();
  const { minElevation, maxElevation } = metadata;
  if (typeof minElevation !== 'number' || typeof maxElevation !== 'number') {
    throw new Error(`${metaJsonUrl} is missing numeric minElevation/maxElevation`);
  }

  // 2. Load textures
  const textureLoader = new THREE.TextureLoader();
  const loadTexture = (url) =>
    new Promise((resolve, reject) => {
      textureLoader.load(url, resolve, undefined, (err) =>
        reject(new Error(`Failed to load texture ${url}: ${err?.message || err}`))
      );
    });

  const [demTexture, satelliteTexture] = await Promise.all([
    loadTexture(demPngUrl),
    loadTexture(satelliteJpgUrl),
  ]);

  const maxAnisotropy = renderer ? renderer.capabilities.getMaxAnisotropy() : 1;
  configureTexture(demTexture, maxAnisotropy);
  configureTexture(satelliteTexture, maxAnisotropy);

  // Correct color spaces
  satelliteTexture.colorSpace = THREE.SRGBColorSpace;
  demTexture.colorSpace = THREE.NoColorSpace;

  // 3. Compute dynamic aspect ratio from satellite dimensions
  const imgWidth = satelliteTexture.image.naturalWidth || satelliteTexture.image.width || 1;
  const imgHeight = satelliteTexture.image.naturalHeight || satelliteTexture.image.height || 1;
  const aspect = imgWidth / imgHeight;

  let planeWidth = baseSize;
  let planeHeight = baseSize;
  if (aspect >= 1) {
    planeHeight = baseSize / aspect;
  } else {
    planeWidth = baseSize * aspect;
  }

  // 4. Create Plane Geometry matching dimensions
  const geometry = new THREE.PlaneGeometry(planeWidth, planeHeight, segmentsX, segmentsY);
  geometry.rotateX(-Math.PI / 2);
  geometry.computeVertexNormals();

  // 5. Calculate displacement scale normalized to world units
  // Elevation span is mapped proportionally to the plane size (typical mountain relief ~ 10-25% of width)
  const rawElevationRange = Math.max(1, maxElevation - minElevation);
  const normalizedScaleFactor = (baseSize * 0.18) * (rawElevationRange / 1000); 

  const material = new THREE.MeshStandardMaterial({
    map: satelliteTexture,
    displacementMap: demTexture,
    displacementScale: normalizedScaleFactor * verticalExaggeration,
    displacementBias: 0,
    roughness: 0.85,
    metalness: 0.1,
  });

  const mesh = new THREE.Mesh(geometry, material);
  mesh.receiveShadow = true;
  mesh.castShadow = true;

  function setVerticalExaggeration(factor) {
    material.displacementScale = normalizedScaleFactor * factor;
  }

  function dispose() {
    geometry.dispose();
    material.dispose();
    demTexture.dispose();
    satelliteTexture.dispose();
  }

  return { mesh, setVerticalExaggeration, dispose };
}

function configureTexture(texture, maxAnisotropy) {
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = Math.max(1, maxAnisotropy);
  texture.needsUpdate = true;
}