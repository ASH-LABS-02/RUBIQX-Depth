// Instanced tree visuals for roof-fit City view (display layer only; DSM grid unchanged).
import * as THREE from 'three';

/** Same default as depthwizard/buildings.py `min_height_m` (LoD1 footprint / structure minimum). */
export const CANOPY_MIN_AGL_M = 2.5;

const MAX_INSTANCES = 30000;
const TARGET_SPACING_M = 5;
const BASE_CROWN = new THREE.Color(0x3f6b35);
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
  const pixels = readTexturePixels(texImg);
  const skipGreen = textureLooksGreyscale(pixels);
  let aglCells = 0;
  let greenKept = 0;
  for (let i = 0; i < h.length; i++) {
    if (buildingMask?.[i]) continue;
    const agl = h[i] - dtm[i];
    if (!Number.isFinite(agl) || agl < CANOPY_MIN_AGL_M) continue;
    aglCells++;
    const r = (i / gw) | 0;
    const c = i - r * gw;
    if (!greennessAt(pixels, gw, gh, c, r, skipGreen)) continue;
    greenKept++;
    mask[i] = 1;
  }
  return {
    mask,
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
  if (!canopy?.mask) return 0;
  const { mask } = canopy;
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
  const t = 0.85 + hash01(seed * 19) * 0.3;
  return new THREE.Color(BASE_CROWN.r * t, BASE_CROWN.g * t, BASE_CROWN.b * t);
}

function trunkRadiusFor(aglM) {
  return Math.min(0.45, Math.max(0.12, aglM * 0.025));
}

/**
 * @returns {{ group: THREE.Group|null, instanceCount: number, stats: object }}
 */
export function buildTreeGroup({ h, dtm, buildingMask, gw, gh, W, H, worldY, exag, texImg, canopy: canopyIn }) {
  if (!dtm) return { group: null, instanceCount: 0, stats: {} };
  const canopy = canopyIn || analyzeCanopy({ h, dtm, buildingMask, texImg, gw, gh });
  canopy.dtm = dtm;

  const cell = Math.min(W / Math.max(1, gw - 1), H / Math.max(1, gh - 1));
  let step = Math.max(1, Math.round(TARGET_SPACING_M / Math.max(cell, 0.25)));
  const dummy = new THREE.Object3D();

  const collect = (stride) => {
    const out = [];
    for (let r = 0; r < gh; r += stride) {
      for (let c = 0; c < gw; c += stride) {
        const i = r * gw + c;
        if (!isCanopyCandidate(i, canopy)) continue;
        const jx = (hash01(i * 3 + 1) - 0.5) * stride * 0.85;
        const jz = (hash01(i * 7 + 2) - 0.5) * stride * 0.85;
        const cc = Math.min(gw - 1, Math.max(0, c + jx));
        const rr = Math.min(gh - 1, Math.max(0, r + jz));
        const ii = Math.round(rr) * gw + Math.round(cc);
        if (!isCanopyCandidate(ii, canopy)) continue;
        const agl = h[ii] - dtm[ii];
        const x = (cc / (gw - 1) - 0.5) * W;
        const z = (rr / (gh - 1) - 0.5) * H;
        out.push({ x, z, ground: dtm[ii], agl, seed: ii });
      }
    }
    return out;
  };

  let trees = collect(step);
  while (trees.length > MAX_INSTANCES && step < Math.max(gw, gh)) {
    step += 1;
    trees = collect(step);
  }
  if (trees.length > MAX_INSTANCES) {
    const ratio = MAX_INSTANCES / trees.length;
    trees = trees.filter((_, idx) => hash01(idx * 11 + step) <= ratio);
    if (trees.length > MAX_INSTANCES) trees.length = MAX_INSTANCES;
  }

  const stats = {
    aglCells: canopy.aglCells,
    canopyCells: canopy.greenKept,
    greenShare: canopy.greenShare,
    skipGreen: canopy.skipGreen,
    instanceCount: trees.length,
    gridStep: step,
  };

  if (!trees.length) return { group: null, instanceCount: 0, stats };

  const trunkGeo = new THREE.CylinderGeometry(0.2, 0.28, 1, 6);
  trunkGeo.translate(0, 0.5, 0);
  trunkGeo.computeVertexNormals();
  const crownGeo = new THREE.IcosahedronGeometry(1, 2);
  crownGeo.computeVertexNormals();

  const trunkMat = new THREE.MeshLambertMaterial({ color: TRUNK_BROWN });
  const crownMat = new THREE.MeshLambertMaterial({ color: 0xffffff, vertexColors: true });

  const trunkMesh = new THREE.InstancedMesh(trunkGeo, trunkMat, trees.length);
  const crownMesh = new THREE.InstancedMesh(crownGeo, crownMat, trees.length);
  trunkMesh.castShadow = crownMesh.castShadow = true;
  trunkMesh.receiveShadow = crownMesh.receiveShadow = true;

  for (let n = 0; n < trees.length; n++) {
    const t = trees[n];
    const rot = hash01(t.seed * 13) * Math.PI * 2;
    const scaleJ = 0.88 + hash01(t.seed * 17) * 0.24;
    const trunkH = t.agl * 0.3 * exag;
    const crownR = Math.min(8, Math.max(1.5, 0.35 * t.agl)) * exag * scaleJ;
    const trunkR = trunkRadiusFor(t.agl) * scaleJ;
    const groundY = worldY(t.ground);

    crownMesh.setColorAt(n, crownColor(t.seed));

    dummy.position.set(t.x, groundY + trunkH * 0.5, t.z);
    dummy.rotation.set(0, rot, 0);
    dummy.scale.set(trunkR, trunkH, trunkR);
    dummy.updateMatrix();
    trunkMesh.setMatrixAt(n, dummy.matrix);

    dummy.position.set(t.x, groundY + trunkH + crownR, t.z);
    dummy.rotation.set(0, rot, 0);
    dummy.scale.setScalar(crownR);
    dummy.updateMatrix();
    crownMesh.setMatrixAt(n, dummy.matrix);
  }

  trunkMesh.instanceMatrix.needsUpdate = true;
  crownMesh.instanceMatrix.needsUpdate = true;
  if (crownMesh.instanceColor) crownMesh.instanceColor.needsUpdate = true;

  const group = new THREE.Group();
  group.name = 'treeInstances';
  group.add(trunkMesh, crownMesh);
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
