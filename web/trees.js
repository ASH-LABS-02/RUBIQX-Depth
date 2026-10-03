// Instanced tree visuals for roof-fit City view (display layer only; DSM grid unchanged).
import * as THREE from 'three';

/** Same default as depthwizard/buildings.py `min_height_m` (LoD1 footprint / structure minimum). */
export const CANOPY_MIN_AGL_M = 2.5;

const MAX_INSTANCES = 30000;
const TARGET_SPACING_M = 6;
const TRUNK_BROWN = 0x5c4033;

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

export function analyzeCanopy({ h, dtm, buildingMask, texImg, gw, gh }) {
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
    elevated[i] = 1;
    aglCells++;
    const r = (i / gw) | 0;
    const c = i - r * gw;
    if (!greennessAt(pixels, gw, gh, c, r, skipGreen)) continue;
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
      if (!buildingMask?.[j] && Number.isFinite(dtm[j])) flattenMask[j] = 1;
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
  group.traverse((c) => {
    if (c.geometry) c.geometry.dispose();
    if (c.material) c.material.dispose();
  });
}

function crownColor(seed) {
  return new THREE.Color().setHSL(
    0.28 + hash01(seed * 19) * 0.06,
    0.32 + hash01(seed * 23) * 0.20,
    0.20 + hash01(seed * 29) * 0.10,
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
        out.push({ x, z, ground: dtm[ii], agl, seed: ii, blobs: hash01(ii * 31) < 0.65 ? 5 : 6 });
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

  const trunkMesh = new THREE.InstancedMesh(trunkGeo, trunkMat, trees.length);
  const branchMesh = new THREE.InstancedMesh(branchGeo, trunkMat.clone(), trees.length * 4);
  const crownMesh = new THREE.InstancedMesh(crownGeo, crownMat, trees.reduce((n, t) => n + t.blobs, 0));
  trunkMesh.name = 'treeTrunks'; branchMesh.name = 'treeBranches'; crownMesh.name = 'treeFoliage';
  trunkMesh.castShadow = branchMesh.castShadow = crownMesh.castShadow = true;
  trunkMesh.receiveShadow = branchMesh.receiveShadow = crownMesh.receiveShadow = true;
  const up = new THREE.Vector3(0, 1, 0), branchDirection = new THREE.Vector3();

  let crownIndex = 0;
  for (let n = 0; n < trees.length; n++) {
    const t = trees[n];
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
      const offsetM = main ? 0 : crownR * (lower ? 0.47 : 0.30);
      const radiusM = crownR * (main ? 0.58 : lower ? 0.46 : 0.42);
      const bottomY = worldY(t.ground + t.agl * (main ? 0.56 : lower ? 0.32 + hash01(seed) * 0.08 : 0.52));
      const topY = worldY(t.ground + t.agl * (main ? 1 : lower ? 0.74 + hash01(seed * 7) * 0.10 : 0.88 + hash01(seed * 7) * 0.08));
      const radiusY = (topY - bottomY) / (crownMaxY - crownMinY);
      dummy.position.set(t.x + Math.cos(angle) * offsetM * scaleX,
        topY - crownMaxY * radiusY, t.z + Math.sin(angle) * offsetM * scaleZ);
      dummy.rotation.set(0, rot + b, 0);
      const width = 0.85 + hash01(t.seed * 41) * 0.25;
      dummy.scale.set(radiusM * width * scaleX, radiusY, radiusM * (0.85 + hash01(seed * 11) * 0.20) * scaleZ);
      dummy.updateMatrix();
      crownMesh.setMatrixAt(crownIndex, dummy.matrix);
      crownMesh.setColorAt(crownIndex++, crownColor(seed));
    }
  }

  trunkMesh.instanceMatrix.needsUpdate = true;
  branchMesh.instanceMatrix.needsUpdate = true;
  crownMesh.instanceMatrix.needsUpdate = true;
  if (crownMesh.instanceColor) crownMesh.instanceColor.needsUpdate = true;

  const group = new THREE.Group();
  group.name = 'treeInstances';
  group.add(trunkMesh, branchMesh, crownMesh);
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
