// Instanced tree visuals for roof-fit City view (display layer only; DSM grid unchanged).
import * as THREE from 'three';

/** Same default as depthwizard/buildings.py `min_height_m` (LoD1 footprint / structure minimum). */
export const CANOPY_MIN_AGL_M = 2.5;

const MAX_INSTANCES = 30000;
const TARGET_SPACING_M = 5;
const GREENNESS_MIN = 0.06;

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

function greennessAt(pixels, gw, gh, c, r) {
  if (!pixels) return true;
  const { data, w, h } = pixels;
  const u = c / Math.max(1, gw - 1);
  const v = 1 - r / Math.max(1, gh - 1);
  const x = Math.min(w - 1, Math.max(0, Math.round(u * (w - 1))));
  const y = Math.min(h - 1, Math.max(0, Math.round(v * (h - 1))));
  const i = (y * w + x) * 4;
  const R = data[i];
  const G = data[i + 1];
  const B = data[i + 2];
  return (G - (R + B) * 0.5) / 255 >= GREENNESS_MIN;
}

export function isCanopyCandidate(i, { h, dtm, buildingMask, gw, gh, pixels }) {
  if (!dtm || buildingMask?.[i]) return false;
  const agl = h[i] - dtm[i];
  if (!Number.isFinite(agl) || agl < CANOPY_MIN_AGL_M) return false;
  const r = (i / gw) | 0;
  const c = i - r * gw;
  return greennessAt(pixels, gw, gh, c, r);
}

/** Display-only: drop canopy bumps to bare ground where instanced trees will stand. */
export function flattenCanopyHeights(src, h, dtm, buildingMask, texImg, gw, gh) {
  if (!dtm) return;
  const pixels = readTexturePixels(texImg);
  for (let i = 0; i < src.length; i++) {
    if (buildingMask?.[i]) continue;
    if (!isCanopyCandidate(i, { h, dtm, buildingMask, gw, gh, pixels })) continue;
    src[i] = dtm[i];
  }
}

export function disposeTreeGroup(group) {
  if (!group) return;
  group.traverse((c) => {
    if (c.geometry) c.geometry.dispose();
    if (c.material) c.material.dispose();
  });
}

/**
 * @returns {THREE.Group|null}
 */
export function buildTreeGroup({ h, dtm, buildingMask, gw, gh, W, H, worldY, exag, texImg }) {
  if (!dtm) return null;
  const cell = Math.min(W / Math.max(1, gw - 1), H / Math.max(1, gh - 1));
  let step = Math.max(1, Math.round(TARGET_SPACING_M / Math.max(cell, 0.25)));
  const pixels = readTexturePixels(texImg);
  const dummy = new THREE.Object3D();

  const collect = (stride) => {
    const out = [];
    for (let r = 0; r < gh; r += stride) {
      for (let c = 0; c < gw; c += stride) {
        const i = r * gw + c;
        if (!isCanopyCandidate(i, { h, dtm, buildingMask, gw, gh, pixels })) continue;
        const jx = (hash01(i * 3 + 1) - 0.5) * stride * 0.85;
        const jz = (hash01(i * 7 + 2) - 0.5) * stride * 0.85;
        const cc = Math.min(gw - 1, Math.max(0, c + jx));
        const rr = Math.min(gh - 1, Math.max(0, r + jz));
        const ii = Math.round(rr) * gw + Math.round(cc);
        if (!isCanopyCandidate(ii, { h, dtm, buildingMask, gw, gh, pixels })) continue;
        const agl = h[ii] - dtm[ii];
        const x = (cc / (gw - 1) - 0.5) * W;
        const z = (rr / (gh - 1) - 0.5) * H;
        const ground = dtm[ii];
        out.push({ x, z, ground, agl, seed: ii });
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
  if (!trees.length) return null;

  const trunkGeo = new THREE.CylinderGeometry(0.05, 0.08, 1, 5);
  trunkGeo.translate(0, 0.5, 0);
  const crownGeo = new THREE.ConeGeometry(1, 1, 6);
  crownGeo.translate(0, 0.5, 0);
  const crownTopGeo = new THREE.ConeGeometry(0.62, 1, 5);
  crownTopGeo.translate(0, 0.5, 0);

  const trunkMat = new THREE.MeshStandardMaterial({ color: 0x4a3728, roughness: 0.95, metalness: 0 });
  const crownMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.88, metalness: 0, vertexColors: true });
  const crownTopMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85, metalness: 0, vertexColors: true });

  const trunkMesh = new THREE.InstancedMesh(trunkGeo, trunkMat, trees.length);
  const crownMesh = new THREE.InstancedMesh(crownGeo, crownMat, trees.length);
  const crownTopMesh = new THREE.InstancedMesh(crownTopGeo, crownTopMat, trees.length);
  trunkMesh.castShadow = crownMesh.castShadow = crownTopMesh.castShadow = true;
  trunkMesh.receiveShadow = crownMesh.receiveShadow = crownTopMesh.receiveShadow = true;

  for (let n = 0; n < trees.length; n++) {
    const t = trees[n];
    const rot = hash01(t.seed * 13) * Math.PI * 2;
    const scaleJ = 0.88 + hash01(t.seed * 17) * 0.24;
    const aglW = t.agl * exag;
    const trunkH = aglW * 0.38;
    const crownR = Math.min(8, Math.max(1.5, 0.35 * t.agl)) * exag * scaleJ;
    const crownH1 = aglW * 0.36;
    const crownH2 = aglW * 0.26;
    const groundY = worldY(t.ground);
    const hue = 0.28 + (hash01(t.seed * 19) - 0.5) * 0.04;
    crownMesh.setColorAt(n, new THREE.Color().setHSL(hue, 0.45, 0.32));
    crownTopMesh.setColorAt(n, new THREE.Color().setHSL(hue + 0.02, 0.42, 0.4));

    dummy.position.set(t.x, groundY + trunkH * 0.5, t.z);
    dummy.rotation.set(0, rot, 0);
    dummy.scale.set(scaleJ, trunkH, scaleJ);
    dummy.updateMatrix();
    trunkMesh.setMatrixAt(n, dummy.matrix);

    dummy.position.set(t.x, groundY + trunkH + crownH1 * 0.5, t.z);
    dummy.rotation.set(0, rot, 0);
    dummy.scale.set(crownR, crownH1, crownR);
    dummy.updateMatrix();
    crownMesh.setMatrixAt(n, dummy.matrix);

    dummy.position.set(t.x, groundY + trunkH + crownH1 + crownH2 * 0.5, t.z);
    dummy.rotation.set(0, rot + 0.35, 0);
    dummy.scale.set(crownR * 0.58, crownH2, crownR * 0.58);
    dummy.updateMatrix();
    crownTopMesh.setMatrixAt(n, dummy.matrix);
  }
  trunkMesh.instanceMatrix.needsUpdate = true;
  crownMesh.instanceMatrix.needsUpdate = true;
  crownTopMesh.instanceMatrix.needsUpdate = true;
  if (crownMesh.instanceColor) crownMesh.instanceColor.needsUpdate = true;
  if (crownTopMesh.instanceColor) crownTopMesh.instanceColor.needsUpdate = true;

  const group = new THREE.Group();
  group.name = 'treeInstances';
  group.add(trunkMesh, crownMesh, crownTopMesh);
  return group;
}
