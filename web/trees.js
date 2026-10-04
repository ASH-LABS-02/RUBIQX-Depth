// Instanced tree visuals for roof-fit City view (display layer only; DSM grid unchanged).
import * as THREE from 'three';

/** Same default as depthwizard/buildings.py `min_height_m` (LoD1 footprint / structure minimum). */
export const CANOPY_MIN_AGL_M = 2.5;

const MAX_INSTANCES = 30000;
const TARGET_SPACING_M = 6;
const TRUNK_BROWN = 0x5c4033;
const TREE_LOD_STATE = new WeakMap();
const TREE_LOD_MIN_PIXELS = { performance: 38, balanced: 24, cinematic: 15 };
const ignoreTreeRaycast = () => {};

function hash01(n) {
  let h = (n * 2654435761) >>> 0;
  h = ((h >>> 16) ^ h) * 0x45d9f3b;
  h = ((h >>> 16) ^ h) >>> 0;
  return (h % 10000) / 10000;
}

function readTexturePixels(texImg) {
  if (!texImg?.width) return null;
  const w = texImg.width;
  const h = texImg.height;
  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(texImg, 0, 0, w, h);
  return { data: ctx.getImageData(0, 0, w, h).data, w, h };
}

function textureLooksGreyscale(pixels) {
  if (!pixels) return true;
  const { data, w, h } = pixels;
  let sumGr = 0;
  let sumGb = 0;
  let n = 0;
  const step = Math.max(4, Math.floor(Math.min(w, h) / 64));
  for (let y = 0; y < h; y += step) {
    for (let x = 0; x < w; x += step) {
      const i = (y * w + x) * 4;
      sumGr += Math.abs(data[i + 1] - data[i]);
      sumGb += Math.abs(data[i + 1] - data[i + 2]);
      n++;
    }
  }
  if (!n) return true;
  return sumGr / n < 10 && sumGb / n < 10;
}

function greennessAt(pixels, gw, gh, c, r, skipGreen) {
  if (skipGreen || !pixels) return true;
  const { data, w, h } = pixels;
  const u = c / Math.max(1, gw - 1);
  const v = 1 - r / Math.max(1, gh - 1);
  const x = Math.min(w - 1, Math.max(0, Math.round(u * (w - 1))));
  const y = Math.min(h - 1, Math.max(0, Math.round(v * (h - 1))));
  const i = (y * w + x) * 4;
  const R = data[i];
  const G = data[i + 1];
  const B = data[i + 2];
  const margin = 4;
  return G > R + margin && G > B + margin;
}

export function analyzeCanopy({ h, dtm, buildingMask, semanticLabels, texImg, gw, gh }) {
  const mask = new Uint8Array(gw * gh);
  const elevated = new Uint8Array(mask.length);
  const pixels = readTexturePixels(texImg);
  const skipGreen = textureLooksGreyscale(pixels);
  let aglCells = 0;
  let greenKept = 0;
  for (let i = 0; i < h.length; i++) {
    if (buildingMask?.[i]) continue;
    const agl = h[i] - dtm[i];
    if (!Number.isFinite(agl) || agl < CANOPY_MIN_AGL_M) continue;
    // Canonical labels: unknown=0, building=1, tree/forest=2,
    // ground=3, water=4, road=5. Unknown retains the RGB heuristic.
    const semantic = semanticLabels?.[i] || 0;
    if (semantic && semantic !== 2) continue;
    elevated[i] = 1;
    aglCells++;
    const r = (i / gw) | 0;
    const c = i - r * gw;
    if (semantic !== 2 && !greennessAt(pixels, gw, gh, c, r, skipGreen)) continue;
    greenKept++;
    mask[i] = 1;
  }
  // Flatten complete elevated non-building regions, including connected
  // shadows/brown branches. RGB greenness only controls tree placement.
  // Two cells include the low shoulders of a canopy and smoothing spill.
  // Never consume a mapped building or an invalid bare-ground sample.
  const flattenMask = elevated.slice();
  for (let i = 0; i < elevated.length; i++) {
    if (!elevated[i]) continue;
    const r = (i / gw) | 0, c = i - r * gw;
    for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) {
      const rr = r + dr, cc = c + dc;
      if (rr < 0 || rr >= gh || cc < 0 || cc >= gw) continue;
      const j = rr * gw + cc;
      if (!buildingMask?.[j] && Number.isFinite(dtm[j]) &&
          (!semanticLabels?.[j] || semanticLabels[j] === 2)) flattenMask[j] = 1;
    }
  }
  return {
    mask,
    flattenMask,
    dtm,
    pixels,
    skipGreen,
    aglCells,
    greenKept,
    greenShare: aglCells ? greenKept / aglCells : 0,
  };
}

export function isCanopyCandidate(i, ctx) {
  return Boolean(ctx.mask?.[i]);
}

/** Display-only: drop canopy bumps to bare ground where instanced trees will stand. */
export function flattenCanopyHeights(src, canopy) {
  if (!canopy?.flattenMask || !canopy.dtm) return 0;
  const mask = canopy.flattenMask;
  let flattened = 0;
  for (let i = 0; i < src.length; i++) {
    if (!mask[i]) continue;
    src[i] = canopy.dtm[i];
    flattened++;
  }
  return flattened;
}

export function disposeTreeGroup(group) {
  if (!group) return;
  const geometries = new Set(), materials = new Set();
  group.traverse((c) => {
    if (c.geometry) geometries.add(c.geometry);
    if (Array.isArray(c.material)) c.material.forEach((m) => materials.add(m));
    else if (c.material) materials.add(c.material);
    // Instance buffers belong to each mesh, while geometry/materials are
    // shared by every spatial chunk and must be released only once.
    if (c.isInstancedMesh) c.dispose?.();
  });
  geometries.forEach((g) => g.dispose());
  materials.forEach((m) => m.dispose());
  TREE_LOD_STATE.delete(group);
}

/** Counts describe the active representation before renderer frustum culling. */
export function getTreeLodDiagnostics(group) {
  const state = group && TREE_LOD_STATE.get(group);
  if (!state) return null;
  let nearTrees = 0, distantTrees = 0, crownInstances = 0, branchInstances = 0;
  let estimatedTriangles = 0, estimatedDrawCalls = 0;
  for (const chunk of state.chunks) {
    if (chunk.near) {
      nearTrees += chunk.count;
      crownInstances += chunk.crowns;
      branchInstances += chunk.count * 4;
      estimatedTriangles += chunk.nearTriangles;
      estimatedDrawCalls += 3;
    } else {
      distantTrees += chunk.count;
      crownInstances += chunk.count;
      estimatedTriangles += chunk.distantTriangles;
      estimatedDrawCalls += 2;
    }
  }
  return {
    quality: state.quality,
    instanceCount: state.count,
    nearTrees,
    distantTrees,
    chunkCount: state.chunks.length,
    crownInstances,
    branchInstances,
    trunkInstances: state.count,
    estimatedTriangles,
    estimatedDrawCalls,
    detailedTriangles: state.detailedTriangles,
    nearShadowTrees: state.quality === 'performance' ? 0 : nearTrees,
    visible: group.visible,
    updates: state.updates,
  };
}

/**
 * Switch static chunk representations; no per-frame instance matrix uploads.
 * Screen size combines camera distance, lens, viewport and metric tree scale.
 * `force` is useful immediately after rebuilding or changing quality.
 * @returns {boolean} Whether any mesh visibility/shadow setting changed.
 */
export function updateTreeLod(group, camera, {
  quality = 'balanced', viewportHeight = 720, now = performance.now(),
  force = false, updateIntervalMs = 180,
} = {}) {
  const state = group && TREE_LOD_STATE.get(group);
  if (!state || !camera || (!group.visible && !force)) return false;
  let fading = false;
  for (const chunk of state.chunks) {
    if (chunk.fadeStarted == null) continue;
    const t = Math.min(1, (now - chunk.fadeStarted) / 250);
    const nearOpacity = chunk.near ? t : 1 - t;
    for (const mesh of chunk.detailed.children) mesh.material.opacity = nearOpacity;
    for (const mesh of chunk.distant.children) mesh.material.opacity = 1 - nearOpacity;
    chunk.detailed.visible = nearOpacity > 0;
    chunk.distant.visible = nearOpacity < 1;
    if (t === 1) chunk.fadeStarted = null;
    fading = true;
  }
  quality = Object.hasOwn(TREE_LOD_MIN_PIXELS, quality) ? quality : 'balanced';
  viewportHeight = Number.isFinite(viewportHeight) && viewportHeight > 0 ? viewportHeight : 720;
  const qualityChanged = quality !== state.quality;
  if (!force && !qualityChanged && now - state.lastUpdate < Math.max(0, updateIntervalMs)) return fading;

  camera.getWorldPosition(state.cameraWorld);
  group.updateWorldMatrix(true, false);
  state.inverse.copy(group.matrixWorld).invert();
  state.cameraLocal.copy(state.cameraWorld).applyMatrix4(state.inverse);
  const lens = camera.isOrthographicCamera
    ? (camera.top - camera.bottom) / (camera.zoom || 1)
    : camera.getEffectiveFOV?.() || camera.fov || 55;
  const viewChanged = state.lastViewportHeight !== viewportHeight || state.lastLens !== lens;
  if (!force && !qualityChanged && !viewChanged &&
      state.cameraLocal.distanceToSquared(state.lastCamera) < 1e-8) return fading;
  state.lastUpdate = now;
  state.lastCamera.copy(state.cameraLocal);
  state.lastViewportHeight = viewportHeight;
  state.lastLens = lens;
  state.quality = quality;
  state.updates++;
  const focalPixels = camera.isOrthographicCamera ? viewportHeight / Math.max(1e-6, lens)
    : viewportHeight / (2 * Math.tan(THREE.MathUtils.degToRad(lens / 2)));
  const minPixels = TREE_LOD_MIN_PIXELS[quality];
  // Account for a transformed parent without changing any tree dimensions.
  const matrix = group.matrixWorld.elements;
  const groupScale = Math.max(Math.hypot(matrix[0], matrix[1], matrix[2]),
    Math.hypot(matrix[4], matrix[5], matrix[6]), Math.hypot(matrix[8], matrix[9], matrix[10]));
  let changed = fading;
  for (const chunk of state.chunks) {
    const distance = Math.max(1e-6, chunk.bounds.distanceToPoint(state.cameraLocal));
    const pixels = camera.isOrthographicCamera ? chunk.crownSize * groupScale * focalPixels
      : chunk.crownSize * focalPixels / distance;
    // A 30% dead band avoids flicker when orbiting around a chunk boundary.
    const near = pixels >= minPixels * (chunk.near ? .85 : 1.15);
    if (near !== chunk.near) {
      chunk.near = near;
      chunk.fadeStarted = now;
      chunk.detailed.visible = chunk.distant.visible = true;
      changed = true;
    }
    const shadows = quality !== 'performance';
    if (chunk.shadowEnabled !== shadows) {
      chunk.shadowEnabled = shadows;
      for (const mesh of chunk.detailed.children) {
        mesh.castShadow = shadows;
        mesh.receiveShadow = shadows;
      }
      changed = true;
    }
  }
  return changed;
}

function crownColor(seed) {
  return new THREE.Color().setHSL(
    0.28 + hash01(seed * 19) * 0.06,
    0.32 + hash01(seed * 23) * 0.20,
    0.16 + hash01(seed * 29) * 0.09,
  );
}

function trunkRadiusFor(aglM) {
  return Math.min(0.45, Math.max(0.12, aglM * 0.025));
}

/**
 * @returns {{ group: THREE.Group|null, instanceCount: number, stats: object }}
 */
export function buildTreeGroup({ h, dtm, buildingMask, gw, gh, W, H, groundWm = W, groundHm = H,
  worldY, units = 'metre', texImg, canopy: canopyIn }) {
  if (!dtm || units !== 'metre' || gw < 2 || gh < 2 || !(groundWm > 0 && groundHm > 0))
    return { group: null, instanceCount: 0, stats: {} };
  const canopy = canopyIn || analyzeCanopy({ h, dtm, buildingMask, texImg, gw, gh });
  canopy.dtm = dtm;

  const scaleX = W / groundWm, scaleZ = H / groundHm;
  const cellX = groundWm / (gw - 1), cellZ = groundHm / (gh - 1);
  let stepC = Math.max(1, Math.ceil(TARGET_SPACING_M / cellX));
  let stepR = Math.max(1, Math.ceil(TARGET_SPACING_M / cellZ));
  const dummy = new THREE.Object3D();

  const collect = () => {
    const out = [];
    for (let r = 0; r < gh; r += stepR) {
      for (let c = 0; c < gw; c += stepC) {
        // One deterministic candidate per ~6 m block; use grid coordinates
        // so its base coincides with the flattened DTM vertex.
        let ii = -1, best = Infinity;
        for (let rr = r; rr < Math.min(gh, r + stepR); rr++) {
          for (let cc = c; cc < Math.min(gw, c + stepC); cc++) {
            const i = rr * gw + cc;
            if (!isCanopyCandidate(i, canopy) || buildingMask?.[i]) continue;
            const rank = hash01(i * 3 + 1);
            if (rank < best) { best = rank; ii = i; }
          }
        }
        if (ii < 0) continue;
        const rr = (ii / gw) | 0, cc = ii - rr * gw;
        const agl = h[ii] - dtm[ii];
        if (!Number.isFinite(dtm[ii]) || !Number.isFinite(agl) || agl < CANOPY_MIN_AGL_M) continue;
        const x = (cc / (gw - 1) - 0.5) * W;
        const z = (rr / (gh - 1) - 0.5) * H;
        const form = Math.floor(hash01(ii * 47) * 3); // broad, slender or irregular
        out.push({ x, z, ground: dtm[ii], agl, seed: ii, form,
          blobs: form === 0 ? 4 : form === 1 ? 5 : 6 });
        if (out.length > MAX_INSTANCES) return out;
      }
    }
    return out;
  };

  let trees = collect();
  while (trees.length > MAX_INSTANCES) {
    stepC = Math.ceil(stepC * 1.1);
    stepR = Math.ceil(stepR * 1.1);
    trees = collect();
  }

  const stats = {
    aglCells: canopy.aglCells,
    canopyCells: canopy.greenKept,
    greenShare: canopy.greenShare,
    skipGreen: canopy.skipGreen,
    instanceCount: trees.length,
    gridStep: [stepC, stepR],
  };

  if (!trees.length) return { group: null, instanceCount: 0, stats };

  const trunkGeo = new THREE.CylinderGeometry(0.7, 1, 1, 6);
  trunkGeo.translate(0, 0.5, 0);
  trunkGeo.computeVertexNormals();
  const branchGeo = new THREE.CylinderGeometry(0.25, 1, 1, 5);
  branchGeo.translate(0, 0.5, 0);
  const crownGeo = new THREE.IcosahedronGeometry(1, 1);
  // Uneven leaf-clump silhouettes, shared by every instance. Equal positions
  // receive equal deformation so neighbouring triangles never open cracks.
  const crownPos = crownGeo.attributes.position;
  for (let i = 0; i < crownPos.count; i++) {
    const x = crownPos.getX(i), y = crownPos.getY(i), z = crownPos.getZ(i);
    const ripple = 0.88 + 0.12 * Math.sin(x * 11 + y * 7) * Math.cos(z * 13 - y * 5);
    crownPos.setXYZ(i, x * ripple, y * ripple, z * ripple);
  }
  crownGeo.computeVertexNormals();
  crownGeo.computeBoundingBox();
  const crownMinY = crownGeo.boundingBox.min.y, crownMaxY = crownGeo.boundingBox.max.y;

  const trunkMat = new THREE.MeshLambertMaterial({ color: TRUNK_BROWN, flatShading: true });
  // Instanced colours are independent of geometry vertex colours. This
  // geometry has no colour attribute, so vertexColors must remain disabled.
  const crownMat = new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true });
  const distantTrunkGeo = new THREE.CylinderGeometry(0.7, 1, 1, 4);
  distantTrunkGeo.translate(0, .5, 0);
  const distantCrownGeo = new THREE.IcosahedronGeometry(1, 0);
  distantCrownGeo.computeBoundingBox();
  const distantMinY = distantCrownGeo.boundingBox.min.y;
  const distantMaxY = distantCrownGeo.boundingBox.max.y;

  // Bound the number of spatial chunks by candidate count. Empty bins create
  // no meshes; all candidates retain their original coordinates and seed.
  const targetChunks = Math.min(128, Math.max(1, Math.ceil(trees.length / 256)));
  const aspect = THREE.MathUtils.clamp(groundWm / groundHm, 1 / 16, 16);
  const columns = Math.min(targetChunks, Math.max(1, Math.ceil(Math.sqrt(targetChunks * aspect))));
  const rows = Math.max(1, Math.floor(targetChunks / columns));
  const buckets = new Map();
  for (const tree of trees) {
    const c = THREE.MathUtils.clamp(Math.floor((tree.x / W + .5) * columns), 0, columns - 1);
    const r = THREE.MathUtils.clamp(Math.floor((tree.z / H + .5) * rows), 0, rows - 1);
    const key = r * columns + c;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(tree);
  }
  const group = new THREE.Group();
  group.name = 'treeInstances';
  const state = {
    count: trees.length, chunks: [], quality: 'balanced', updates: 0, lastUpdate: -Infinity,
    detailedTriangles: 0, lastViewportHeight: 0, lastLens: 0,
    cameraWorld: new THREE.Vector3(), cameraLocal: new THREE.Vector3(),
    lastCamera: new THREE.Vector3(Infinity, Infinity, Infinity), inverse: new THREE.Matrix4(),
  };
  const triangleCount = (geo) => (geo.index?.count ?? geo.attributes.position.count) / 3;
  for (const [key, chunkTrees] of buckets) {
    const chunkCrowns = chunkTrees.reduce((n, t) => n + t.blobs, 0);
    const trunkMesh = new THREE.InstancedMesh(trunkGeo, trunkMat, chunkTrees.length);
    const branchMesh = new THREE.InstancedMesh(branchGeo, trunkMat, chunkTrees.length * 4);
    const crownMesh = new THREE.InstancedMesh(crownGeo, crownMat, chunkCrowns);
    const distantTrunkMesh = new THREE.InstancedMesh(distantTrunkGeo, trunkMat, chunkTrees.length);
    const distantCrownMesh = new THREE.InstancedMesh(distantCrownGeo, crownMat, chunkTrees.length);
    trunkMesh.name = 'treeTrunks'; branchMesh.name = 'treeBranches'; crownMesh.name = 'treeFoliage';
    distantTrunkMesh.name = 'distantTreeTrunks'; distantCrownMesh.name = 'distantTreeFoliage';
    trunkMesh.castShadow = branchMesh.castShadow = crownMesh.castShadow = true;
    trunkMesh.receiveShadow = branchMesh.receiveShadow = crownMesh.receiveShadow = true;
    // The distant layer remains unshadowed at every quality setting.
    distantTrunkMesh.castShadow = distantCrownMesh.castShadow = false;
    distantTrunkMesh.receiveShadow = distantCrownMesh.receiveShadow = false;
    for (const mesh of [trunkMesh, branchMesh, crownMesh, distantTrunkMesh, distantCrownMesh]) {
      mesh.raycast = ignoreTreeRaycast;
    }
    const up = new THREE.Vector3(0, 1, 0), branchDirection = new THREE.Vector3();

    let crownIndex = 0, crownSizeSum = 0;
    for (let n = 0; n < chunkTrees.length; n++) {
      const t = chunkTrees[n];
      const rot = hash01(t.seed * 13) * Math.PI * 2;
      const crownR = Math.min(7, Math.max(1.5, (0.3 + hash01(t.seed * 17) * 0.1) * t.agl));
      const trunkR = trunkRadiusFor(t.agl);
      const groundY = worldY(t.ground);
      const trunkH = worldY(t.ground + t.agl * 0.3) - groundY;

      dummy.position.set(t.x, groundY, t.z);
      dummy.rotation.set(0, rot, 0);
      dummy.scale.set(trunkR * scaleX, trunkH, trunkR * scaleZ);
      dummy.updateMatrix();
      trunkMesh.setMatrixAt(n, dummy.matrix);

      // A short exposed trunk forks into a central leader and three tapered
      // limbs. Everything stays in the same metric/exaggerated frame as the DSM.
      for (let b = 0; b < 4; b++) {
        const angle = rot + b * 2.399963;
        const reachM = b === 0 ? 0 : crownR * 0.47;
        const baseY = worldY(t.ground + t.agl * (b === 0 ? 0.3 : 0.25));
        const tipY = worldY(t.ground + t.agl * (b === 0 ? 0.72 : 0.61 + hash01(t.seed + b) * 0.07));
        branchDirection.set(Math.cos(angle) * reachM * scaleX, tipY - baseY,
          Math.sin(angle) * reachM * scaleZ);
        const length = branchDirection.length();
        dummy.position.set(t.x, baseY, t.z);
        dummy.quaternion.setFromUnitVectors(up, branchDirection.normalize());
        dummy.scale.set(trunkR * 0.65 * scaleX, length, trunkR * 0.65 * scaleZ);
        dummy.updateMatrix();
        branchMesh.setMatrixAt(n * 4 + b, dummy.matrix);
      }

      for (let b = 0; b < t.blobs; b++) {
        const seed = t.seed * 37 + b * 101;
        const main = b === 0;
        const lower = b > 0 && b <= 3;
        const angle = rot + b * 2.399963;
        const spread = t.form === 0 ? 1.12 : t.form === 1 ? .68 : 1;
        const offsetM = main ? 0 : crownR * spread * (lower ? .40 + hash01(seed * 3) * .16 : .30);
        const radiusM = crownR * spread * (main ? .68 : lower ? .38 + hash01(seed * 5) * .10 : .38);
        const bottomY = worldY(t.ground + t.agl * (main ? (t.form === 1 ? .40 : .55) : lower ? .35 + hash01(seed) * .10 : .54));
        const topY = worldY(t.ground + t.agl * (main ? 1 : lower ? 0.74 + hash01(seed * 7) * 0.10 : 0.88 + hash01(seed * 7) * 0.08));
        const radiusY = (topY - bottomY) / (crownMaxY - crownMinY);
        dummy.position.set(t.x + Math.cos(angle) * offsetM * scaleX,
          topY - crownMaxY * radiusY, t.z + Math.sin(angle) * offsetM * scaleZ);
        dummy.rotation.set(0, rot + b, 0);
        const width = .78 + hash01(t.seed * 41) * .38;
        dummy.scale.set(radiusM * width * scaleX, radiusY, radiusM * (0.85 + hash01(seed * 11) * 0.20) * scaleZ);
        dummy.updateMatrix();
        crownMesh.setMatrixAt(crownIndex, dummy.matrix);
        // Keep each tree's palette coherent; nearby trees still vary naturally.
        const color = crownColor(t.seed);
        color.offsetHSL((hash01(seed * 19) - .5) * .015, 0, (hash01(seed * 23) - .5) * .025);
        crownMesh.setColorAt(crownIndex++, color);
      }

      // A coarse crown uses the existing metric canopy radius and touches the
      // identical measured top elevation. A slightly longer trunk replaces the
      // hidden branch network without introducing a gap beneath the foliage.
      dummy.position.set(t.x, groundY, t.z);
      dummy.rotation.set(0, rot, 0);
      dummy.scale.set(trunkR * scaleX, worldY(t.ground + t.agl * .42) - groundY, trunkR * scaleZ);
      dummy.updateMatrix();
      distantTrunkMesh.setMatrixAt(n, dummy.matrix);
      const spread = t.form === 0 ? 1.12 : t.form === 1 ? .68 : 1;
      const width = .78 + hash01(t.seed * 41) * .38;
      const topY = worldY(t.ground + t.agl);
      const bottomY = worldY(t.ground + t.agl * (t.form === 1 ? .40 : .35));
      const radiusY = (topY - bottomY) / (distantMaxY - distantMinY);
      const radiusX = crownR * spread * .94 * width * scaleX;
      const radiusZ = crownR * spread * .91 * scaleZ;
      dummy.position.set(t.x, topY - distantMaxY * radiusY, t.z);
      dummy.rotation.set(0, rot, 0);
      dummy.scale.set(radiusX, radiusY, radiusZ);
      dummy.updateMatrix();
      distantCrownMesh.setMatrixAt(n, dummy.matrix);
      distantCrownMesh.setColorAt(n, crownColor(t.seed));
      crownSizeSum += Math.max(topY - bottomY, radiusX * 2, radiusZ * 2);
    }

    const bounds = new THREE.Box3();
    for (const mesh of [trunkMesh, branchMesh, crownMesh, distantTrunkMesh, distantCrownMesh]) {
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.computeBoundingBox();
      mesh.computeBoundingSphere();
      bounds.union(mesh.boundingBox);
    }
    const detailed = new THREE.Group(), distant = new THREE.Group();
    detailed.name = `treeDetailChunk${key}`; distant.name = `treeDistantChunk${key}`;
    detailed.add(trunkMesh, branchMesh, crownMesh);
    distant.add(distantTrunkMesh, distantCrownMesh);
    // Independent chunk uniforms allow opaque alpha-hashed crossfades without
    // changing measured tops or sorting overlapping transparent crowns.
    for (const mesh of [...detailed.children, ...distant.children]) {
      mesh.material = mesh.material.clone();
      mesh.material.alphaHash = true;
      mesh.material.opacity = detailed.children.includes(mesh) ? 0 : 1;
    }
    detailed.visible = false;
    group.add(detailed, distant);
    const nearTriangles = chunkTrees.length * (triangleCount(trunkGeo) + 4 * triangleCount(branchGeo))
      + chunkCrowns * triangleCount(crownGeo);
    state.detailedTriangles += nearTriangles;
    state.chunks.push({ count: chunkTrees.length, crowns: chunkCrowns,
      bounds, crownSize: crownSizeSum / chunkTrees.length, detailed, distant,
      near: false, shadowEnabled: true, nearTriangles,
      distantTriangles: chunkTrees.length * (triangleCount(distantTrunkGeo) + triangleCount(distantCrownGeo)),
    });
  }
  TREE_LOD_STATE.set(group, state);
  trunkMat.dispose(); crownMat.dispose();
  stats.lodChunkCount = state.chunks.length;
  stats.detailedTriangles = state.detailedTriangles;
  return { group, instanceCount: trees.length, stats };
}

export function logTreeStats(label, flattened, canopy, build) {
  const inst = build?.instanceCount ?? 0;
  const share = canopy?.greenShare != null ? `${(canopy.greenShare * 100).toFixed(1)}%` : '—';
  console.info(
    `[DepthWizard trees] ${label}: flattened ${flattened} mesh cells · ` +
    `${canopy?.greenKept ?? 0}/${canopy?.aglCells ?? 0} canopy (agl≥${CANOPY_MIN_AGL_M} m, green ${share}` +
    `${canopy?.skipGreen ? ', greyscale texture — green filter off' : ''}) · ${inst} instances`,
  );
}
