// DepthWizard viewer – terrain mesh from DSM + optical drape, orbit / fly / tour navigation,
// height probe, elevation profiles, slope & error shading, validation dashboard.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { PointerLockControls } from 'three/addons/PointerLockControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Sky } from 'three/addons/objects/Sky.js';
import { floodFill, boundarySeeds, waterMesh, waterUniforms, scatterSvg, histSvg, lonLatAt } from './city.js?v=20261004-roadmap';
import { createCoordinateProbe } from './coordinates.js?v=20261004-roadmap';
import { createFrameBudget } from './frame-budget.js?v=20261004-roadmap';
import { createTerrainStream } from './terrain-stream.js?v=20261004-roadmap';
import { createSavedViews } from './saved-views.js?v=20261004-roadmap';
import { createMissionUi } from './ui-v2.js?v=20261004-roadmap';
import { createRunoffOverlay } from './runoff-overlay.js?v=20261004-roadmap';
import { createDiorama } from './diorama.js?v=20261001-bold-r4';
import { createBoldUi } from './ui-v3.js?v=20261003-scene-polish';
import { analyzeCanopy, buildTreeGroup, disposeTreeGroup, flattenCanopyHeights, logTreeStats, updateTreeLod, getTreeLodDiagnostics } from './trees.js?v=20261004-roadmap';
import { createWalkController } from './walk.js?v=20261003-navigation-budget';
import { createRenderProfiler } from './render-profile.js?v=20261003-navigation-budget';
import { createDialogManager } from './dialogs.js?v=20261003-navigation-budget';
import { cinematicPath } from './cinematic.js?v=20261003-cinematic';
import { coordinateAt, coordinateFrame, coordinateGrid } from './coordinates.js?v=20261004-roadmap';
// Same occupancy proxy as the server's population_exposure (mission 'population').
const FLOOR_AREA_PER_PERSON_M2 = 30;

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let missionUi = null, boldUi = null, dialogManager = null, renderProfiler = null, sceneGeneration = 0, sceneAbort = null, loadingSceneId = null;
let terrainStream = null, terrainStreamScene = null;
let savedViews = null;

async function apiFetch(url, options = {}) {
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = '';
      try { const body = await response.clone().json(); detail = typeof body.detail === 'string' ? body.detail : ''; } catch {}
      const error = new Error(detail || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return response;
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    // A connection retry never repeats a mutation that may already have succeeded.
    const retry = () => apiFetch(options.method && options.method !== 'GET' ? 'api/scenes' : url,
      options.method && options.method !== 'GET' ? {silent:true} : {...options, silent:true}).then(() => {
      toast('Connection restored. Retry the action to update this view.');
    }).catch(() => {});
    if (!options.silent) toast(missionUi?.humanError(error.message) || error.message, 'error', 9000, retry);
    throw error;
  }
}

let _sceneListCache = null;
let _sceneListFetchTime = 0;
async function fetchSceneList(force = false) {
  if (!force && _sceneListCache && Date.now() - _sceneListFetchTime < 30000) return _sceneListCache;
  _sceneListCache = await (await apiFetch('api/scenes')).json();
  _sceneListFetchTime = Date.now();
  return _sceneListCache;
}

// ------------------------------------------------------------------ colour maps
const RAMPS = {
  height: [[0, [0.16, 0.20, 0.45]], [0.3, [0.10, 0.55, 0.55]], [0.6, [0.55, 0.78, 0.30]], [0.85, [0.96, 0.84, 0.35]], [1, [1, 0.97, 0.92]]],
  slope: [[0, [0.18, 0.55, 0.34]], [0.33, [0.95, 0.85, 0.35]], [0.66, [0.93, 0.45, 0.20]], [1, [0.62, 0.10, 0.18]]],
  curvature: [[0, [0.18, 0.35, 0.8]], [0.5, [0.92, 0.92, 0.88]], [1, [0.78, 0.18, 0.16]]],
  error: [[0, [0.13, 0.35, 0.75]], [0.5, [0.96, 0.96, 0.96]], [1, [0.80, 0.16, 0.16]]],
  confidence: [[0, [0.85, 0.20, 0.20]], [0.5, [0.95, 0.85, 0.25]], [1, [0.15, 0.78, 0.45]]],
  ndsm: [[0, [0.10, 0.14, 0.22]], [0.2, [0.20, 0.55, 0.65]], [0.5, [0.92, 0.82, 0.28]], [1, [0.98, 0.96, 0.92]]],
  landslide: [[0, [0.18, 0.55, 0.34]], [0.25, [0.62, 0.78, 0.32]], [0.5, [0.96, 0.78, 0.25]], [0.7, [0.93, 0.42, 0.18]], [1, [0.65, 0.08, 0.15]]],
  change: [[0, [0.70, 0.10, 0.14]], [0.5, [0.94, 0.94, 0.92]], [1, [0.12, 0.42, 0.80]]],
  viewshed: [[0, [0.25, 0.25, 0.3]], [1, [0.98, 0.86, 0.3]]],
  // hypsometric tint: valley green -> olive -> tan -> brown -> rock grey -> snow
  topo: [[0, [0.106, 0.220, 0.169]], [0.16, [0.235, 0.431, 0.259]], [0.33, [0.557, 0.627, 0.353]],
    [0.5, [0.847, 0.776, 0.537]], [0.67, [0.690, 0.537, 0.380]], [0.84, [0.600, 0.588, 0.576]], [1, [0.973, 0.976, 0.980]]],
};
const HAZARD = [{ max: 30, color: [0.133, 0.773, 0.369], label: 'Slope 0–30°' },
  { max: 45, color: [0.918, 0.702, 0.031], label: 'Slope 30–45°' },
  { max: 91, color: [0.937, 0.267, 0.267], label: 'Slope >45°' }];
const niceStep = (x) => { const p = 10 ** Math.floor(Math.log10(Math.max(x, 1e-9))), d = x / p; return (d < 1.5 ? 1 : d < 3.5 ? 2 : d < 7.5 ? 5 : 10) * p; };
function ramp(name, t) {
  const r = RAMPS[name] || RAMPS.height; t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < r.length; i++) {
    if (t <= r[i][0]) {
      const [t0, c0] = r[i - 1], [t1, c1] = r[i], f = (t - t0) / (t1 - t0 || 1);
      return [c0[0] + (c1[0] - c0[0]) * f, c0[1] + (c1[1] - c0[1]) * f, c0[2] + (c1[2] - c0[2]) * f];
    }
  }
  return r[r.length - 1][1];
}
const cssRamp = (name) => 'linear-gradient(90deg,' + (RAMPS[name] || RAMPS.height).map(([t, c]) =>
  `rgb(${c.map((v) => Math.round(v * 255)).join(',')}) ${t * 100}%`).join(',') + ')';
const pct = (arr, p) => { const a = Float32Array.from(arr).filter(Number.isFinite).sort(); return a[Math.floor((a.length - 1) * p)]; };
const fmt = (v, d = 2) => (v === undefined || v === null || Number.isNaN(v)) ? '–' : Number(v).toFixed(d);
const escapeHtml = (v) => String(v).replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);

function sceneDisplayName(id = S.id, meta = S.meta, name) {
  const libraryName = name ?? $(`#scene-list .item[data-id="${CSS.escape(id || '')}"] .n`)?.textContent;
  const raw = String(meta?.display_name || meta?.scene_name || libraryName || meta?.input || '')
    .replace(/Â·/g, '·').split(/[\\/]/).pop().replace(/\.(tiff?|png|jpe?g)$/i, '');
  if (raw && !/^(rgb|image|optical|input|texture|scene)([ _-]?\d+)?$/i.test(raw)) return raw;
  id = String(id || '');
  if (!id || /^[a-f\d-]{20,}$/i.test(id)) return id ? `Uploaded scene · ${id.slice(-8)}` : 'Current scene';
  return id.replace(/^demo[-_]/i, '').split(/[-_]+/).map((word) =>
    /^(dc|nyc|s2)$/i.test(word) ? word.toUpperCase() : word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

// ------------------------------------------------------------------ renderer / scene
const canvas = $('#gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.shadowMap.autoUpdate = false;
renderer.toneMapping = THREE.ACESFilmicToneMapping;       // no clipped roofs, richer shadows
renderer.toneMappingExposure = 1.08;
const SUN_I = 2.6, HEMI_I = 0.95;                          // balanced for ACES tone mapping
const scene = new THREE.Scene();
const SKY = new THREE.Color(0x243e46);
const skyCanvas = document.createElement('canvas'); skyCanvas.width = 2; skyCanvas.height = 256;
const skyContext = skyCanvas.getContext('2d'), skyGradient = skyContext.createLinearGradient(0, 0, 0, 256);
skyGradient.addColorStop(0, '#070b14'); skyGradient.addColorStop(.60, '#172c36'); skyGradient.addColorStop(1, '#243e46');
skyContext.fillStyle = skyGradient; skyContext.fillRect(0, 0, 2, 256);
const skyBackdrop = new THREE.CanvasTexture(skyCanvas); skyBackdrop.colorSpace = THREE.SRGBColorSpace;
scene.background = skyBackdrop;
scene.fog = new THREE.Fog(SKY.clone(), 1e6, 2e6);
const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 1e5);
const hemi = new THREE.HemisphereLight(0xdfe9ff, 0x3a3226, HEMI_I);
const sun = new THREE.DirectionalLight(0xfff6ea, SUN_I);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.02;
scene.add(hemi, sun, sun.target);
const diorama = createDiorama(scene);

// ---- render on demand: full frame rate while something moves, a trickle when idle
let lastActivity = performance.now();
function requestRender() { lastActivity = performance.now(); }
['pointerdown', 'pointermove', 'wheel', 'keydown', 'input', 'change', 'click'].forEach((ev) =>
  addEventListener(ev, requestRender, { passive: true, capture: true }));

// ---- Cinematic quality: ambient occlusion + SMAA through a post-processing chain,
// physical sky and a ground plane that fades into haze. Built lazily on first use.
const post = { composer: null, gtao: null, smaa: null, sky: null, ground: null };
function ensurePost() {
  if (post.composer) return;
  const w = canvas.clientWidth || 800, h = canvas.clientHeight || 600;
  post.composer = new EffectComposer(renderer);
  post.composer.addPass(new RenderPass(scene, camera));
  post.gtao = new GTAOPass(scene, camera, w, h);
  post.gtao.output = GTAOPass.OUTPUT.Default;
  post.composer.addPass(post.gtao);
  post.composer.addPass(new OutputPass());
  post.smaa = new SMAAPass(w * renderer.getPixelRatio(), h * renderer.getPixelRatio());
  post.composer.addPass(post.smaa);
  post.sky = new Sky(); post.sky.visible = false; scene.add(post.sky);
  post.ground = new THREE.Mesh(new THREE.CircleGeometry(1, 64), new THREE.MeshStandardMaterial({ color: 0x3b4a3f, roughness: 1 }));
  post.ground.rotation.x = -Math.PI / 2; post.ground.receiveShadow = true; post.ground.visible = false; scene.add(post.ground);
}
function updateCinematicScene() {
  const on = S.quality === 'cinematic' && !!S.mesh;
  if (S.quality !== 'performance' && S.mesh) ensurePost();
  if (!post.sky) return;
  post.smaa.enabled = on;
  post.gtao.updateGtaoMaterial({samples:on?16:8,radius:Math.max(1,S.extent*.008),thickness:Math.max(1,S.extent*.004)});
  post.sky.visible = false; post.ground.visible = false;
  if (on) {
    post.sky.scale.setScalar(S.extent * 20);
    const u = post.sky.material.uniforms;
    u.turbidity.value = 6; u.rayleigh.value = 1.6; u.mieCoefficient.value = 0.004; u.mieDirectionalG.value = 0.82;
    u.sunPosition.value.copy(sun.position).normalize();
    scene.fog.color.copy(SKY); scene.background = skyBackdrop;
    post.gtao.updateGtaoMaterial({ radius: Math.max(1, S.extent * 0.008), distanceExponent: 1.5, thickness: Math.max(1, S.extent * 0.004), scale: 1 });
    post.gtao.blendIntensity = 1.0;
  } else {
    scene.background = skyBackdrop; scene.fog.color.copy(SKY);
  }
  requestRender();
}

const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true; orbit.dampingFactor = 0.08;
orbit.zoomToCursor = true;                                  // zoom towards what you point at
orbit.addEventListener('change', requestRender);
orbit.maxPolarAngle = Math.PI * 0.495;
orbit.screenSpacePanning = false;
const fly = new PointerLockControls(camera, canvas);

// ------------------------------------------------------------------ state
const S = {
  id: null, meta: null, gw: 0, gh: 0, W: 1, H: 1, h: null, dtm: null, confidence: null,
  renderH: null, ref: null, base: 0,
  exag: 1, smoothingM: 0, mode: 'optical', tool: 'probe', nav: 'orbit',
  viewGeometry: 'surface', treesEnabled: true, treeGroup: null, canopyCache: null, buildingToolsAvailable: false,
  mesh: null, skirt: null, water: null, floodActive: false, tex: null, texImg: null,
  marker: null, profileLine: null, profilePts: [],
  distPts: [], distLine: null,
  buildings: null, buildingGroup: null, selectedMesh: null,
  swipeActive: false, swipeKind: 'dem', swipeX: 0.5, modelBaseline: null, floodAnimating: false,
  riseStart: 0, riseDuration: 1050, missionAction: null, missionOverlay: null,
  cameraFlight: null, mapTiles: new Map(), mapView: null,
  keys: {}, tourT: 0, cinematic: null, extent: 1,
};
const uniforms = {
  uContourOn: { value: 0 },
  uContourInt: { value: 5 },
  uExag: { value: 1 },
  uBase: { value: 0 },
  uSwipeOn: { value: 0 },
  uSwipeX: { value: 0.5 },
  uResolution: { value: new THREE.Vector2(1000, 800) },
};

// ------------------------------------------------------------------ terrain
function elev(r, c) { return S.h[r * S.gw + c]; }
function sampleGrid(arr, x, z) {           // bilinear, world x/z -> value
  const c = (x / S.W + 0.5) * (S.gw - 1), r = (z / S.H + 0.5) * (S.gh - 1);
  const c0 = Math.max(0, Math.min(S.gw - 2, Math.floor(c))), r0 = Math.max(0, Math.min(S.gh - 2, Math.floor(r)));
  const fc = Math.min(1, Math.max(0, c - c0)), fr = Math.min(1, Math.max(0, r - r0));
  const i = r0 * S.gw + c0;
  return (arr[i] * (1 - fc) + arr[i + 1] * fc) * (1 - fr) + (arr[i + S.gw] * (1 - fc) + arr[i + S.gw + 1] * fc) * fr;
}
const worldY = (hm) => (hm - S.base) * S.exag;
const terrainY = (x, z) => worldY(sampleGrid(S.renderH || S.h, x, z));
const verticalDisplayFactor = () => S.meta?.units === 'relative' ? Math.max(+S.meta.display_height_m || 1, 1e-9) : 1;
const reportedHeight = (viewHeight) => viewHeight / verticalDisplayFactor();

function upsampleGrid(a, w, h, nw, nh) {      // bilinear resample of a viewer layer
  const out = new Float32Array(nw * nh);
  for (let r = 0; r < nh; r++) {
    const y = r * (h - 1) / Math.max(nh - 1, 1), y0 = Math.min(h - 2, Math.floor(y)), fy = y - y0;
    for (let c = 0; c < nw; c++) {
      const x = c * (w - 1) / Math.max(nw - 1, 1), x0 = Math.min(w - 2, Math.floor(x)), fx = x - x0, i = y0 * w + x0;
      out[r * nw + c] = (a[i] * (1 - fx) + a[i + 1] * fx) * (1 - fy) + (a[i + w] * (1 - fx) + a[i + w + 1] * fx) * fy;
    }
  }
  return out;
}

function smoothGrid(source, sigmaPixels) {
  if (sigmaPixels < 0.5) return source;
  const radius = Math.ceil(sigmaPixels * 3), kernel = new Float32Array(radius * 2 + 1);
  let total = 0;
  for (let k = -radius; k <= radius; k++) { const v = Math.exp(-0.5 * (k / sigmaPixels) ** 2); kernel[k + radius] = v; total += v; }
  for (let k = 0; k < kernel.length; k++) kernel[k] /= total;
  const temp = new Float32Array(source.length), output = new Float32Array(source.length);
  for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) {
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += source[r * S.gw + Math.max(0, Math.min(S.gw - 1, c + k))] * kernel[k + radius];
    temp[r * S.gw + c] = sum;
  }
  for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) {
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += temp[Math.max(0, Math.min(S.gh - 1, r + k)) * S.gw + c] * kernel[k + radius];
    output[r * S.gw + c] = sum;
  }
  return output;
}

// Display-only removal of isolated needles (1-2 cell spikes and pits): a cell is replaced by
// its 3x3 median when it differs by more than 6 robust sigmas. Exports are never touched.
function despikeGrid(src) {
  const w = S.gw, h = S.gh, out = Float32Array.from(src), med = new Float32Array(src.length), win = new Float32Array(9);
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
    let k = 0;
    for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
      const rr = Math.min(h - 1, Math.max(0, r + dr)), cc = Math.min(w - 1, Math.max(0, c + dc));
      win[k++] = src[rr * w + cc];
    }
    win.sort(); med[r * w + c] = win[4];
  }
  const step = Math.max(1, Math.floor(src.length / 20000)), res = [];
  for (let i = 0; i < src.length; i += step) res.push(Math.abs(src[i] - med[i]));
  res.sort((a, b) => a - b);
  const mad = Math.max(res[Math.floor(res.length / 2)] * 1.4826, (S.hmax - S.hmin) * 0.002, 1e-6);
  let n = 0;
  for (let i = 0; i < src.length; i++) if (Math.abs(src[i] - med[i]) > 6 * mad) { out[i] = med[i]; n++; }
  S.despiked = n;
  return out;
}

function updateRenderHeight(rebuild = true) {
  let src = S.h;
  let flattened = 0;
  S.canopyCache = null;
  if (S.viewGeometry === 'city') {
    src = Float32Array.from(S.h);
    const m = buildingMaskGrid();
    if (m) {
      // ground under footprints: DTM when calibrated, otherwise the lowest nearby surface
      for (let i = 0; i < src.length; i++) if (m[i]) src[i] = S.dtm ? Math.min(S.h[i], S.dtm[i]) : S.h[i];
      if (!S.dtm) {
        const out = Float32Array.from(src), R = 6;
        for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) {
          const i = r * S.gw + c; if (!m[i]) continue;
          let lo = Infinity;
          for (let d = -R; d <= R; d++) {
            const cc = c + d, rr = r + d;
            if (cc >= 0 && cc < S.gw && !m[r * S.gw + cc]) lo = Math.min(lo, S.h[r * S.gw + cc]);
            if (rr >= 0 && rr < S.gh && !m[rr * S.gw + c]) lo = Math.min(lo, S.h[rr * S.gw + c]);
          }
          if (lo < Infinity) out[i] = lo;
        }
        src = out;
      }
    }
    if (treesActive()) {
      S.canopyCache = analyzeCanopy({ h: S.h, dtm: S.dtm, buildingMask: m, semanticLabels: S.semanticLabels, texImg: S.texImg, gw: S.gw, gh: S.gh });
      S.canopyCache.dtm = S.dtm;
      flattened = flattenCanopyHeights(src, S.canopyCache);
    }
  }
  if ($('#despike')?.checked !== false) src = despikeGrid(src);
  S.renderH = smoothGrid(src, S.smoothingM / Math.max(S.W / (S.gw - 1), 1e-6));
  // Restore the expanded canopy footprint to DTM after smoothing, then
  // upload those final heights so shaded canopy shoulders cannot reappear.
  if (treesActive() && S.canopyCache?.flattenMask) {
    flattened = flattenCanopyHeights(S.renderH, S.canopyCache);
  }
  if (rebuild && S.mesh) applyHeights();
  syncTreeLayer(flattened);
}

function treesActive() {
  return S.viewGeometry === 'city' && S.treesEnabled && S.meta?.units === 'metre' && S.dtm && S.h;
}

function syncTreeLayer(flattenedCells = 0) {
  if (S.treeGroup) {
    scene.remove(S.treeGroup);
    disposeTreeGroup(S.treeGroup);
    S.treeGroup = null;
  }
  if (!treesActive()) return;
  const mask = buildingMaskGrid();
  const canopy = S.canopyCache || analyzeCanopy({ h: S.h, dtm: S.dtm, buildingMask: mask, semanticLabels: S.semanticLabels, texImg: S.texImg, gw: S.gw, gh: S.gh });
  canopy.dtm = S.dtm;
  const built = buildTreeGroup({
    h: S.h, dtm: S.dtm, buildingMask: mask, gw: S.gw, gh: S.gh, W: S.W, H: S.H,
    groundWm: S.meta.ground_w_m, groundHm: S.meta.ground_h_m,
    worldY, units: S.meta.units, texImg: S.texImg, canopy,
  });
  S.treeGroup = built.group;
  if (S.treeGroup) {
    scene.add(S.treeGroup);
    S.treeGroup.visible = true;
    updateTreeLod(S.treeGroup, camera, { quality: S.quality, viewportHeight: canvas.clientHeight, force: true });
  }
  logTreeStats(S.id || 'scene', flattenedCells, canopy, built);
  requestRender();
}

function slopeAt(r, c) {                    // degrees, true metres (no exaggeration)
  const dx = S.W / (S.gw - 1), dz = S.H / (S.gh - 1);
  const cl = Math.max(0, c - 1), cr = Math.min(S.gw - 1, c + 1), ru = Math.max(0, r - 1), rd = Math.min(S.gh - 1, r + 1);
  const gx = (elev(r, cr) - elev(r, cl)) / ((cr - cl) * dx), gz = (elev(rd, c) - elev(ru, c)) / ((rd - ru) * dz);
  const gradient = Math.hypot(gx, gz) / verticalDisplayFactor();
  return { slope: S.meta?.units === 'metre' ? Math.atan(gradient) * 180 / Math.PI : gradient,
    aspect: (Math.atan2(gx, -gz) * 180 / Math.PI + 360) % 360 };
}

function curvatureAt(r, c) {                // Laplacian approximation, 1/m
  const dx = S.W / (S.gw - 1), dz = S.H / (S.gh - 1);
  const l = Math.max(0, c - 1), rr = Math.min(S.gw - 1, c + 1);
  const u = Math.max(0, r - 1), d = Math.min(S.gh - 1, r + 1);
  const dxx = (elev(r, rr) - 2 * elev(r, c) + elev(r, l)) / Math.max(dx * dx, 1e-9);
  const dzz = (elev(d, c) - 2 * elev(r, c) + elev(u, c)) / Math.max(dz * dz, 1e-9);
  return (dxx + dzz) / verticalDisplayFactor();
}

function buildTerrain() {
  if (S.mesh) {scene.remove(S.mesh);S.mesh.geometry.dispose();S.mesh.material.dispose();}
  const geo = new THREE.PlaneGeometry(S.W, S.H, S.gw - 1, S.gh - 1);
  geo.rotateX(-Math.PI / 2);
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(S.gw * S.gh * 3).fill(1), 3));
  const mat = new THREE.MeshStandardMaterial({ map: S.tex, vertexColors: true, roughness: 1, metalness: 0, side: THREE.DoubleSide });
  if (S.normalTex) { mat.normalMap = S.normalTex; const k = Math.min(4, S.exag); mat.normalScale.set(k, k); }
  mat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, uniforms);
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying float vElev;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvElev = position.y;');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>',
      '#include <common>\nvarying float vElev;\nuniform float uContourOn, uContourInt, uExag, uSwipeOn, uSwipeX;\nuniform vec2 uResolution;')
      .replace('#include <dithering_fragment>', `#include <dithering_fragment>
      if (uContourOn > 0.5) {
        float e = vElev / max(uExag, 1e-4) / uContourInt;
        float d = abs(fract(e + 0.5) - 0.5) / max(fwidth(e), 1e-5);
        float em = e / 5.0;
        float dm = abs(fract(em + 0.5) - 0.5) / max(fwidth(em), 1e-5);
        float line = max(1.0 - smoothstep(0.0, 1.0, d), 1.0 - smoothstep(0.0, 1.8, dm));
        gl_FragColor.rgb = mix(gl_FragColor.rgb, vec3(0.05, 0.05, 0.06), line * 0.75);
      }
      if (uSwipeOn > 0.5) {
        float screenX = gl_FragCoord.x / max(uResolution.x, 1.0);
        if (screenX < uSwipeX) {
          float gray = dot(gl_FragColor.rgb, vec3(0.299, 0.587, 0.114));
          gl_FragColor.rgb = mix(vec3(gray), gl_FragColor.rgb * vec3(0.75, 0.88, 1.0), 0.5);
        }
        if (abs(screenX - uSwipeX) < 0.0015) {
          gl_FragColor.rgb = vec3(0.28, 0.74, 0.91);
        }
      }`);
  };
  S.mesh = new THREE.Mesh(geo, mat);
  S.mesh.receiveShadow = true; S.mesh.castShadow = true;
  scene.add(S.mesh);
  S.skirt = diorama.walls;
  applyHeights();
  applyShading();
}

function applyHeights() {
  if(S.missionOverlay?.userData.runoff && S.missionOverlay.userData.exag!==S.exag) clearMissionOverlay();
  const pos = S.mesh.geometry.attributes.position;
  for (let i = 0; i < S.gw * S.gh; i++) pos.setY(i, worldY(S.renderH[i]));
  pos.needsUpdate = true;
  S.mesh.geometry.computeVertexNormals();
  S.mesh.geometry.computeBoundingSphere(); S.mesh.geometry.computeBoundingBox();
  uniforms.uExag.value = S.exag;
  // skirt: walls from each edge down to a floor below the lowest point
  const floor = -Math.max(S.extent * 0.06, (S.hmax - S.hmin) * S.exag * 0.18);
  diorama.update(S,pos,floor);
  renderer.shadowMap.needsUpdate=true;
  if (S.marker) placeMarker(S.marker.userData.x, S.marker.userData.z);
  if (S.profilePts.length === 2) drawProfileLine();
  if (S.water) S.water.position.y = worldY(+$('#flood-level').value);
  if (S.gcpPins?.length) drawGcpPins();
  rebuildCoordinateGrid();
}

function applyShading() {
  const col = S.mesh.geometry.attributes.color, n = S.gw * S.gh;
  const mat = S.mesh.material;
  let legend = null;
  const topo = S.mode === 'topo' || S.mode === 'topogray';
  // Topo turns on index contours at an automatic interval; other modes restore the user's choice.
  if (topo) {
    const relief = (pct(S.renderH || S.h, 0.98) - pct(S.renderH || S.h, 0.02)) / verticalDisplayFactor();
    const step = S.topoManualStep || niceStep(Math.max(relief, 1e-6) / 14);
    uniforms.uContourOn.value = 1; uniforms.uContourInt.value = step * verticalDisplayFactor();
    S.topoStep = step;
    if (!S.topoManualStep) $('#contour-int').value = String(step);
  } else {
    uniforms.uContourOn.value = $('#contours').checked ? 1 : 0;
    uniforms.uContourInt.value = Math.max(0.001, (+$('#contour-int').value || 0.1) * verticalDisplayFactor());
  }
  if (S.mode === 'optical') {
    col.array.fill(1); mat.map = S.tex;
  } else if (topo) {
    mat.map = null;
    const src = S.renderH || S.h, lo = pct(src, 0.02), hi = pct(src, 0.98);
    for (let i = 0; i < n; i++) {
      let c = ramp('topo', (src[i] - lo) / (hi - lo || 1));
      if (S.mode === 'topogray') { const g = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]; c = [g, g, g]; }
      col.array[i * 3] = c[0]; col.array[i * 3 + 1] = c[1]; col.array[i * 3 + 2] = c[2];
    }
    legend = { name: 'topo', lo: reportedHeight(lo), hi: reportedHeight(hi), unit: S.units };
  } else if (S.mode === 'hazard') {
    mat.map = null;
    const counts = [0, 0, 0];
    for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) {
      const i = r * S.gw + c, sl = slopeAt(r, c).slope;          // true slope: exaggeration not included
      const k = sl < 30 ? 0 : sl < 45 ? 1 : 2; counts[k]++;
      const col3 = HAZARD[k].color;
      col.array[i * 3] = col3[0]; col.array[i * 3 + 1] = col3[1]; col.array[i * 3 + 2] = col3[2];
    }
    S.hazardShare = counts.map((v) => v / n);
  } else {
    mat.map = null;
    let vals = new Float32Array(n), lo, hi, name = S.mode;
    if (S.mode === 'height') {
      vals = S.h; lo = pct(S.h, 0.01); hi = pct(S.h, 0.99);
      legend = { name, lo: reportedHeight(lo), hi: reportedHeight(hi), unit: S.units };
    } else if (S.mode === 'slope') {
      for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) vals[r * S.gw + c] = slopeAt(r, c).slope;
      lo = 0; hi = S.meta?.units === 'metre' ? 45 : Math.max(0.001, pct(vals, 0.98));
      legend = { name, lo, hi, unit: S.meta?.units === 'metre' ? '°' : 'relative / m' };
    } else if (S.mode === 'confidence') {
      vals = S.confidence;
      lo = 0; hi = 1.0;
      legend = { name: 'Reliability index', lo: 0, hi: 100, unit: '% ensemble agreement · not accuracy probability' };
    } else if (S.mode === 'ndsm') {
      const dtmBase = S.dtm || S.renderH;
      for (let i = 0; i < n; i++) vals[i] = Math.max(0, S.h[i] - dtmBase[i]);
      lo = 0; hi = Math.max(5, pct(vals, 0.98));
      legend = { name: 'ndsm', lo: 0, hi: reportedHeight(hi), unit: `${S.units} structure` };
    } else if (S.mode === 'curvature') {
      for (let r = 0; r < S.gh; r++) for (let c = 0; c < S.gw; c++) vals[r * S.gw + c] = curvatureAt(r, c);
      const e = Math.max(1e-5, pct(vals.map(Math.abs), 0.95));
      lo = -e; hi = e; legend = { name, lo, hi, unit: S.meta?.units === 'metre' ? '1/m (Laplacian)' : 'relative/m² (Laplacian)' };
    } else if (S.mode === 'landslide' && S.susc) {
      vals = S.susc; lo = 0; hi = 1;
      legend = { name: 'landslide', lo: 0, hi: 1, unit: 'susceptibility index (screening)' };
    } else if (S.mode === 'change' && S.change) {
      vals = S.change; const e = Math.max(1, pct(S.change.map(Math.abs), 0.98));
      lo = -e; hi = e; legend = { name: 'change', lo, hi, unit: 'm (after − before) · red = lowered' };
    } else if (S.mode === 'viewshed' && S.viewshed) {
      vals = S.viewshed; lo = 0; hi = 1; legend = { name: 'viewshed', lo: 0, hi: 1, unit: 'visible from observer (yellow)' };
    } else if (S.mode === 'demdiff' && S.demBase) {
      for (let i=0;i<n;i++)vals[i]=S.h[i]-S.demBase[i];
      name='error';lo=-20;hi=20;legend={name,lo,hi,unit:'m · DSM − input DEM · display grid'};
    } else if (S.mode === 'error' && S.ref) {
      for (let i = 0; i < n; i++) vals[i] = S.h[i] - S.ref[i];
      // A fixed, interpretable metric range keeps outlier pixels from washing
      // out the useful signed-error detail. Values beyond it saturate.
      const e = S.meta?.units === 'metre' ? 20 : Math.max(0.5, pct(vals.map(Math.abs), 0.95));
      lo = -e; hi = e; legend = { name, lo, hi, unit: S.meta?.units === 'metre'
        ? 'm (est − ref; colours saturate beyond ±20 m)'
        : 'relative units (est − ref)' };
    }
    for (let i = 0; i < n; i++) {
      const c = ramp(name, (vals[i] - lo) / (hi - lo || 1));
      col.array[i * 3] = c[0]; col.array[i * 3 + 1] = c[1]; col.array[i * 3 + 2] = c[2];
    }
  }
  col.needsUpdate = true; mat.needsUpdate = true;
  const L = $('#legend');
  if (S.mode === 'hazard' && S.hazardShare) {
    L.innerHTML = `<div class="hazard-legend">${HAZARD.map((h, k) => `<span><i style="background:rgb(${h.color.map((v) => Math.round(v * 255)).join(',')})"></i>${h.label}<b>${(S.hazardShare[k] * 100).toFixed(1)}%</b></span>`).join('')}</div>
      <p class="note">True surface slope (vertical exaggeration removed) · screening classes, not a landslide forecast.</p>`;
    requestRender();
    return;
  }
  const errorTicks = S.mode === 'error' && legend;
  L.innerHTML = legend ? `<div class="bar" style="background:${cssRamp(legend.name)}"></div>
    ${errorTicks ? `<div class="ticks error-ticks"><span>${fmt(legend.lo, 0)}${S.meta?.units === 'metre' ? ' m' : ''}</span><span>0</span><span>+${fmt(legend.hi, 0)}${S.meta?.units === 'metre' ? ' m' : ''}</span></div>
      <p class="legend-note">Estimated − reference · colours clip beyond ±${fmt(legend.hi, 0)} ${S.meta?.units === 'metre' ? 'm' : 'relative units'}</p>`
      : `<div class="ticks"><span>${fmt(legend.lo, 1)}</span><span>${legend.unit}</span><span>${fmt(legend.hi, 1)}</span></div>`}` : '';
}

function updateAnalysisTools() {
  const slider = $('#flood-level'), info = $('#flood-info'), levelOut = $('#flood-v');
  const metric = S.meta?.units === 'metre';
  slider.disabled = !metric || !S.h;
  if (!metric || !S.h) {
    if (S.water) S.water.visible = false;
    levelOut.textContent = '—';
    info.textContent = 'Flood scenarios require a metric DSM.';
  } else {
    const lo = S.hmin, hi = S.hmax;
    slider.min = lo; slider.max = hi; slider.step = Math.max((hi - lo) / 200, 0.1);
    if (+slider.value < lo || +slider.value > hi) slider.value = lo + (hi - lo) * 0.25;
    const level = +slider.value;
    levelOut.textContent = `${fmt(level, 1)} m`;
    if (!S.water) {
      const geo = new THREE.PlaneGeometry(S.W, S.H); geo.rotateX(-Math.PI / 2);
      S.water = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.42, depthWrite: false, side: THREE.DoubleSide }));
      S.water.renderOrder = 2; scene.add(S.water);
    }
    // Connected flood: water spreads from a source (lowest scene edge or a clicked
    // point) only through ground below the level, so ridges and embankments hold it back.
    S.water.visible = false;
    if (S.floodMesh) { scene.remove(S.floodMesh); S.floodMesh.geometry.dispose(); S.floodMesh.material.alphaMap?.dispose(); S.floodMesh.material.dispose(); S.floodMesh = null; }
    const g = S.dtm || S.h;
    let count = 0, depthSum = 0, maxDepth = 0, mask = null;
    if (S.floodActive && !(S.floodSource === 'point' && S.floodSeed == null)) {
      if (S.floodSource === 'plane') { mask = new Uint8Array(g.length); for (let i = 0; i < g.length; i++) mask[i] = g[i] <= level ? 1 : 0; }
      else mask = floodFill(g, S.gw, S.gh, level, S.floodSource === 'point' ? [S.floodSeed] : boundarySeeds(g, S.gw, S.gh));
      const depthArr = new Float32Array(mask.length); let dmax = 0.1;
      for (let i = 0; i < mask.length; i++) if (mask[i]) { depthArr[i] = level - g[i]; if (depthArr[i] > dmax) dmax = depthArr[i]; }
      S.floodMesh = waterMesh(mask, S.gw, S.gh, S.W, S.H, depthArr, dmax); S.floodMesh.position.y = worldY(level); scene.add(S.floodMesh);
      for (let i = 0; i < mask.length; i++) if (mask[i]) { count++; const d = level - g[i]; depthSum += d; if (d > maxDepth) maxDepth = d; }
    }
    S.floodMask = mask;
    const area = count * S.W * S.H / S.h.length;
    const subPct = (count / S.h.length) * 100;
    $('#flood-submerged-pct').textContent = `${subPct.toFixed(1)}%`;
    const hit = floodedBuildings(level);
    $('#flood-buildings-count').textContent = String(hit.length);
    const people = hit.reduce((total, x) => {
      const b = S.buildings?.buildings?.find((item) => item.id === x.id);
      return total + (b ? Math.max(0, b.area_m2 || 0) * Math.max(1, b.storeys || Math.round((b.height_m || 3) / 3)) / FLOOR_AREA_PER_PERSON_M2 : 0);
    }, 0);
    $('#flood-population').textContent = S.floodActive ? `≈${Math.round(people).toLocaleString()}` : '—';
    S.floodAreaHa = S.floodActive ? area / 1e4 : 0;
    S.floodPeople = S.floodActive ? Math.round(people) : 0;
    S.floodBuildings = S.floodActive ? hit.length : 0;
    updateFloodBuildingColors(new Set(hit.map((x) => x.id)));
    updateSceneSummary(hit.length);
    const srcTxt = S.floodSource === 'plane' ? 'every cell below the level (no connectivity)' : S.floodSource === 'point' ? 'spreading from the clicked source' : 'entering from the lowest scene edge';
    info.innerHTML = S.floodActive
      ? (S.floodSource === 'point' && S.floodSeed == null ? '<span class="note" style="grid-column:1/-1">Click the terrain to place the water source.</span>'
        : `<b>Flooded area</b><span>${fmt(area / 1e4, 2)} ha (${subPct.toFixed(1)}%)</span><b>Mean / max depth</b><span>${fmt(count ? depthSum / count : 0, 2)} / ${fmt(maxDepth, 2)} m</span><b>Water volume</b><span>${fmt(depthSum * S.W * S.H / S.h.length, 0)} m³</span><b>Buildings &gt;1 m deep</b><span>${hit.filter((x) => x.depth > 1).length}</span><span class="note" style="grid-column:1/-1">Water ${srcTxt}; bare-ground estimate, no rainfall or drainage modelling.</span>`)
      : 'Move the water elevation slider, or press Animate Rise.';
  }
  const volume = $('#volume-info');
  if (!S.ref || !metric) {
    volume.textContent = 'Load a metric scene with a reference DSM to compare cut and fill volumes.';
  } else {
    let cut = 0, fill = 0, n = 0;
    for (let i = 0; i < S.h.length; i++) {
      const d = S.h[i] - S.ref[i];
      if (!Number.isFinite(d)) continue;
      n++;
      if (d > 0) fill += d; else cut -= d;
    }
    const cellArea = S.W * S.H / S.h.length;
    volume.innerHTML = `<b>Fill</b> ${fmt(fill * cellArea, 1)} m³<br><b>Cut</b> ${fmt(cut * cellArea, 1)} m³<br><span class="note">Estimated from ${n.toLocaleString()} mesh cells against the reference DSM.</span>`;
  }
  updateMissionHud();
}

$('#flood-level').addEventListener('input', () => { S.floodActive = true; updateAnalysisTools(); });
function floodedBuildings(level) {
  const list = S.buildings?.buildings; if (!list || !S.floodMask) return [];
  const out = [];
  // Test the actual polygon against the displayed inundation grid. A square
  // around its centre over-counted buildings beside narrow flooded streets.
  for (const b of list) {
    const ring = b.polygon_uv;
    if (!ring?.length) continue;
    const x0 = Math.max(0, Math.floor(Math.min(...ring.map(([u]) => u)) * (S.gw - 1)));
    const x1 = Math.min(S.gw - 1, Math.ceil(Math.max(...ring.map(([u]) => u)) * (S.gw - 1)));
    const y0 = Math.max(0, Math.floor(Math.min(...ring.map(([,v]) => v)) * (S.gh - 1)));
    const y1 = Math.min(S.gh - 1, Math.ceil(Math.max(...ring.map(([,v]) => v)) * (S.gh - 1)));
    let footprint = 0, wet = 0;
    for (let r = y0; r <= y1; r++) for (let c = x0; c <= x1; c++) {
      const u = (c + .5) / S.gw, v = (r + .5) / S.gh;
      let inside = false;
      for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
        const [uj, vj] = ring[j], [uk, vk] = ring[k];
        if ((vj > v) !== (vk > v) && u < (uk - uj) * (v - vj) / (vk - vj) + uj) inside = !inside;
      }
      if (inside) { footprint++; if (S.floodMask[r * S.gw + c]) wet++; }
    }
    if (footprint && wet / footprint >= .05 && level > b.ground_elevation_m)
      out.push({ id: b.id, depth: level - b.ground_elevation_m });
  }
  return out;
}
function updateFloodBuildingColors(wetIds) {
  S.lastWetIds = wetIds;
  if (!S.buildingGroup) return;
  for (const mesh of S.buildingGroup.children) {
    const wet = wetIds.has(mesh.userData.building?.id);
    mesh.traverse((part) => {
      const mats = Array.isArray(part.material) ? part.material : [part.material];
      for (const mat of mats) {
        if (!mat?.emissive) continue;
        mat.emissive.setHex(wet ? 0x9b231e : mesh === S.selectedMesh ? 0x0284c7 : 0x000000);
        mat.emissiveIntensity = wet ? 0.75 : 1;
      }
    });
  }
  if (S.heightLimit) applyHeightLimit();
}
// ---- planning: buildings taller than a height limit (orange highlight)
function applyHeightLimit() {
  const info = $('#height-limit-info');
  const list = S.buildings?.buildings || [];
  if (S.meta?.units !== 'metre' || !list.length) {
    S.heightLimit = null;
    info.textContent = 'Needs a metric scene with LoD1 buildings.';
    return;
  }
  const limit = +$('#height-limit').value;
  if (!(limit > 0)) { info.textContent = 'Enter a limit in metres.'; return; }
  S.heightLimit = limit;
  const over = list.filter((b) => (b.height_m || 0) > limit).sort((a, b) => b.height_m - a.height_m);
  const ids = new Set(over.map((b) => b.id));
  S.buildingGroup?.children.forEach((mesh) => {
    if (!ids.has(mesh.userData.building?.id)) return;
    mesh.traverse((part) => {
      const mats = Array.isArray(part.material) ? part.material : [part.material];
      for (const mat of mats) if (mat?.emissive) { mat.emissive.setHex(0xd97706); mat.emissiveIntensity = 0.8; }
    });
  });
  const top = over.slice(0, 5).map((b) => `#${b.id} ${fmt(b.height_m, 1)} m`).join(', ');
  info.innerHTML = over.length
    ? `<b>${over.length}</b> of ${list.length} buildings exceed ${fmt(limit, 1)} m (highlighted orange).${top ? ' Tallest: ' + top + '.' : ''} <span class="note">Heights carry the scene's calibration uncertainty.</span>`
    : `No building exceeds ${fmt(limit, 1)} m (tallest ${fmt(Math.max(...list.map((b) => b.height_m || 0)), 1)} m).`;
}
function clearHeightLimit() {
  S.heightLimit = null;
  updateFloodBuildingColors(S.lastWetIds || new Set());
  $('#height-limit-info').textContent = 'Highlights LoD1 buildings taller than a planning limit (metric scenes).';
}
$('#height-limit-run').onclick = () => { updateFloodBuildingColors(S.lastWetIds || new Set()); applyHeightLimit(); };
$('#height-limit-clear').onclick = clearHeightLimit;
$('#height-limit').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#height-limit-run').click(); });

$$('#flood-source button').forEach((btn) => btn.onclick = () => {
  S.floodSource = btn.dataset.src; $$('#flood-source button').forEach((x) => x.classList.toggle('active', x === btn));
  if (S.floodSource === 'point') S.floodSeed = null;
  S.floodActive = true; updateAnalysisTools();
});

let floodAnimTimer = null;
$('#flood-play').onclick = () => {
  if (S.floodAnimating) {
    clearInterval(floodAnimTimer);
    S.floodAnimating = false;
    $('#flood-play').textContent = '▶ Animate Rise';
  } else {
    S.floodAnimating = true;
    S.floodActive = true;
    $('#flood-play').textContent = '⏸ Pause';
    const slider = $('#flood-level');
    const min = +slider.min, max = +slider.max;
    if (+slider.value >= max - 0.5) slider.value = min;
    const step = (max - min) / 100;
    floodAnimTimer = setInterval(() => {
      let v = +slider.value + step;
      if (v >= max) {
        v = max;
        clearInterval(floodAnimTimer);
        S.floodAnimating = false;
        $('#flood-play').textContent = '▶ Animate Rise';
      }
      slider.value = v;
      updateAnalysisTools();
    }, 50);
  }
};
$('#flood-reset').onclick = () => {
  if (S.floodAnimating) {
    clearInterval(floodAnimTimer);
    S.floodAnimating = false;
    $('#flood-play').textContent = '▶ Animate Rise';
  }
  const slider = $('#flood-level');
  slider.value = slider.min;
  S.floodActive = false;
  S.floodMask = null;
  updateAnalysisTools();
  updateFloodBuildingColors(new Set());
};

$('#rainfall-mm').oninput = (e) => { $('#rainfall-v').textContent = `${e.target.value} mm`; };
let rainfallFrame = 0;
$('#rainfall-play').onclick = () => {
  if (!S.h || S.meta?.units !== 'metre') return;
  cancelAnimationFrame(rainfallFrame);
  const g = S.dtm || S.h, slider = $('#flood-level');
  const runoff = +$('#rainfall-mm').value / 1000 * S.W * S.H * 0.6;
  const volumeAt = (stage) => {
    const mask = floodFill(g, S.gw, S.gh, stage, boundarySeeds(g, S.gw, S.gh));
    let volume = 0;
    const cellArea = S.W * S.H / g.length;
    for (let i = 0; i < g.length; i++) if (mask[i]) volume += Math.max(0, stage - g[i]) * cellArea;
    return volume;
  };
  let lo = +slider.min, hi = +slider.max;
  for (let i = 0; i < 13; i++) { const mid = (lo + hi) / 2; if (volumeAt(mid) < runoff) lo = mid; else hi = mid; }
  const start = +slider.min, end = (lo + hi) / 2, t0 = performance.now();
  S.floodActive = true; S.floodSource = 'edge';
  $$('#flood-source button').forEach((x) => x.classList.toggle('active', x.dataset.src === 'edge'));
  const animate = (now) => {
    const t = Math.min(1, (now - t0) / 6000), smooth = t * t * (3 - 2 * t);
    slider.value = String(start + (end - start) * smooth);
    updateAnalysisTools();
    if (t < 1) rainfallFrame = requestAnimationFrame(animate);
  };
  rainfallFrame = requestAnimationFrame(animate);
};

function setSun(deg, elevDeg) {
  // azimuth clockwise from north (scene north = -Z); elevation from image metadata when known
  const el = Math.max(5, elevDeg ?? S.sunEl ?? 35) * Math.PI / 180, a = deg * Math.PI / 180, R = S.extent * 1.5;
  const cy = worldY((S.hmin + S.hmax) / 2) || 0;
  sun.position.set(Math.sin(a) * Math.cos(el) * R, cy + Math.sin(el) * R, -Math.cos(a) * Math.cos(el) * R);
  sun.target.position.set(0, cy, 0);
  renderer.shadowMap.needsUpdate=true;
  const sc = sun.shadow.camera, half = Math.hypot(S.W, S.H) * 0.5 * 1.02;   // tight fit around the scene
  sc.left = -half; sc.right = half; sc.top = half; sc.bottom = -half; sc.near = R * 0.2; sc.far = R * 2.2;
  sc.updateProjectionMatrix();
  const texel = (2 * half) / sun.shadow.mapSize.x;
  sun.shadow.bias = -0.0002;
  sun.shadow.normalBias = texel * 1.5;                   // removes acne without detaching shadows
  if (post.sky?.visible) post.sky.material.uniforms.sunPosition.value.copy(sun.position).normalize();
  requestRender();
}

// ------------------------------------------------------------------ loading scenes
async function loadScene(id) {
  coordinateProbe?.reset();
  terrainStream?.dispose(); if (terrainStream) scene.remove(terrainStream.group);
  terrainStream = null; terrainStreamScene = null;
  const generation = ++sceneGeneration;
  sceneAbort?.abort(); sceneAbort = new AbortController();
  const signal = sceneAbort.signal; loadingSceneId = id;
  const current = () => generation === sceneGeneration;
  S.heightLimit = null; S.lastWetIds = null;
  const hlInfo = document.getElementById('height-limit-info');
  if (hlInfo) hlInfo.textContent = 'Highlights LoD1 buildings taller than a planning limit (metric scenes).';
  busy(true, 'Loading terrain & 3D buildings…');
  try {
    const base = `jobs/${id}/viewer/`;
    let meta = await (await apiFetch(base + 'meta.json?' + Date.now(), {signal})).json();
    const hBuf = await (await apiFetch(base + 'height.bin?' + Date.now(), {signal})).arrayBuffer();
    let ref = meta.has_reference && meta.units === 'metre'
      ? new Float32Array(await (await apiFetch(base + 'ref.bin?' + Date.now(), {signal})).arrayBuffer()) : null;

    const fetchLayer = async (name, type = 'bin') => {
      try {
        const res = await apiFetch(base + name, {signal, silent:true});
        if (!res.ok) return null;
        return type === 'json' ? await res.json() : type === 'labels' ? new Uint8Array(await res.arrayBuffer()) : new Float32Array(await res.arrayBuffer());
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        if(error.status!==404)toast(`Could not load ${name}: ${error.message}`,'error',9000,()=>loadScene(id));
        return null;
      }
    };

    let [dtm, confidence, buildings, susc, change, demBase, semanticLabels] = await Promise.all([
      meta.has_dtm !== false ? fetchLayer('dtm.bin') : Promise.resolve(null),
      meta.has_confidence !== false && meta.tta !== 1 && meta.backbone !== 'heuristic-fallback' ? fetchLayer('confidence.bin') : Promise.resolve(null),
      meta.buildings_count !== 0 ? fetchLayer('buildings.json', 'json') : Promise.resolve(null),
      meta.layers?.susc ? fetchLayer('susc.bin') : Promise.resolve(null),
      meta.layers?.change ? fetchLayer('change.bin') : Promise.resolve(null),
      meta.layers?.base ? fetchLayer('base.bin') : Promise.resolve(null),
      meta.layers?.semantic ? fetchLayer('semantic.bin', 'labels') : Promise.resolve(null)
    ]);
    if (semanticLabels && semanticLabels.length !== meta.grid_w * meta.grid_h) {
      semanticLabels = null;
      toast('Semantic mask has the wrong grid size; using existing tree placement.');
    }

    // High mesh detail: real 1024 heights from the full-resolution rasters; other layers upsampled
    let hArr = new Float32Array(hBuf);
    if ($('#mesh-detail').value === '1024' && Math.max(meta.src_w || 0, meta.src_h || 0) > meta.grid_w) {
      try {
        const r = await apiFetch(`api/scenes/${id}/grid/height.bin?size=1024`, {signal, silent:true});
        if (r.ok) {
          const nw = +r.headers.get('X-Grid-W'), nh = +r.headers.get('X-Grid-H');
          const up = (a) => a ? upsampleGrid(a, meta.grid_w, meta.grid_h, nw, nh) : a;
          let dtmHi = null;
          if (dtm) { const rd = await apiFetch(`api/scenes/${id}/grid/dtm.bin?size=1024`, {signal, silent:true}); dtmHi = new Float32Array(await rd.arrayBuffer()); }
          hArr = new Float32Array(await r.arrayBuffer());
          if (semanticLabels) {
            const oldW = meta.grid_w, oldH = meta.grid_h, oldLabels = semanticLabels;
            semanticLabels = new Uint8Array(nw * nh);
            for (let y = 0; y < nh; y++) for (let x = 0; x < nw; x++) {
              const sx = Math.round(x * (oldW - 1) / Math.max(1, nw - 1));
              const sy = Math.round(y * (oldH - 1) / Math.max(1, nh - 1));
              semanticLabels[y * nw + x] = oldLabels[sy * oldW + sx];
            }
          }
          ref = up(ref); confidence = up(confidence); susc = up(susc); change = up(change); demBase = up(demBase); dtm = dtmHi;
          meta = { ...meta, grid_w: nw, grid_h: nh };
        }
      } catch (err) { if (err.name === 'AbortError') throw err; toast('High mesh detail unavailable; using the standard grid.'); }
    }
    let normalTex = null;
    try {
      normalTex = await new THREE.TextureLoader().loadAsync(`api/scenes/${id}/normal.png?` + Date.now());
      normalTex.colorSpace = THREE.NoColorSpace; normalTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    } catch { normalTex = null; }
    const tex = await new THREE.TextureLoader().loadAsync(base + 'texture.jpg?' + Date.now());
    if (!current()) { normalTex?.dispose(); tex.dispose(); return; }
    if (S.normalTex) S.normalTex.dispose();
    S.normalTex = normalTex;
    tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    if (S.tex) S.tex.dispose();
    if (S.water) { scene.remove(S.water); S.water.geometry.dispose(); S.water.material.dispose(); S.water = null; }
    for (const k of ['floodMesh', 'baseMesh']) if (S[k]) { scene.remove(S[k]); S[k].geometry.dispose(); S[k].material.dispose(); S[k] = null; }
    if (S.swipeActive) setSwipe(false);
    clearMissionOverlay();
    S.cameraFlight = null;
    S.mapTiles.clear();
    S._bmask = null;
    S.canopyCache = null;
    if (S.treeGroup) { scene.remove(S.treeGroup); disposeTreeGroup(S.treeGroup); S.treeGroup = null; }
    Object.assign(S, { demBase, modelBaseline: null, susc, change, viewshed: null, floodMask: null,
      floodSeed: null, floodSource: S.floodSource || 'edge', missionAction: null });
    Object.assign(S, { id, meta, gw: meta.grid_w, gh: meta.grid_h, W: meta.ground_w_m, H: meta.ground_h_m,
      h: hArr, dtm, confidence, buildings, ref, tex, texImg: tex.image, semanticLabels, units: meta.units === 'metre' ? 'm' : 'relative units',
      viewGeometry: 'surface' });
    const terrainOverview = Number(meta.gsd_m) >= 2.5 || !(buildings?.count > 0);
    S.buildingToolsAvailable = !terrainOverview;
    S.hmin = Math.min(...[pct(S.h, 0), ref ? pct(ref, 0) : Infinity]);
    S.hmax = Math.max(pct(S.h, 1), ref ? pct(ref, 1) : -Infinity);
    S.base = S.hmin; S.extent = Math.max(S.W, S.H);
    S.smoothingM = meta.scene === 'forest' ? 2 : meta.scene === 'urban' ? 0.5 : 0.75;
    $('#smooth').value = S.smoothingM;
    $('#smooth-v').textContent = `${S.smoothingM.toFixed(2)} m`;
    updateRenderHeight(false);
    S.floodActive = false;
    $('#flood-level').min = String(S.hmin); $('#flood-level').max = String(S.hmax);
    $('#flood-level').value = String(S.hmin + (S.hmax - S.hmin) * 0.25);
    const relief = S.hmax - S.hmin;
    S.exag = meta.units === 'metre' ? 1 : Math.min(10, Math.max(1, +(0.06 * S.extent / Math.max(relief, 1e-3)).toFixed(1)));
    if (terrainOverview) S.exag = 3;
    $('#exag').value = S.exag; $('#exag-v').textContent = S.exag.toFixed(1) + '×';
    camera.near = S.extent / 5000; camera.far = S.extent * 30; camera.updateProjectionMatrix();
    scene.fog.near = S.extent * 1.5; scene.fog.far = S.extent * 6;
    clearTools();
    buildTerrain();
    createBuildingMeshes(S.buildings);
    const builtFraction = (buildings?.total_footprint_m2 || 0) / Math.max(1, S.W * S.H);
    S.treesEnabled = meta.units === 'metre';
    const treesEl = $('#trees');
    if (treesEl) {
      treesEl.checked = S.treesEnabled;
      treesEl.disabled = meta.units !== 'metre';
    }
    $('#trees-toggle-row')?.classList.toggle('hidden', meta.units !== 'metre');
    if (!terrainOverview && (meta.scene === 'urban' || ((buildings?.count || 0) >= 50 && builtFraction >= 0.025))) setViewGeometry('city');
    else $$('#view-mode button').forEach((b) => b.classList.toggle('active', b.dataset.view === 'surface'));
    updateAnalysisTools();
    const c0 = meta.calibration || {};
    $('#time-of-day').value = '12'; $('#time-v').textContent = '12:00';
    sun.intensity = SUN_I; hemi.intensity = HEMI_I; sun.color.setHex(0xfff6ea);
    if (S.quality !== 'cinematic') { scene.background = skyBackdrop; scene.fog.color.copy(SKY); }
    const sunInput = meta.sun_input || {};
    const imageElevation = Number.isFinite(sunInput.elevation_deg) ? sunInput.elevation_deg : c0.sun_elevation_deg;
    const imageAzimuth = Number.isFinite(sunInput.azimuth_deg) ? sunInput.azimuth_deg : c0.sun_azimuth_deg;
    S.sunEl = Number.isFinite(imageElevation) ? imageElevation : 35;
    if (Number.isFinite(imageAzimuth)) { $('#sun').value = Math.round(imageAzimuth); $('#sun-v').textContent = `${Math.round(imageAzimuth)}° (image)`; }
    setSun(+$('#sun').value);
    resetView(); savedViews?.refresh();
    $('#swipe-toggle').disabled = !demBase;
    $('#btn-landslide').disabled = !susc; $('#btn-change').disabled = !change;
    if ((S.mode === 'landslide' && !susc) || (S.mode === 'change' && !change) || S.mode === 'viewshed') setMode('optical');
    renderChangePanel();
    $('#swipe-toggle').title = demBase ? 'Swipe: actual input DEM (left) vs DepthWizard DSM (right) · S' : 'Swipe needs a georeferenced scene with an input DEM';
    $('#btn-error').disabled = !ref;
    $('#btn-hazard').disabled = meta.units !== 'metre';
    $('#btn-hazard').title = meta.units === 'metre' ? 'Slope screening: angle bands only, not a stability or safety assessment · 6' : 'Slope screening needs a metric scene (degrees are meaningless in relative units)';
    if (S.mode === 'hazard' && meta.units !== 'metre') setMode('optical');
    if (!ref && S.mode === 'error') setMode('optical');
    if (!demBase && S.mode === 'demdiff') setMode('optical');
    if (!confidence && S.mode === 'confidence') setMode('optical');
    $('#shade-mode button[data-mode="confidence"]').disabled=!confidence;
    $('#contour-unit').textContent = meta.units === 'metre' ? 'm' : 'rel';
    $('#contour-int').value = meta.units === 'metre' ? '5' : '0.1';
    S.topoManualStep = null;
    $('#contour-int').step = meta.units === 'metre' ? '0.5' : '0.01';
    uniforms.uContourInt.value = +$('#contour-int').value * verticalDisplayFactor();
    $('#hud-scene').textContent = `${meta.input} · ${meta.units === 'metre' ? 'absolute DSM' : 'relative rDSM'} · ${meta.calibration?.method ?? ''}`;
    $('#top-scene-name').textContent = meta.input || 'Current scene';
    const crs = meta.crs ? `CRS ${meta.crs}` : 'No spatial reference';
    const gsd = meta.gsd_m ? ` · GSD ${fmt(meta.gsd_m, 2)} m/px` : '';
    const datum = meta.vertical_datum ? ` · Datum ${meta.vertical_datum}` : '';
    const evidenceLevel = meta.calibration?.evidence_level || (meta.calibration?.method === 'dem+prior' ? 'approximate' : '');
    const provisional = evidenceLevel === 'approximate' || String(evidenceLevel).startsWith('provisional');
    const inputDem = meta.calibration?.method === 'input-dem';
    $('#top-scene-meta').textContent = `${inputDem ? 'Input DEM · visualised, not estimated' : meta.units === 'metre' ? (provisional ? 'Provisional metric DSM' : 'Metric DSM') : 'Relative rDSM'} · ${crs}${gsd}${datum}`;
    $('#dl-dsm-header').disabled = false;
    $('#export-menu-btn').disabled = false;
    const cal = meta.calibration || {};
    const evidence = meta.units === 'metre' ? `${evidenceLevel || 'unverified'} · ${cal.method || 'calibrated'}` : 'relative height';
    const badge = $('#scene-badge');
    const label = document.createElement('strong'); label.textContent = inputDem ? 'Input DEM (not estimated)' : meta.units === 'metre' ? (String(evidenceLevel).startsWith('provisional')?'Provisional evidence':provisional?'Approximate evidence':evidenceLevel?'Metric evidence':'Unverified evidence') : 'Relative scene';
    if (meta.units === 'metre' && cal.vertical_datum === 'same as input DEM' && !inputDem)
      label.textContent = `Input DEM datum · ${provisional ? 'approximate' : 'metric'}`;
    if (meta.backbone === 'heuristic-fallback') label.textContent = 'Prototype · heuristic output';
    badge.replaceChildren(label);
    badge.title = `${evidence} · vertical datum: ${cal.vertical_datum || 'unspecified'}`;
    if (meta.backbone === 'heuristic-fallback') badge.title = 'Heuristic prototype output; not representative of model accuracy';
    badge.dataset.evidence = evidenceLevel;
    badge.classList.remove('hidden');

    // Stepper updates
    $('#step-val')?.classList.toggle('done', Boolean(meta.has_reference));

    $$('#scene-list .item').forEach((el) => {
      const active = el.dataset.id === id;
      el.classList.toggle('active', active);
      el.querySelector('.scene-select')?.setAttribute('aria-pressed', String(active));
    });
    $('#model-swipe-toggle').disabled = !String(meta.backbone || '').toLowerCase().includes('gamus');
    $('#nav-mode button[data-nav="walk"]').disabled = meta.units !== 'metre';
    renderMetrics();
    renderBuildingList();
    drawMinimap();
    drawComparison();
    updateSceneSummary();
    renderLayerPreviews();
    setWorkspace('explore', false);
    updateMapAvailability();
    updateMissionAvailability();
    currentAnchors = (meta.height_anchor?.anchors || []).map((a) => {
      const building = buildings?.buildings?.find((b) => b.id === a.building_id);
      return { building_id: a.building_id, known: +a.height_m,
        est: +(building?.height_raw_m || building?.height_m || a.height_m) };
    });
    renderAnchorPanel();
    updateAutoAnchorPanel();
    for (const selector of ['#btn-city-view', '#mode-rail [data-workspace="buildings"]',
      '#building-inspector-group', '#height-limit-group', '#b-count-badge', '#shelter-tool']) {
      $(selector)?.classList.toggle('hidden', terrainOverview);
    }
    for (const selector of ['#flood-buildings-count', '#flood-population']) {
      $(selector)?.closest('.metric-box')?.classList.toggle('hidden', terrainOverview);
    }
    if (terrainOverview) setMode('topo');
    setCoordinateGrid(Boolean(meta.georeferenced));
    updateCoordinateReadout();
    updateCinematicScene();
    missionUi?.sceneLoaded();
    boldUi?.sceneLoaded();
    location.hash = id;
  } catch (e) {
    if (current() && e.name !== 'AbortError') toast('Could not load scene: ' + e.message, 'error', 8000, () => loadScene(id));
  } finally { if (current()) { loadingSceneId = null; busy(false); } }
}

function resetView() {
  S.cameraFlight = null;
  setNav('orbit');
  camera.up.set(0, 1, 0);
  const cy = worldY(pct(S.renderH || S.h, .5));
  orbit.target.set(0, cy, 0);
  // Fit the real scene bounds at a lower inspection angle, including relief.
  // Portrait screens need more distance than a widescreen city presentation.
  const direction = new THREE.Vector3(.42, .40, .81).normalize();
  const right = new THREE.Vector3().crossVectors(camera.up, direction).normalize();
  const up = new THREE.Vector3().crossVectors(direction, right).normalize();
  const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const tanH = tanV * camera.aspect;
  let distance = 0;
  for (const x of [-S.W / 2, S.W / 2]) for (const z of [-S.H / 2, S.H / 2])
    for (const y of [worldY(S.hmin), worldY(S.hmax)]) {
      const corner = new THREE.Vector3(x, y - cy, z);
      distance = Math.max(distance, corner.dot(direction) + Math.max(
        Math.abs(corner.dot(right)) / (tanH * .92), Math.abs(corner.dot(up)) / (tanV * .90)));
    }
  camera.position.copy(orbit.target).addScaledVector(direction, Math.max(distance, S.extent * .65));
  orbit.minDistance = S.extent * 0.01; orbit.maxDistance = S.extent * 4;
  orbit.update();
}

function topDownView() {
  if (!S.mesh) return;
  S.cameraFlight = null;
  setNav('orbit');
  const cy = worldY((S.hmin + S.hmax) / 2);
  orbit.target.set(0, cy, 0);
  camera.position.set(0, cy + S.extent * 1.5, S.extent * 0.0001);
  camera.up.copy(sceneNorth());
  camera.lookAt(orbit.target);
  orbit.update();
}

function sceneNorth() {
  // Geographic north in the affine image frame; relative scenes use image up.
  const t=S.meta?.transform,det=t?t[0]*t[4]-t[1]*t[3]:0;
  if(!det)return new THREE.Vector3(0,0,-1);
  return new THREE.Vector3(-t[1]/det*S.W/S.meta.src_w,0,t[0]/det*S.H/S.meta.src_h).normalize();
}

// ------------------------------------------------------------------ LoD1 3D city buildings
function convexFootprint(points) {
  let direction = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i], b = points[(i + 1) % points.length], c = points[(i + 2) % points.length];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (Math.abs(cross) < 1e-7) continue;
    if (direction && Math.sign(cross) !== direction) return false;
    direction = Math.sign(cross);
  }
  return direction !== 0;
}
// Walls with floor lines, window rows and darker bases (fake ambient occlusion).
// One floor = 3 m of true height, so the pattern follows the vertical exaggeration.
const wallUniforms = { uFloor: { value: 3 }, uWin: { value: 3.2 }, uWinOn: { value: 1 } };
function makeWallMaterial() {
  const m = new THREE.MeshStandardMaterial({ color: 0xdfe6ec, roughness: 0.72, metalness: 0.05, side: THREE.DoubleSide });
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, wallUniforms);
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying float vLy;\nvarying float vLu;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvLy = position.y;\nvLu = abs(normal.x) > abs(normal.z) ? position.z : position.x;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vLy;\nvarying float vLu;\nuniform float uFloor, uWin, uWinOn;')
      .replace('#include <color_fragment>', `#include <color_fragment>
        float fy = fract(vLy / uFloor);
        float slab = 1.0 - smoothstep(0.0, 0.07, fy) * (1.0 - smoothstep(0.93, 1.0, fy));
        float wx = fract(vLu / uWin);
        float win = uWinOn * step(0.3, fy) * step(fy, 0.78) * step(0.22, wx) * step(wx, 0.78) * step(uFloor * 0.6, vLy);
        diffuseColor.rgb *= 1.0 - 0.18 * slab;
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.20, 0.29, 0.37), 0.62 * win);
        diffuseColor.rgb *= 0.62 + 0.38 * smoothstep(0.0, uFloor * 1.3, vLy);`);
  };
  m.customProgramCacheKey = () => 'dw-wall-v4-axis-projection';
  return m;
}

function createBuildingMeshes(bData) {
  wallUniforms.uFloor.value = 3 * S.exag; wallUniforms.uWin.value = 3.2;
  if (S.buildingGroup) {
    scene.remove(S.buildingGroup);
    S.buildingGroup.traverse((c) => {
      if (c.geometry) c.geometry.dispose();
      if (c.material) {
        if (Array.isArray(c.material)) c.material.forEach((m) => m.dispose());
        else c.material.dispose();
      }
    });
    S.buildingGroup = null;
  }
  if (!bData || !bData.buildings || !bData.buildings.length) {
    $('#b-count-badge').textContent = '0 BUILDINGS';
    return;
  }
  $('#b-count-badge').textContent = `${bData.count} BUILDINGS`;
  const group = new THREE.Group();
  group.name = 'buildingsGroup';

  const wallMat = makeWallMaterial();

  const roofMat = new THREE.MeshStandardMaterial({
    map: S.tex,
    roughness: 0.85,
    metalness: 0.05,
    side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
  });

  for (const b of bData.buildings) {
    const pts = b.polygon_world;
    if (!pts || pts.length < 3) continue;
    const shape = new THREE.Shape();
    shape.moveTo(pts[0][0], -pts[0][1]);
    for (let i = 1; i < pts.length; i++) shape.lineTo(pts[i][0], -pts[i][1]);

    const hWorld = Math.max(1.5, b.height_m) * S.exag;
    const geo = new THREE.ExtrudeGeometry(shape, { depth: hWorld, bevelEnabled: false });
    // shape lies in XY with y = -z_world; rotating by -90° about X maps it onto the
    // ground plane (x, 0, z_world) and turns the extrusion depth into +Y (upwards)
    geo.rotateX(-Math.PI / 2);

    const roof = b.roof_fit?.type === 'gable' && !convexFootprint(pts)
      ? { type: 'flat' } : b.roof_fit;
    const topHeight = (x, z) => {
      if (!roof || roof.type === 'flat') return hWorld;
      if (roof.type === 'plane' && roof.center_world && roof.gradient_fraction_per_m) {
        const [cx, cz] = roof.center_world, [gx, gz] = roof.gradient_fraction_per_m;
        return Math.max(1, b.height_m * S.exag * (roof.center_fraction + gx * (x - cx) + gz * (z - cz)));
      }
      if (roof.type === 'gable' && roof.ridge_world && roof.pitch_fraction_per_m) {
        const [[ax, az], [bx, bz]] = roof.ridge_world;
        const dist = Math.abs((bx - ax) * (az - z) - (ax - x) * (bz - az)) / Math.max(1e-6, Math.hypot(bx - ax, bz - az));
        return Math.max(1, b.height_m * S.exag * (roof.ridge_fraction - roof.pitch_fraction_per_m * dist));
      }
      return hWorld;
    };
    const gableBase = roof?.type === 'gable' ? Math.min(...pts.map(([x, z]) => topHeight(x, z))) : hWorld;
    const pos = geo.attributes.position;
    const uvs = geo.attributes.uv;
    for (let i = 0; i < pos.count; i++) {
      uvs.setXY(i, pos.getX(i) / S.W + 0.5, 0.5 - pos.getZ(i) / S.H);
      if (pos.getY(i) > hWorld - 0.001) pos.setY(i,
        roof?.type === 'gable' ? gableBase : topHeight(pos.getX(i), pos.getZ(i)));
    }
    uvs.needsUpdate = true;
    pos.needsUpdate = true;
    geo.computeVertexNormals();

    // ExtrudeGeometry groups: 0 = caps (roof/floor) · 1 = side walls
    const mesh = new THREE.Mesh(geo, [roofMat.clone(), makeWallMaterial()]);
    const groundElev = b.ground_elevation_m !== undefined ? b.ground_elevation_m : S.base;
    mesh.position.y = worldY(groundElev);
    mesh.userData.building = b; mesh.castShadow = true; mesh.receiveShadow = true;
    if (roof?.type === 'gable' && roof.ridge_world) {
      const [[ax, az], [bx, bz]] = roof.ridge_world;
      const side = (p) => (bx - ax) * (p[1] - az) - (bz - az) * (p[0] - ax);
      // Complete the walls between a common eave base and the fitted roof.
      // Split an edge where it crosses the ridge so its top meets both roof
      // planes, including the triangular gable ends.
      const wallVertices = [];
      const addTriangle = (a, b, c) => wallVertices.push(...a, ...b, ...c);
      for (let k = 0; k < pts.length; k++) {
        const a = pts[k], b = pts[(k + 1) % pts.length];
        const da = side(a), db = side(b);
        const cut = da * db < -1e-9 ? [a[0] + (b[0] - a[0]) * da / (da - db),
          a[1] + (b[1] - a[1]) * da / (da - db)] : null;
        const pieces = cut ? [a, cut, b] : [a, b];
        for (let j = 0; j + 1 < pieces.length; j++) {
          const p = pieces[j], q = pieces[j + 1], py = topHeight(p[0], p[1]), qy = topHeight(q[0], q[1]);
          addTriangle([p[0], gableBase, p[1]], [q[0], gableBase, q[1]], [q[0], qy, q[1]]);
          addTriangle([p[0], gableBase, p[1]], [q[0], qy, q[1]], [p[0], py, p[1]]);
        }
      }
      const wallGeo = new THREE.BufferGeometry();
      wallGeo.setAttribute('position', new THREE.Float32BufferAttribute(wallVertices, 3));
      wallGeo.computeVertexNormals();
      const wallSections = new THREE.Mesh(wallGeo, makeWallMaterial());
      wallSections.userData.building = b; wallSections.castShadow = true; wallSections.receiveShadow = true;
      mesh.add(wallSections);
      const clip = (poly, keepPositive) => {
        const out = [];
        for (let k = 0; k < poly.length; k++) {
          const a = poly[k], q = poly[(k + 1) % poly.length], da = side(a), db = side(q);
          const inA = keepPositive ? da >= -1e-6 : da <= 1e-6;
          const inB = keepPositive ? db >= -1e-6 : db <= 1e-6;
          if (inA) out.push(a);
          if (inA !== inB && Math.abs(da - db) > 1e-9) {
            const t = da / (da - db); out.push([a[0] + (q[0] - a[0]) * t, a[1] + (q[1] - a[1]) * t]);
          }
        }
        return out;
      };
      for (const positive of [true, false]) {
        const part = clip(pts, positive);
        if (part.length < 3) continue;
        const panelShape = new THREE.Shape();
        panelShape.moveTo(part[0][0], -part[0][1]);
        for (let k = 1; k < part.length; k++) panelShape.lineTo(part[k][0], -part[k][1]);
        const panelGeo = new THREE.ShapeGeometry(panelShape);
        panelGeo.rotateX(-Math.PI / 2);
        const pp = panelGeo.attributes.position, puv = panelGeo.attributes.uv;
        for (let k = 0; k < pp.count; k++) {
          const x = pp.getX(k), z = pp.getZ(k);
          pp.setY(k, topHeight(x, z) + 0.03);
          puv.setXY(k, x / S.W + 0.5, 0.5 - z / S.H);
        }
        pp.needsUpdate = true; puv.needsUpdate = true; panelGeo.computeVertexNormals();
        const panel = new THREE.Mesh(panelGeo, roofMat.clone());
        panel.userData.building = b; panel.castShadow = true; panel.receiveShadow = true;
        mesh.add(panel);
      }
    }
    // Quiet crease lines clarify roof/wall boundaries without changing geometry.
    mesh.updateMatrixWorld(true);
    const edgeParts = [];
    mesh.traverse((part) => { if (part.isMesh) edgeParts.push(part); });
    for (const part of edgeParts) {
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(part.geometry, 35),
        new THREE.LineBasicMaterial({ color: 0x253039, transparent: true, opacity: .24, depthWrite: false }));
      edges.raycast = () => {}; edges.renderOrder = 1; part.add(edges);
    }
    mesh.scale.y = 0.001;
    group.add(mesh);
  }
  S.buildingGroup = group;
  wallMat.dispose(); roofMat.dispose();
  scene.add(group);
  group.visible = (S.viewGeometry === 'city');
  S.riseStart = performance.now();
}

function clearBuildingSelection() {
  if (S.selectedMesh) {
    if (Array.isArray(S.selectedMesh.material) && S.selectedMesh.material[1]?.emissive) {
      S.selectedMesh.material[1].emissive.setHex(0x000000);
    }
    S.selectedMesh = null;
  }
  $('#building-info').classList.add('muted');
  $('#building-info').textContent = 'Click any building in 3D to inspect storeys, height, footprint area and volume.';
  $('#building-anchor-controls').classList.add('hidden');
}

function selectBuilding(mesh) {
  setWorkspace('buildings');
  clearBuildingSelection();
  S.selectedMesh = mesh;
  if (Array.isArray(mesh.material) && mesh.material[1]?.emissive) {
    mesh.material[1].emissive.setHex(0x0284c7);
  }
  const b = mesh.userData.building;
  if (!b) return;
  const info = $('#building-info');
  info.classList.remove('muted');
  info.innerHTML = S.meta.units!=='metre' ? `
    <b>Building #${b.id}</b><span>${fmt(b.height_m,3)} relative units</span>
    <b>Roof elevation</b><span>${fmt(b.roof_elevation_m,3)} relative units</span>
    <b>Ground elevation</b><span>${fmt(b.ground_elevation_m,3)} relative units</span>
    <b>Reliability index</b><span>${Number.isFinite(b.confidence)?`${Math.round(b.confidence*100)}%`:'Unavailable'} · ${escapeHtml(b.confidence_basis || 'basis unavailable')} · not accuracy probability</span>
    <b>Storeys / volume</b><span>Require metric height calibration</span>
    <b>Footprint area</b><span>Requires a georeferenced image</span>
  ` : `
    <b>Building #${b.id}</b><span><strong style="color:var(--cyan);">${b.storeys} storeys</strong> (~${fmt(b.height_m, 1)} m)</span>
    <b>Roof elevation</b><span>${fmt(b.roof_elevation_m, 1)} m</span>
    <b>Ground elevation</b><span>${fmt(b.ground_elevation_m, 1)} m</span>
    <b>Footprint area</b><span>${fmt(b.area_m2, 1)} m²</span>
    <b>Structural volume</b><span>${fmt(b.volume_m3, 0)} m³</span>
    <b>Reliability index</b><span>${Number.isFinite(b.confidence)?`${Math.round(b.confidence*100)}%`:'Unavailable'} <small class="muted">${escapeHtml(b.confidence_basis || 'basis unavailable')} · not accuracy probability</small></span>
    ${b.roof_fit ? `<b>Roof hypothesis</b><span>${escapeHtml(b.roof_fit.type)}${b.roof_fit.pitch_deg ? ` · ${fmt(b.roof_fit.pitch_deg, 1)}° pitch` : ''} · model-inferred</span>` : ''}
    ${b.pv_kwh_yr !== undefined ? `<b>Rooftop solar</b><span>${fmt(b.sunlit_fraction * 100, 0)}% sunlit · ≈ ${Math.round(b.pv_kwh_yr).toLocaleString()} kWh/yr</span>` : ''}
    ${S.floodMask ? (() => { const f = floodedBuildings(+$('#flood-level').value).find((x) => x.id === b.id); return f ? `<b>Flood depth at base</b><span class="worse">${fmt(f.depth, 2)} m</span>` : ''; })() : ''}
  `;
  if (S.meta && S.meta.units === 'metre') {
    $('#building-anchor-controls').classList.remove('hidden');
    $('#anchor-known-height').value = (b.storeys * 3.0).toFixed(1);
    $('#anchor-storeys-btn').onclick = () => { $('#anchor-known-height').value = (b.storeys * 3.0).toFixed(1); };
    $('#anchor-add-btn').textContent='Apply height anchor';
    $('#anchor-add-btn').onclick = () => {
      const height=parseFloat($('#anchor-known-height').value);
      if(!Number.isFinite(height)||height<=0){toast('Enter a positive, independently known building height.','error');return;}
      addAnchor(b.id,b.height_m,height);setWorkspace('calibrate');$('#anchor-apply-btn').click();
    };
  }
}

// ------------------------------------------------------------------ cinematic flythrough recording
async function recordTour(seconds = 30) {
  if (S.recording) return;
  if (!S.mesh || !canvas.captureStream || typeof MediaRecorder === 'undefined') { toast('Video recording is not supported in this browser.', 'error'); return; }
  const btn = $('#record-tour');
  const type = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  const rec = new MediaRecorder(canvas.captureStream(30), type ? { mimeType: type, videoBitsPerSecond: 8e6 } : undefined);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.onstop = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
    a.download = `depthwizard_${S.id}_flythrough.webm`; a.click();
    btn.textContent = '● Record'; btn.classList.remove('active');
    S.recording = false;
  };
  setNav('tour'); rec.start(); S.recording = true;
  let left = seconds; btn.classList.add('active'); btn.textContent = `■ ${left}s`;
  const t = setInterval(() => { left--; btn.textContent = `■ ${left}s`; if (left <= 0) { clearInterval(t); rec.stop(); setNav('orbit'); } }, 1000);
}
$('#record-tour').onclick = () => recordTour(30);

// ------------------------------------------------------------------ viewshed (line of sight)
function computeViewshed(point, observerH = 10) {
  // Visibility from an observer `observerH` metres above the clicked ground point,
  // by marching 720 rays over the surface grid (the tallest horizon so far blocks).
  placeMarker(point.x, point.z);
  const { r: r0, c: c0 } = toGrid(point.x, point.z);
  const n = S.gw * S.gh, vis = new Float32Array(n), dx = S.W / (S.gw - 1);
  const f = verticalDisplayFactor(), h = S.h, eye = h[r0 * S.gw + c0] + observerH * f;
  vis[r0 * S.gw + c0] = 1;
  const rays = 720, maxR = Math.hypot(S.gw, S.gh);
  for (let k = 0; k < rays; k++) {
    const a = k / rays * Math.PI * 2, sx = Math.cos(a), sy = Math.sin(a);
    let maxSlope = -Infinity;
    for (let t = 1; t < maxR; t += 0.7) {
      const c = Math.round(c0 + sx * t), r = Math.round(r0 + sy * t);
      if (c < 0 || r < 0 || c >= S.gw || r >= S.gh) break;
      const i = r * S.gw + c, slope = (h[i] - eye) / (t * dx);
      if (slope >= maxSlope) { vis[i] = 1; maxSlope = slope; }
    }
  }
  S.viewshed = vis;
  let seen = 0; for (let i = 0; i < n; i++) seen += vis[i];
  $('#probe-info').classList.remove('muted');
  $('#probe-info').innerHTML = `<b>Observer</b><span>${observerH} m above ground</span><b>Visible area</b><span>${fmt(seen / n * 100, 1)}% of scene · ${fmt(seen * dx * dx / 1e4, 2)} ha</span><span class="note" style="grid-column:1/-1">Yellow = visible. Useful for siting watchtowers, relays and sirens.</span>`;
  setMode('viewshed');
}

// ------------------------------------------------------------------ change detection
async function renderChangePanel() {
  const el = $('#change-panel'); if (!el || !S.id) return;
  let list = [];
  try { list = await fetchSceneList(false); } catch {}
  const opts = list.filter((x) => x.id !== S.id).map((x) => `<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)}</option>`).join('');
  const st = S.meta?.change_stats;
  el.innerHTML = `<label>Before-event scene<select id="change-before">${opts}</select></label>
    <div class="btns"><button id="change-run" type="button" ${opts ? '' : 'disabled'}>Compare → this scene</button></div>
    <div id="change-info" class="kv ${st ? '' : 'muted'}">${st ? changeRows(st) : 'Pick the pre-event scene of the same footprint to map height loss (collapse, landslide scars) and gain (debris, new construction).'}</div>`;
  $('#change-run').onclick = async () => {
    const before = $('#change-before').value; if (!before) return;
    $('#change-info').textContent = 'Comparing…';
    const res = await apiFetch(`api/scenes/${encodeURIComponent(before)}/change/${encodeURIComponent(S.id)}`);
    const js = await res.json();
    if (!res.ok) { $('#change-info').textContent = js.detail || 'Comparison failed'; return; }
    await loadScene(S.id); setMode('change');
  };
}
function changeRows(st) {
  const rows = [['Volume lowered', `${fmt(st.volume_loss_m3, 0)} m³`], ['Volume raised', `${fmt(st.volume_gain_m3, 0)} m³`],
    ['Area lowered > 3 m', `${fmt(st.area_lowered_ha, 2)} ha`], ['Area raised > 3 m', `${fmt(st.area_raised_ha, 2)} ha`]];
  if (st.buildings_checked !== undefined) rows.push(['Buildings with roof loss', `<b class="worse">${st.buildings_height_loss}</b> of ${st.buildings_checked}`]);
  return rows.map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
}

// ------------------------------------------------------------------ 3D distance measurement
function clearDistanceTool() {
  S.distPts = [];
  if (S.distLine) { scene.remove(S.distLine); S.distLine.geometry.dispose(); S.distLine = null; }
}

function measureDistance(point) {
  if (S.distPts.length >= 2) clearDistanceTool();
  S.distPts.push(point);
  placeMarker(point.x, point.z);
  if (S.distPts.length === 1) {
    $('#probe-info').classList.remove('muted');
    $('#probe-info').innerHTML = '<span class="muted">Click second point for 3D distance…</span>';
    return;
  }
  const [p1, p2] = S.distPts;
  const gDist = Math.hypot(p2.x - p1.x, p2.z - p1.z);
  const y1 = terrainY(p1.x, p1.z), y2 = terrainY(p2.x, p2.z);
  const dz = (y2 - y1) / S.exag;
  const dist3d = Math.hypot(gDist, dz);
  const grade = (Math.abs(dz) / Math.max(gDist, 1e-3)) * 100;

  const pts = [new THREE.Vector3(p1.x, y1 + 0.5, p1.z), new THREE.Vector3(p2.x, y2 + 0.5, p2.z)];
  if (S.distLine) scene.remove(S.distLine);
  S.distLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x48bce7, linewidth: 2, depthTest: false }));
  S.distLine.renderOrder = 15;
  scene.add(S.distLine);

  $('#probe-info').classList.remove('muted');
  $('#probe-info').innerHTML = `
    <b>Ground distance</b><span>${fmt(gDist, 1)} m</span>
    <b>3D direct line</b><span>${fmt(dist3d, 1)} m</span>
    <b>Height diff (ΔZ)</b><span>${dz >= 0 ? '+' : ''}${fmt(dz, 1)} m</span>
    <b>Grade / slope</b><span>${fmt(grade, 1)}% (${fmt(Math.atan(grade / 100) * 180 / Math.PI, 1)}°)</span>
  `;
}

// ------------------------------------------------------------------ 3D swipe comparison
function updateBaseMesh() {
  const heights = S.swipeKind === 'model' ? S.modelBaseline : S.demBase;
  if (!heights) return;
  if (!S.baseMesh) {
    const geo = new THREE.PlaneGeometry(S.W, S.H, S.gw - 1, S.gh - 1); geo.rotateX(-Math.PI / 2);
    S.baseMesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: S.tex, roughness: 1, side: THREE.DoubleSide }));
    S.baseMesh.receiveShadow = true; S.baseMesh.visible = false; scene.add(S.baseMesh);
  }
  const pos = S.baseMesh.geometry.attributes.position;
  for (let i = 0; i < S.gw * S.gh; i++) pos.setY(i, worldY(heights[i]));
  pos.needsUpdate = true; S.baseMesh.geometry.computeVertexNormals();
}
function setSwipe(on, kind = 'dem') {
  S.swipeKind = kind;
  S.swipeActive = Boolean(on && (kind === 'model' ? S.modelBaseline : S.demBase));
  // geometric swipe: the left side really renders the input DEM surface
  uniforms.uSwipeOn.value = 0.0;
  if (S.swipeActive) updateBaseMesh();
  $('#model-swipe-toggle')?.setAttribute('aria-pressed', String(S.swipeActive && kind === 'model'));
  $('#swipe-toggle')?.setAttribute('aria-pressed', String(S.swipeActive && kind === 'dem'));
  $('#swipe-divider')?.classList.toggle('hidden', !S.swipeActive);
  $('#swipe-divider').dataset.compare = kind;
  $('#swipe-label-left')?.classList.toggle('hidden', !S.swipeActive);
  $('#swipe-label-right')?.classList.toggle('hidden', !S.swipeActive);
  $('#model-compare-note')?.classList.toggle('hidden', !(S.swipeActive && kind === 'model'));
  $('#swipe-label-left').textContent = kind === 'model' ? 'Pretrained Depth Anything V2' : 'Input coarse DEM (baseline)';
  $('#swipe-label-right').textContent = kind === 'model' ? 'GAMUS fine-tuned DepthWizard' : 'DepthWizard DSM';
  if (S.swipeActive) updateSwipePosition(0.5);
}
function updateSwipePosition(frac) {
  S.swipeX = Math.max(0.05, Math.min(0.95, frac));
  uniforms.uSwipeX.value = S.swipeX;
  const divider = $('#swipe-divider');
  if (divider) divider.style.left = `${S.swipeX * 100}%`;
}

function buildingMaskGrid() {
  // rasterise LoD1 footprints onto the terrain grid (1-cell dilation) so the
  // city model stands on bare ground instead of on the DSM's roof bumps
  if (S._bmask) return S._bmask;
  const list = S.buildings?.buildings; if (!list?.length) return null;
  const cv = document.createElement('canvas'); cv.width = S.gw; cv.height = S.gh;
  const ctx = cv.getContext('2d'); ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
  for (const b of list) {
    const ring = b.polygon_uv; if (!ring || ring.length < 3) continue;
    ctx.beginPath(); ring.forEach(([u, v], i) => { const x = u * (S.gw - 1), y = v * (S.gh - 1); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.closePath(); ctx.fill(); ctx.stroke();
  }
  const px = ctx.getImageData(0, 0, S.gw, S.gh).data, m = new Uint8Array(S.gw * S.gh);
  for (let i = 0; i < m.length; i++) m[i] = px[i * 4 + 3] > 0 ? 1 : 0;
  S._bmask = m; return m;
}
function setViewGeometry(mode) {
  if (!S.h || !S.mesh) return;
  if (mode === 'city' && !S.buildingToolsAvailable) mode = 'surface';
  const changed = S.viewGeometry !== mode;
  S.viewGeometry = mode;
  updateRenderHeight(true);
  $$('#view-mode button').forEach((b) => b.classList.toggle('active', b.dataset.view === mode));
  if (S.buildingGroup) {
    S.buildingGroup.visible = (mode === 'city');
    if (mode === 'city' && changed) {
      for (const b of S.buildingGroup.children) b.scale.y = 0.001;
      S.riseStart = performance.now();
    }
  }
}

// ------------------------------------------------------------------ navigation
let walkController = null, walkBuildings = null, walkScene = null;
function prepareWalk() {
  if (!walkController || walkBuildings !== S.buildings || walkScene !== S.id) {
    walkController = createWalkController({ W: S.W, H: S.H, groundWm: S.meta.ground_w_m, groundHm: S.meta.ground_h_m,
      groundAt: (x, z) => sampleGrid(S.renderH || S.h, x, z),
      polygons: (S.buildings?.buildings || []).map(b => b.polygon_world) });
    walkBuildings = S.buildings; walkScene = S.id;
  }
  return walkController;
}
function setNav(mode) {
  if (mode === 'walk' && (!S.mesh || S.meta?.units !== 'metre')) return;
  if (!S.mesh && mode === 'tour') return;
  if (mode === S.nav && mode !== 'tour') return;
  if (mode === 'walk') {
    const hit = raycastFrom(new THREE.Vector2(0, 0));
    const spawn = prepareWalk().spawn(hit?.point.x ?? camera.position.x, hit?.point.z ?? camera.position.z);
    if (!spawn) { toast('No clear ground with a suitable slope was found for Walk in this scene.', 'error'); return; }
    camera.position.set(spawn.x, worldY(spawn.ground + 1.7), spawn.z);
  }
  if (S.nav === 'fly' || S.nav === 'walk') fly.unlock();
  if (mode !== 'orbit') camera.up.set(0, 1, 0);
  S.nav = mode;
  S.cinematic = null;
  S.cameraFlight = null;
  S.keys = {};
  $('#cinematic-status').classList.toggle('hidden', mode !== 'tour' || S.presentation);
  missionUi?.navigationChanged(mode);
  orbit.enabled = mode === 'orbit';
  $('#fly-hint').classList.toggle('hidden', mode !== 'fly' && mode !== 'walk');
  updateNavigationHint();
  $$('#nav-mode button').forEach((b) => {
    b.classList.toggle('active', b.dataset.nav === mode);
    b.setAttribute('aria-pressed', String(b.dataset.nav === mode));
  });
  if (mode === 'walk') {
    const direction = camera.getWorldDirection(new THREE.Vector3());
    camera.rotation.set(-.08, Math.atan2(-direction.x, -direction.z), 0, 'YXZ');
    clampCamera();
  }
  if (mode === 'orbit') {
    // look at the terrain point in the centre of the view
    const hit = raycastFrom(new THREE.Vector2(0, 0));
    if (hit) orbit.target.copy(hit.point);
    orbit.update();
  }
  if (mode === 'tour') {
    S.cameraFlight = null;
    $('#hover-hud').classList.add('hidden');
    S.tourT = Math.atan2(camera.position.x, camera.position.z);
    S.cinematic = { elapsed: 0, duration: 30, from: camera.position.clone(),
      fromTarget: orbit.target.clone(), ...cinematicPath({ W: S.W, H: S.H, extent: S.extent,
        top: worldY(S.hmax), centreY: worldY((S.hmin + S.hmax) / 2), angle: S.tourT, terrainY }) };
    requestRender();
  }
}

const clock = new THREE.Clock();
function updateNavigationHint() {
  $('#fly-hint').textContent = `${fly.isLocked ? '' : 'Click to look around · '}${S.nav === 'walk'
    ? 'WASD walk · footprint barriers · slope limits' : 'WASD fly · Q/E down/up'} · Shift fast · Esc release`;
}
function updateFly(dt) {
  if (S.nav === 'walk') clampCamera();
  if (!fly.isLocked || dialogManager?.isOpen()) return;
  const walking = S.nav === 'walk';
  const diagonal = walking && (Boolean(S.keys.KeyW) !== Boolean(S.keys.KeyS))
    && (Boolean(S.keys.KeyA) !== Boolean(S.keys.KeyD));
  const speed = (walking ? 1.8 * S.W / (S.meta.ground_w_m || S.W) : S.extent / 12)
    * (S.keys.ShiftLeft || S.keys.ShiftRight ? (walking ? 2 : 4) : 1) * dt / (diagonal ? Math.SQRT2 : 1);
  const priorX = camera.position.x, priorZ = camera.position.z;
  if (S.keys.KeyW) fly.moveForward(speed);
  if (S.keys.KeyS) fly.moveForward(-speed);
  if (S.keys.KeyD) fly.moveRight(speed);
  if (S.keys.KeyA) fly.moveRight(-speed);
  if (!walking && (S.keys.KeyE || S.keys.Space)) camera.position.y += speed;
  if (!walking && S.keys.KeyQ) camera.position.y -= speed;
  if (walking) {
    const next = prepareWalk().move(priorX, priorZ, camera.position.x - priorX, camera.position.z - priorZ);
    camera.position.set(next.x, worldY(next.ground + 1.7), next.z);
  }
  clampCamera();
}
function clampCamera() {
  if (S.nav === 'walk') {
    const controller = prepareWalk();
    if (!controller.valid(camera.position.x, camera.position.z)) {
      const spawn = controller.spawn(camera.position.x, camera.position.z);
      if (!spawn) { setNav('orbit'); toast('Walk stopped: clear ground is unavailable.', 'error'); return; }
      camera.position.x = spawn.x; camera.position.z = spawn.z;
    }
    camera.position.y = worldY(controller.groundAt(camera.position.x, camera.position.z) + 1.7);
    return;
  }
  // Constrain first-person travel, not the orbit dolly or the diorama framing.
  const m = S.extent * (S.nav === 'fly' ? .6 : 5);
  camera.position.x = Math.max(-m, Math.min(m, camera.position.x));
  camera.position.z = Math.max(-m, Math.min(m, camera.position.z));
  const inside = Math.abs(camera.position.x) < S.W / 2 && Math.abs(camera.position.z) < S.H / 2;
  if (inside) {
    const g = terrainY(camera.position.x, camera.position.z) + Math.max(S.extent / 800, 1.5 * S.exag);
    if (camera.position.y < g) camera.position.y = g;
  }
  camera.position.y = Math.min(camera.position.y, S.extent * (S.nav === 'fly' ? 3 : 5));
}
function updateTour(dt) {
  S.tourT += dt * (S.presentation ? 0.025 : 0.08);
  if (S.presentation) {
    const cy = worldY((S.hmin + S.hmax) / 2), radius = S.extent * 0.85;
    camera.position.set(Math.sin(S.tourT) * radius, cy + S.extent * 0.65, Math.cos(S.tourT) * radius);
    camera.lookAt(0, cy, 0); return;
  }
  const tour = S.cinematic; if (!tour) return;
  tour.elapsed += dt;
  const t = Math.min(1, tour.elapsed / tour.duration), ease = t * t * (3 - 2 * t);
  const blend = Math.min(1, tour.elapsed / 2), entry = blend * blend * (3 - 2 * blend);
  camera.position.lerpVectors(tour.from, tour.position.getPoint(ease), entry);
  const inside = Math.abs(camera.position.x) <= S.W / 2 && Math.abs(camera.position.z) <= S.H / 2;
  if (inside) camera.position.y = Math.max(camera.position.y,
    terrainY(camera.position.x, camera.position.z) + S.extent * 0.04, worldY(S.hmax) + S.extent * 0.02);
  orbit.target.lerpVectors(tour.fromTarget, tour.target.getPoint(ease), entry);
  camera.lookAt(orbit.target);
  $('#cinematic-progress').textContent = `Cinematic · ${Math.ceil(tour.duration - tour.elapsed)}s`;
  if (t >= 1) setNav('orbit');
}

$('#cinematic-stop').onclick = () => setNav('orbit');

// ------------------------------------------------------------------ picking & tools
const raycaster = new THREE.Raycaster();
function raycastFrom(ndc) {
  if (!S.mesh) return null;
  raycaster.setFromCamera(ndc, camera);
  return raycaster.intersectObject(S.mesh, false)[0] || null;
}
function toGrid(x, z) { return { r: Math.round((z / S.H + 0.5) * (S.gh - 1)), c: Math.round((x / S.W + 0.5) * (S.gw - 1)) }; }
function mapCoords(x, z) {
  if (!S.meta?.georeferenced || !S.meta.transform) return null;
  return coordinateAt(S.meta, x / S.W + 0.5, z / S.H + 0.5);
}

let coordinateLines = null, coordinateLabels = [], gridVisible = false;
function setCoordinateGrid(on) {
  gridVisible = on;
  $('#coordinate-grid-toggle').setAttribute('aria-pressed', String(on));
  $('#coordinate-grid-toggle').classList.toggle('active', on);
  rebuildCoordinateGrid(); requestRender();
}
function rebuildCoordinateGrid() {
  if (coordinateLines) { scene.remove(coordinateLines); coordinateLines.geometry.dispose(); coordinateLines.material.dispose(); coordinateLines = null; }
  $('#coordinate-labels').replaceChildren(); coordinateLabels = [];
  if (!gridVisible || !S.mesh || !S.meta) return;
  const positions = [], offset = S.extent * 0.001;
  const pointAt = ([u, v]) => {
    const x = (u - 0.5) * S.W, z = (v - 0.5) * S.H;
    return new THREE.Vector3(x, terrainY(x, z) + offset, z);
  };
  for (const line of coordinateGrid(S.meta)) {
    const [p, q] = line.uv;
    let prior = pointAt(p);
    const steps = Math.min(512, Math.max(S.gw, S.gh));
    for (let i = 1; i <= steps; i++) {
      const t = i / steps, next = pointAt([p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]);
      positions.push(...prior.toArray(), ...next.toArray()); prior = next;
    }
    const label = document.createElement('span'); label.textContent = line.label;
    $('#coordinate-labels').append(label); coordinateLabels.push({ label, point: pointAt(q) });
  }
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  coordinateLines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0x7acbd5, transparent: true, opacity: 0.28, depthWrite: false }));
  scene.add(coordinateLines);
}
function updateCoordinateLabels() {
  const rect = canvas.getBoundingClientRect();
  const blockers = ['#toolbar', '#camera-modes', '#scene-hero', '#layer-dock', '#layer-legend', '#minimap-shell', '#coordinate-readout', '#inspector', '#app-header']
    .map(s => $(s)).filter(el => el && el.getClientRects().length).map(el => el.getBoundingClientRect());
  for (const { label, point } of coordinateLabels) {
    const p = point.clone().project(camera), x = (p.x + 1) * rect.width / 2, y = (1 - p.y) * rect.height / 2;
    label.style.left = `${x}px`; label.style.top = `${y}px`;
    const covered = blockers.some(b => x + rect.left > b.left - 60 && x + rect.left < b.right + 60 && y + rect.top > b.top - 10 && y + rect.top < b.bottom + 10);
    label.hidden = S.presentation || S.swipeActive || covered || p.z < -1 || p.z > 1 || x < 60 || x > rect.width - 60 || y < 70 || y > rect.height - 35;
  }
  if (coordinateLines) coordinateLines.visible = !S.swipeActive;
}
function updateCoordinateReadout(x, z) {
  if (!S.meta) return;
  const frame = coordinateFrame(S.meta);
  $('#coordinate-crs').textContent = frame.crs;
  $('#coordinate-readout').title = frame.georeferenced ? 'Native input CRS from source affine; longitude/latitude from an exact PROJ CRS transform.' : 'Local image pixel coordinates; no geographic position is available.';
  if (!Number.isFinite(x) || !Number.isFinite(z)) { $('#coordinate-value').textContent = 'Move over terrain to inspect'; $('#coordinate-lonlat').textContent = ''; return; }
  const p = coordinateAt(S.meta, x / S.W + 0.5, z / S.H + 0.5), d = frame.geographic ? 6 : 1;
  $('#coordinate-value').textContent = frame.geographic ? `Lon ${p.E.toFixed(d)}° · Lat ${p.N.toFixed(d)}°`
    : `${frame.georeferenced ? 'E' : 'Col'} ${p.E.toFixed(d)} · ${frame.georeferenced ? 'N' : 'Row'} ${p.N.toFixed(d)}${frame.georeferenced ? '' : ' px'}`;
  $('#coordinate-lonlat').textContent = frame.georeferenced ? 'Transforming geographic coordinates…' : '';
  if (frame.georeferenced) coordinateProbe.probe(x / S.W + .5, z / S.H + .5);
}
const coordinateProbe = createCoordinateProbe({ fetchApi: (...args) => apiFetch(...args),
  getScene: () => S.id, onResult: ll => {
    $('#coordinate-lonlat').textContent = `Lat ${ll[1].toFixed(6)}° · Lon ${ll[0].toFixed(6)}°`;
  } });
$('#coordinate-grid-toggle').onclick = () => setCoordinateGrid(!gridVisible);

function placeMarker(x, z) {
  if (!S.marker) {
    const g = new THREE.Group();
    const pin = new THREE.Mesh(new THREE.ConeGeometry(1, 3, 16), new THREE.MeshBasicMaterial({ color: 0xf59e0b }));
    pin.rotation.x = Math.PI; pin.position.y = 1.5;
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.2, 1.7, 32), new THREE.MeshBasicMaterial({ color: 0xf59e0b, side: THREE.DoubleSide, depthTest: false }));
    ring.rotation.x = -Math.PI / 2;
    g.add(pin, ring); scene.add(g); S.marker = g;
  }
  const s = S.extent / 250;
  S.marker.scale.setScalar(s);
  S.marker.position.set(x, terrainY(x, z) + s * 0.1, z);
  S.marker.userData = { x, z };
}

function probe(point) {
  const { x, z } = point;
  placeMarker(x, z);
  const { r, c } = toGrid(x, z);
  const h = sampleGrid(S.h, x, z), sa = slopeAt(r, c);
  const rows = [['Estimated', `${fmt(reportedHeight(h), S.meta.units === 'metre' ? 2 : 3)} ${S.units}`]];
  if (S.ref) { const rv = sampleGrid(S.ref, x, z); rows.push(['Reference', `${fmt(rv)} m`], ['Error', `<span class="${Math.abs(h - rv) < 2 ? 'better' : 'worse'}">${h - rv >= 0 ? '+' : ''}${fmt(h - rv)} m</span>`]); }
  rows.push([S.meta?.units === 'metre' ? 'Slope' : 'Relative gradient', S.meta?.units === 'metre' ? `${fmt(sa.slope, 1)}°` : `${fmt(sa.slope, 3)} / m`], ['Aspect', `${fmt(sa.aspect, 0)}°`]);
  const mc = mapCoords(x, z);
  if (mc) rows.push(['Easting', fmt(mc.E, 1)], ['Northing', fmt(mc.N, 1)]);
  else rows.push(['Local x, y', `${fmt(x + S.W / 2, 1)}, ${fmt(z + S.H / 2, 1)} m`]);
  $('#probe-info').classList.remove('muted');
  $('#probe-info').innerHTML = rows.map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
  const hud = $('#hover-hud');
  hud.innerHTML = rows.slice(0, 6).map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
  hud.classList.add('pinned'); hud.classList.remove('hidden');
  placeHoverHud(hud, (S.lastPointer?.x ?? 98) - 16, (S.lastPointer?.y ?? 90) - 16);
  S.hudPinned = true;
}

function placeHoverHud(hud, pointerX, pointerY) {
  const stage = $('#stage'), stageRect = stage.getBoundingClientRect();
  hud.classList.remove('hidden');
  const width = hud.offsetWidth || 250, height = hud.offsetHeight || 170, pad = 10;
  const clamp = (n, max) => Math.max(pad, Math.min(n, Math.max(pad, max - pad)));
  const obstacles = ['#app-header', '#camera-modes', '#coordinate-readout', '#mode-rail', '#scene-hero', '#layer-dock', '#toolbar', '#layer-legend',
    '#model-compare-note', '#swipe-label-left', '#swipe-label-right', '#inspector', '#welcome-card', '#minimap', '#scale-bar-container', '#exaggeration-note']
    .map((selector) => $(selector)?.getBoundingClientRect())
    .filter((r) => r && r.width && r.height)
    .map((r) => ({ left: r.left - stageRect.left - pad, top: r.top - stageRect.top - pad,
      right: r.right - stageRect.left + pad, bottom: r.bottom - stageRect.top + pad }));
  const candidates = [
    [pointerX + 16, pointerY + 16], [pointerX - width - 16, pointerY + 16],
    [pointerX + 16, pointerY - height - 16], [pointerX - width - 16, pointerY - height - 16],
    [stage.clientWidth - width - pad, 80], [pad, 80],
  ];
  const placed = candidates.map(([x, y]) => {
    x = clamp(x, stage.clientWidth - width); y = clamp(y, stage.clientHeight - height);
    const overlap = obstacles.reduce((sum, r) => sum + Math.max(0, Math.min(x + width, r.right) - Math.max(x, r.left))
      * Math.max(0, Math.min(y + height, r.bottom) - Math.max(y, r.top)), 0);
    return { x, y, score: overlap * 20 + Math.hypot(x - pointerX, y - pointerY) };
  }).sort((a, b) => a.score - b.score)[0];
  hud.style.transform = `translate(${placed.x}px, ${placed.y}px)`;
}

function drawProfileLine() {
  if (S.profileLine) { scene.remove(S.profileLine); S.profileLine.geometry.dispose(); }
  const [a, b] = S.profilePts, N = 300, pts = [];
  const lift = S.extent / 1500;
  for (let i = 0; i <= N; i++) {
    const x = a.x + (b.x - a.x) * i / N, z = a.z + (b.z - a.z) * i / N;
    pts.push(new THREE.Vector3(x, terrainY(x, z) + lift, z));
  }
  S.profileLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x38bdf8, depthTest: false }));
  S.profileLine.renderOrder = 10;
  scene.add(S.profileLine);
}

function profile(point) {
  if (S.profilePts.length >= 2) S.profilePts = [];
  S.profilePts.push({ x: point.x, z: point.z });
  placeMarker(point.x, point.z);
  if (S.profilePts.length < 2) { $('#probe-info').innerHTML = '<span class="muted">Click the end point…</span>'; return; }
  drawProfileLine();
  const [a, b] = S.profilePts, N = 300, dist = Math.hypot(b.x - a.x, b.z - a.z);
  const est = [], ref = [];
  for (let i = 0; i <= N; i++) {
    const x = a.x + (b.x - a.x) * i / N, z = a.z + (b.z - a.z) * i / N;
    est.push(reportedHeight(sampleGrid(S.h, x, z))); if (S.ref) ref.push(sampleGrid(S.ref, x, z));
  }
  const cv = $('#profile'); cv.classList.remove('hidden');
  const ctx = cv.getContext('2d'), Wd = cv.width, Hd = cv.height, pad = { l: 46, r: 10, t: 12, b: 26 };
  ctx.clearRect(0, 0, Wd, Hd);
  const all = est.concat(ref); let lo = Math.min(...all), hi = Math.max(...all);
  const span = (hi - lo) || 1; lo -= span * 0.08; hi += span * 0.08;
  const X = (i) => pad.l + (Wd - pad.l - pad.r) * i / N, Y = (v) => pad.t + (Hd - pad.t - pad.b) * (1 - (v - lo) / (hi - lo));
  ctx.strokeStyle = '#273241'; ctx.fillStyle = '#8b98a8'; ctx.font = '11px system-ui'; ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const v = lo + (hi - lo) * k / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(Wd - pad.r, y); ctx.stroke();
    ctx.fillText(v.toFixed(1), 4, y + 4);
  }
  ctx.fillText('0', pad.l, Hd - 8); ctx.fillText(`${dist.toFixed(0)} m`, Wd - pad.r - 40, Hd - 8);
  const line = (arr, color, w) => { ctx.strokeStyle = color; ctx.lineWidth = w; ctx.beginPath(); arr.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))); ctx.stroke(); };
  // filled estimate
  ctx.fillStyle = 'rgba(245,158,11,.15)'; ctx.beginPath(); ctx.moveTo(X(0), Y(lo));
  est.forEach((v, i) => ctx.lineTo(X(i), Y(v))); ctx.lineTo(X(N), Y(lo)); ctx.fill();
  if (ref.length) line(ref, '#38bdf8', 1.5);
  line(est, '#f59e0b', 2);
  ctx.fillStyle = '#f59e0b'; ctx.fillText('estimated', pad.l + 6, pad.t + 10);
  if (ref.length) { ctx.fillStyle = '#38bdf8'; ctx.fillText('reference', pad.l + 76, pad.t + 10); }
  const dh = est[N] - est[0];
  let rows = [['Length', `${fmt(dist, 1)} m`], ['Δ height', `${fmt(dh)} ${S.units}`], [S.meta?.units === 'metre' ? 'Mean grade' : 'Mean relative gradient', S.meta?.units === 'metre' ? `${fmt(Math.atan(Math.abs(dh) / (dist || 1)) * 180 / Math.PI, 1)}°` : `${fmt(Math.abs(dh) / (dist || 1), 3)} / m`],
    ['Max / min', `${fmt(Math.max(...est))} / ${fmt(Math.min(...est))}`]];
  if (ref.length) {
    const e = est.map((v, i) => v - ref[i]);
    rows.push(['Profile RMSE', `${fmt(Math.sqrt(e.reduce((s, v) => s + v * v, 0) / e.length))} m`]);
  }
  $('#probe-info').innerHTML = rows.map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
}

function clearTools() {
  S.hudPinned = false;
  $('#hover-hud').classList.remove('pinned');
  $('#hover-hud').classList.add('hidden');
  if (S.marker) { scene.remove(S.marker); S.marker = null; }
  if (S.profileLine) { scene.remove(S.profileLine); S.profileLine = null; }
  S.profilePts = [];
  clearDistanceTool();
  clearBuildingSelection();
  $('#profile').classList.add('hidden');
  $('#probe-info').classList.add('muted');
  $('#probe-info').textContent = S.tool === 'probe' ? 'Click the terrain to read height and slope.' : (S.tool === 'distance' ? 'Click first point for 3D distance…' : 'Click a start point, then an end point.');
}

let down = null;
canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', (e) => {
  if (!S.mesh || !down) return;
  const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]); down = null;
  if (moved <= 4 && e.button === 0) $('#app').classList.add('drawer-collapsed');
  if (S.nav === 'fly' || S.nav === 'walk') { if (!fly.isLocked) fly.lock(); return; }
  if (S.nav === 'tour') { setNav('orbit'); return; }
  if (moved > 4 || e.button !== 0) return;
  const stageRect = $('#stage').getBoundingClientRect();
  S.lastPointer = { x: e.clientX - stageRect.left + 16, y: e.clientY - stageRect.top + 16 };
  const rect = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);

  if (!S.missionAction && S.buildingGroup && S.buildingGroup.visible) {
    raycaster.setFromCamera(ndc, camera);
    const bHits = raycaster.intersectObjects(S.buildingGroup.children, true);
    if (bHits.length > 0 && bHits[0].object.userData?.building) {
      selectBuilding(bHits[0].object);
      showTab('analyse');
      return;
    }
  }

  const hit = raycastFrom(ndc);
  if (!hit) return;
  if (!S.missionAction && S.floodSource === 'point' && S.floodSeed == null && S.meta?.units === 'metre') {
    const { r, c } = toGrid(hit.point.x, hit.point.z);
    S.floodSeed = Math.max(0, Math.min(S.gh - 1, r)) * S.gw + Math.max(0, Math.min(S.gw - 1, c));
    const g = S.dtm || S.h, sl = $('#flood-level');
    if (+sl.value < g[S.floodSeed] + 0.5) sl.value = g[S.floodSeed] + 1.0;
    placeMarker(hit.point.x, hit.point.z); S.floodActive = true; updateAnalysisTools(); return;
  }
  if (S.gcpMode) { addGcpPin(hit.point); return; }
  if (S.missionAction) { runMission(S.missionAction, hit.point); return; }
  showTab('analyse');
  if (S.tool === 'viewshed') { computeViewshed(hit.point); return; }
  if (S.tool === 'probe') probe(hit.point);
  else if (S.tool === 'distance') measureDistance(hit.point);
  else profile(hit.point);
});

let hoverPending = null;
canvas.addEventListener('pointerleave', () => { if (!S.hudPinned) $('#hover-hud').classList.add('hidden'); updateCoordinateReadout(); });
canvas.addEventListener('pointermove', (e) => {
  const sr = $('#stage').getBoundingClientRect(); S.lastPointer = { x: e.clientX - sr.left + 16, y: e.clientY - sr.top + 16 };
  if (!S.mesh) return;
  if (hoverPending) { hoverPending = e; return; }       // one raycast per frame at most
  hoverPending = e;
  requestAnimationFrame(() => { const ev = hoverPending; hoverPending = null; hoverUpdate(ev); });
});
function hoverUpdate(e) {
  if (!S.mesh || !e) return;
  const rect = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  const hit = raycastFrom(ndc);
  if (hit) {
    const { x, z } = hit.point;
    const { r, c } = toGrid(x, z);
    const h = sampleGrid(S.h, x, z);
    const dtmVal = S.dtm ? sampleGrid(S.dtm, x, z) : null;
    const ndsmVal = (dtmVal !== null && h !== null) ? Math.max(0, h - dtmVal) : null;
    const sa = slopeAt(r, c);
    const confVal = S.confidence ? sampleGrid(S.confidence, x, z) : null;
    const mc = mapCoords(x, z);
    updateCoordinateReadout(x, z);
    if (S.hudPinned) return;
    
    const cEl = $('#hud-coords'); if (cEl) cEl.textContent = mc ? `${mc.E.toFixed(1)}, ${mc.N.toFixed(1)}` : `${x.toFixed(1)}, ${z.toFixed(1)}`;
    const px = Math.round(((x / S.W) + 0.5) * (S.meta?.src_w || S.gw));
    const py = Math.round(((z / S.H) + 0.5) * (S.meta?.src_h || S.gh));
    const pEl = $('#hud-pixel'); if (pEl) pEl.textContent = `${px}, ${py}`;
    const dEl = $('#hud-dsm'); if (dEl) dEl.textContent = `${fmt(reportedHeight(h), 1)} ${S.units}`;
    const tEl = $('#hud-dtm'); if (tEl) tEl.textContent = dtmVal !== null ? `${fmt(reportedHeight(dtmVal), 1)} ${S.units}` : '–';
    const nEl = $('#hud-ndsm'); if (nEl) nEl.textContent = ndsmVal !== null ? `${fmt(ndsmVal, 1)} ${S.units}` : '–';
    const sEl = $('#hud-slope'); if (sEl) sEl.textContent = `${fmt(sa.slope, 1)}°`;
    const cfEl = $('#hud-conf'); if (cfEl) cfEl.textContent = confVal !== null ? `${Math.round(confVal * 100)}% reliability` : '—';
    // floating HUD next to the cursor
    const hud = $('#hover-hud');
    if (S.hoverHud !== false && S.nav === 'orbit' && !S.presentation) {
      let bldg = null;
      if (S.buildingGroup?.visible) {
        raycaster.setFromCamera(ndc, camera);
        const bh = raycaster.intersectObjects(S.buildingGroup.children, true)[0];
        bldg = bh?.object?.userData?.building || null;
      }
      const ll = null; // Native E/N here; exact PROJ latitude/longitude is in the coordinate readout.
      const metric = S.meta.units === 'metre';
      const rows = [
        [ll ? 'Lat, lon' : (mc ? 'E, N' : 'x, y'), ll ? `${ll[1].toFixed(5)}, ${ll[0].toFixed(5)}` : mc ? `${mc.E.toFixed(1)}, ${mc.N.toFixed(1)}` : `${(x + S.W / 2).toFixed(1)}, ${(z + S.H / 2).toFixed(1)} m`],
        ['Surface', `${fmt(reportedHeight(h), metric ? 1 : 3)} ${S.units}`],
      ];
      if (dtmVal !== null) rows.push(['Ground', `${fmt(reportedHeight(dtmVal), 1)} ${S.units}`], ['Above ground', `${fmt(ndsmVal, 1)} ${S.units}`]);
      if (metric) rows.push(['Slope', `${fmt(sa.slope, 1)}°`]);
      if (confVal !== null) rows.push(['Reliability index', `${Math.round(confVal * 100)}%`]);
      if (bldg) rows.push(['Building', `#${bldg.id} · ${fmt(bldg.height_m, 1)} ${metric ? 'm' : ''}${bldg.storeys ? ` · ${bldg.storeys} fl` : ''}`]);
      hud.innerHTML = rows.map(([k, v]) => `<b>${k}</b><span>${v}</span>`).join('');
      const sr = $('#stage').getBoundingClientRect();
      placeHoverHud(hud, e.clientX - sr.left, e.clientY - sr.top);
    } else hud.classList.add('hidden');
  } else { if (!S.hudPinned) $('#hover-hud').classList.add('hidden'); updateCoordinateReadout(); }
}

// double-click: smooth fly-to the clicked point
canvas.addEventListener('dblclick', (e) => {
  if (!S.mesh || S.nav !== 'orbit') return;
  const rect = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const targets = [S.mesh].concat(S.buildingGroup?.visible ? S.buildingGroup.children : []);
  const hit = raycaster.intersectObjects(targets, true)[0];
  if (!hit) return;
  const toTarget = hit.point.clone();
  const offset = camera.position.clone().sub(orbit.target).multiplyScalar(0.45);
  const minDist = S.extent * 0.04;
  if (offset.length() < minDist) offset.setLength(minDist);
  S.cameraFlight = { started: performance.now(), fromCamera: camera.position.clone(), toCamera: toTarget.clone().add(offset),
    fromTarget: orbit.target.clone(), toTarget };
  requestRender();
});

// ------------------------------------------------------------------ minimap
const mm = $('#minimap'), mctx = mm.getContext('2d');
function drawMinimap() {
  if (!S.texImg) return;
  const w = mm.width, h = mm.height, pad=22,s = Math.min((w-2*pad) / S.W, (h-2*pad) / S.H);
  const dw = S.W * s, dh = S.H * s, ox = (w - dw) / 2, oy = (h - dh) / 2;
  mctx.fillStyle = '#101923'; mctx.fillRect(0, 0, w, h);
  mctx.drawImage(S.texImg, ox, oy, dw, dh);
  mctx.strokeStyle='rgba(183,201,216,.45)';mctx.lineWidth=.5;mctx.font='8px monospace';mctx.fillStyle='#edf4fa';
  for(const v of [0,.5,1]){
    const gx=ox+v*dw,gy=oy+v*dh;
    mctx.beginPath();mctx.moveTo(gx,oy);mctx.lineTo(gx,oy+dh);mctx.moveTo(ox,gy);mctx.lineTo(ox+dw,gy);mctx.stroke();
    const ll=lonLatAt(S.meta.corners_lonlat,v,1),lat=lonLatAt(S.meta.corners_lonlat,0,v);
    mctx.textAlign='center';mctx.fillText(ll?`${ll[0].toFixed(3)}°`:`${Math.round(v*S.meta.src_w)} px`,gx,oy+dh+12);
    mctx.save();mctx.translate(ox-7,gy);mctx.rotate(-Math.PI/2);mctx.fillText(ll?`${lat[1].toFixed(3)}°`:`${Math.round(v*S.meta.src_h)} px`,0,0);mctx.restore();
  }
  mctx.textAlign='left';mctx.fillText(S.meta.corners_lonlat?'SCENE FOOTPRINT':'LOCAL IMAGE FRAME',ox,12);
  mctx.save();mctx.beginPath();mctx.rect(ox,oy,dw,dh);mctx.clip();
  const px = ox + (camera.position.x / S.W + 0.5) * dw, py = oy + (camera.position.z / S.H + 0.5) * dh;
  const dir = new THREE.Vector3(); camera.getWorldDirection(dir);
  const ang = Math.atan2(dir.z, dir.x);
  mctx.fillStyle = 'rgba(245,158,11,.25)';
  mctx.beginPath(); mctx.moveTo(px, py); mctx.arc(px, py, 40, ang - 0.45, ang + 0.45); mctx.closePath(); mctx.fill();
  mctx.fillStyle = '#f59e0b'; mctx.beginPath(); mctx.arc(px, py, 4, 0, Math.PI * 2); mctx.fill();
  mctx.strokeStyle = '#0d1117'; mctx.lineWidth = 1.5; mctx.stroke();
  if (S.marker) {
    mctx.fillStyle = '#38bdf8';
    mctx.fillRect(ox + (S.marker.userData.x / S.W + 0.5) * dw - 3, oy + (S.marker.userData.z / S.H + 0.5) * dh - 3, 6, 6);
  }
  mctx.restore();
  mm.dataset.ox = ox; mm.dataset.oy = oy; mm.dataset.dw = dw; mm.dataset.dh = dh;
}
mm.addEventListener('click', (e) => {
  if (!S.mesh) return;
  const r = mm.getBoundingClientRect(), k = mm.width / r.width;
  const x = (((e.clientX - r.left) * k - mm.dataset.ox) / mm.dataset.dw - 0.5) * S.W;
  const z = (((e.clientY - r.top) * k - mm.dataset.oy) / mm.dataset.dh - 0.5) * S.H;
  if(Math.abs(x)>S.W/2||Math.abs(z)>S.H/2)return;
  const delta = new THREE.Vector3(x, terrainY(x, z), z).sub(S.nav === 'orbit' ? orbit.target : camera.position);
  if (S.nav === 'orbit') { delta.y = 0; S.cameraFlight={started:performance.now(),duration:850,fromCamera:camera.position.clone(),toCamera:camera.position.clone().add(delta),fromTarget:orbit.target.clone(),toTarget:orbit.target.clone().add(delta)};requestRender(); }
  else { camera.position.x = x; camera.position.z = z; clampCamera(); }
});

// ------------------------------------------------------------------ linked 2D source/height comparison
function drawComparison() {
  if (!S.h || !S.texImg) return;
  const rgb = $('#compare-rgb'), height = $('#compare-height');
  for (const cv of [rgb, height]) { cv.width = S.gw; cv.height = S.gh; }
  rgb.getContext('2d').drawImage(S.texImg, 0, 0, S.gw, S.gh);
  const ctx = height.getContext('2d'), data = ctx.createImageData(S.gw, S.gh);
  const lo = pct(S.h, 0.01), hi = pct(S.h, 0.99);
  for (let i = 0; i < S.h.length; i++) {
    const c = Number.isFinite(S.h[i]) ? ramp('height', (S.h[i] - lo) / (hi - lo || 1)) : [0.15, 0.19, 0.23];
    const k = 4 * i;
    data.data[k] = Math.round(c[0] * 255); data.data[k + 1] = Math.round(c[1] * 255);
    data.data[k + 2] = Math.round(c[2] * 255); data.data[k + 3] = 255;
  }
  ctx.putImageData(data, 0, 0);
  $('#compare-units').textContent = S.meta.units === 'metre' ? '(m)' : '(relative)';
  $('#compare-range').textContent = `P01 ${fmt(reportedHeight(lo), S.meta.units === 'metre' ? 1 : 3)} – P99 ${fmt(reportedHeight(hi), S.meta.units === 'metre' ? 1 : 3)} ${S.units}`;
  $('#compare-readout').textContent = 'Move across either image to inspect aligned pixels';
  $$('.comparison-crosshair').forEach((el) => el.classList.add('hidden'));
}

function setComparison(on) {
  const active = Boolean(on && S.h);
  $('#comparison').classList.toggle('hidden', !active);
  $('#stage').classList.toggle('comparing', active);
  $('#compare-toggle').setAttribute('aria-pressed', String(active));
  if (active) drawComparison();
  requestAnimationFrame(resize);
}

function comparisonImageRect(cv) {
  const box = cv.getBoundingClientRect();
  const scale = Math.min(box.width / S.gw, box.height / S.gh);
  const w = S.gw * scale, h = S.gh * scale;
  return { left: box.left + (box.width - w) / 2, top: box.top + (box.height - h) / 2, width: w, height: h };
}

function showComparisonPoint(u, v, doProbe = false) {
  const x = (u - 0.5) * S.W, z = (v - 0.5) * S.H;
  for (const pane of $$('.comparison-pane')) {
    const cv = pane.querySelector('canvas'), image = comparisonImageRect(cv), outer = pane.getBoundingClientRect();
    const mark = pane.querySelector('.comparison-crosshair');
    mark.style.left = `${image.left - outer.left + u * image.width}px`;
    mark.style.top = `${image.top - outer.top + v * image.height}px`;
    mark.classList.remove('hidden');
  }
  const h = sampleGrid(S.h, x, z);
  const { r, c } = toGrid(x, z), sa = slopeAt(r, c);
  const pixel = `${Math.round(u * Math.max(0, S.meta.src_w - 1))}, ${Math.round(v * Math.max(0, S.meta.src_h - 1))}`;
  const slope = S.meta.units === 'metre' ? `slope ${fmt(sa.slope, 1)}°` : `gradient ${fmt(sa.slope, 3)} / m`;
  $('#compare-readout').textContent = `Pixel ${pixel} · estimated ${fmt(reportedHeight(h), S.meta.units === 'metre' ? 2 : 3)} ${S.units} · ${slope}`;
  if (doProbe) {
    showTab('analyse');
    S.tool = 'probe'; $$('#tool button').forEach((b) => b.classList.toggle('active', b.dataset.tool === 'probe'));
    clearTools(); probe(new THREE.Vector3(x, 0, z));
  }
}

$$('.comparison-pane canvas').forEach((cv) => {
  cv.addEventListener('pointermove', (e) => {
    if (!S.h) return;
    const image = comparisonImageRect(cv);
    const u = (e.clientX - image.left) / image.width, v = (e.clientY - image.top) / image.height;
    if (u < 0 || u > 1 || v < 0 || v > 1) return;
    showComparisonPoint(u, v);
  });
  cv.addEventListener('click', (e) => {
    if (!S.h) return;
    const image = comparisonImageRect(cv);
    const u = (e.clientX - image.left) / image.width, v = (e.clientY - image.top) / image.height;
    if (u >= 0 && u <= 1 && v >= 0 && v <= 1) showComparisonPoint(u, v, true);
  });
});

// ------------------------------------------------------------------ validation panel
function renderMetrics() {
  const m = S.meta?.metrics, el = $('#metrics');
  const warn = S.meta?.backbone === 'heuristic-fallback'
    ? '<p class="note worse">⚠ Processed with the offline heuristic fallback (depth model weights were not available). Numbers are not representative.</p>' : '';
  const cal = S.meta?.calibration || {};
  const calRows = [['Method', cal.method], ['Evidence', cal.evidence_level || (cal.method === 'dem+prior' ? 'approximate' : null)],
    ['Backbone', /gamus/i.test(S.meta?.backbone||'')?'GAMUS fine-tuned':S.meta?.backbone], ['Scale source', cal.scale_source],
    ['Structure scale k', cal.scale_k !== undefined && `${fmt(cal.scale_k, 2)} m / unit`],
    ['DEM fit r', cal.fit_r !== undefined && fmt(cal.fit_r, 2)],
    ['DEM coverage', cal.dem_coverage !== undefined && `${fmt(cal.dem_coverage * 100, 1)} %`],
    ['DEM origin', cal.dem_origin],
    ['Copernicus tiles', cal.dem_tile_names?.join(', ')],
    ['GCPs used', cal.n_gcp || null],
    ['GCP fit residual', cal.gcp_residual_rmse_m !== undefined && `${fmt(cal.gcp_residual_rmse_m)} m (in-sample)`],
    ['GCP leave-one-out', cal.gcp_loo_rmse_m !== undefined && `${fmt(cal.gcp_loo_rmse_m)} m`],
    ['GCP spatial spread', cal.gcp_spread_fraction !== undefined && `${fmt(cal.gcp_spread_fraction * 100, 1)} %`],
    ['DEM type', cal.dem_kind && (cal.dem_kind === 'surface' ? `surface model – sees buildings (r ${fmt(cal.dem_hp_r, 2)})` : `bare-earth terrain (r ${fmt(cal.dem_hp_r, 2)})`)],
    ['DEM resolution', cal.dem_resolution_m !== undefined && `${fmt(cal.dem_resolution_m, 1)} m`],
    ['Matches DEM at its resolution', cal.reference_consistent === true ? `yes · residual ${fmt(cal.consistency_rmse_m)} m` : null],
    ['Vertical datum', cal.vertical_datum],
    ['Learned scale', S.meta?.units==='metre'&&cal.learned_scale_k !== undefined && `${fmt(cal.learned_scale_k, 1)} m / unit`],
    ['Shadow check', cal.shadow_iou !== undefined && `IoU ${fmt(cal.shadow_iou, 2)} · sun az ${fmt(cal.sun_azimuth_deg, 0)}°`],
    ['Structure model', cal.structure_model],
    ['Rotation ensemble', S.meta?.tta ? `${S.meta.tta} passes` : null],
    ['Uncertainty', S.meta?.uncertainty_status || (S.confidence ? 'Provisional; ensemble agreement is not accuracy probability' : 'Unavailable - no model ensemble')],
    ['Note', S.meta?.units==='metre'?cal.note:'Relative heights are uncalibrated. A learned display scale shapes the 3D view; surveyed GCPs are needed to establish metres.']].filter(([, v]) => v);
  const calHtml = `<h3 style="margin-top:16px">Calibration</h3><div class="kv">${calRows.map(([k, v]) => `<b>${escapeHtml(k)}</b><span>${escapeHtml(v)}</span>`).join('')}</div>`;
  const cop = m?.vs_copernicus_30m;
  const copHtml = cop ? `<div class="validation-baseline"><strong>Agreement with Copernicus GLO-30 (30 m)</strong><span>RMSE ${fmt(cop.rmse)} m · bias ${fmt(cop.bias)} m · ${cop.n} cells</span><small>${escapeHtml(cop.note || 'DSM averaged to native Copernicus cells.')}</small></div>` : '';
  const calWarning = cal.evidence_level === 'approximate' || cal.method === 'dem+prior'
    ? `<p class="note">Building/tree heights use ${cal.scale_source === 'learned pixel-footprint scale' ? 'the model\'s learned metre-per-pixel scale (±40 %)' : 'a scene prior'}; terrain comes from the DEM. Add GCPs or a surface DEM (Copernicus/SRTM) for measured scale.</p>`
    : cal.evidence_level === 'provisional-gcp'
      ? '<p class="note">GCP calibration is provisional: more distributed surveyed points and an independent reference are needed.</p>'
      : cal.evidence_level === 'provisional'
        ? '<p class="note">Automatic building-height cues are provisional. They are calibration inputs, not independent validation; compare against held-out LiDAR or surveyed heights before claiming accuracy.</p>' : '';
  if (!m?.absolute && !m?.affine_aligned) {
    el.innerHTML = warn + copHtml + calWarning + '<h3>Validate against an independent reference</h3><p class="note">Upload a LiDAR / DSM GeoTIFF to score this scene. Reprocess the optical image with the reference selected under Advanced; calibration and validation are separate inputs.</p><button id="add-validation-reference" type="button" class="primary">Add reference with imagery</button>' + calHtml;
    $('#add-validation-reference').onclick = () => {showTab('upload');$('#import-advanced').open = true;form.reference.focus();};
    addDemComparison(el);return;
  }
  const main = m.absolute || m.affine_aligned;
  const aligned = !m.absolute;
  const cards = [[aligned ? 'Aligned RMSE' : 'RMSE', fmt(main.rmse), 'm'], [aligned ? 'Aligned MAE' : 'MAE', fmt(main.mae), 'm'], ['Pearson r', fmt(main.r, 3), '']];
  const heldOutReference = ['dc-glover-park', 'dc-capitol-hill'].includes(S.id);
  const rowDefs = [['absolute', heldOutReference ? 'DepthWizard DSM (blind)' : 'DepthWizard DSM'], ['baseline_dem', 'Input DEM only (baseline)'], ['aggregated_30m', 'DSM averaged to 30 m'], ['structure_ndsm', 'Above-ground (nDSM)'], ['buildings', 'Per-building roof height'], ['affine_aligned', 'Shape only (fitted to reference)']];
  const b = m.baseline_dem;
  const deltaPct = b && main?.rmse && b.rmse ? 100 * (b.rmse - main.rmse) / b.rmse : null;
  const baselineHtml = deltaPct === null ? '' : `<div class="validation-baseline ${deltaPct < 0 ? 'regressed' : ''}"><strong>${deltaPct >= 0 ? '↓' : '↑'} ${fmt(Math.abs(deltaPct), 1)}% RMSE ${deltaPct >= 0 ? 'improvement' : 'increase'}</strong><span>Estimated DSM ${fmt(main.rmse)} m vs input DEM ${fmt(b.rmse)} m</span></div>`;
  const cls = (k, key, lowerBetter = true) => (!b || k !== 'absolute') ? '' : ((lowerBetter ? m[k][key] < b[key] : m[k][key] > b[key]) ? 'better' : 'worse');
  const meanings={absolute:'DSM compared directly with the supplied reference; no reference-fitted scale.',baseline_dem:'Input DEM compared with the same reference and valid-pixel mask.',aggregated_30m:'DSM and reference averaged to 30 m before comparison.',structure_ndsm:'Above-ground height compared after subtracting terrain.',buildings:'Roof height scored within mapped building footprints.',affine_aligned:'Scale and offset fitted to the reference; shape diagnostics, not blind accuracy.'};
  const table = `<table class="t"><tr><th></th><th>RMSE</th><th>MAE</th><th>NMAD</th><th>r</th></tr>` +
    rowDefs.filter(([k]) => m[k]).map(([k, label]) => `<tr title="${escapeHtml(meanings[k])}"><td tabindex="0" title="${escapeHtml(meanings[k])}">${label}</td><td class="${cls(k, 'rmse')}">${fmt(m[k].rmse)}</td><td class="${cls(k, 'mae')}">${fmt(m[k].mae)}</td><td>${fmt(m[k].nmad)}</td><td class="${cls(k, 'r', false)}">${fmt(m[k].r, 3)}</td></tr>`).join('') + '</table>';
  const land = m.by_landscape || {};
  const landTable = Object.keys(land).length ? `<h3>Stability across landscapes</h3><table class="t"><tr><th>Class</th><th>Tiles</th><th>RMSE</th><th>MAE</th><th>r</th></tr>` +
    Object.entries(land).map(([k, v]) => `<tr><td>${k}</td><td>${v.tiles}</td><td>${fmt(v.rmse)}</td><td>${fmt(v.mae)}</td><td>${fmt(v.r, 3)}</td></tr>`).join('') + '</table>' : '';
  const heights = m.by_height_band || {};
  const hasBandBaseline = Object.values(heights).some((v) => v.baseline_dem);
  const heightTable = Object.keys(heights).length ? `<h3>Error by reference height above ground</h3><table class="t"><tr><th>Height band</th><th>Pixels</th><th>Estimate RMSE</th>${hasBandBaseline ? '<th>DEM RMSE</th>' : ''}</tr>` +
    Object.entries(heights).map(([k, v]) => `<tr><td>${k}</td><td>${v.estimate.n.toLocaleString()}</td><td>${fmt(v.estimate.rmse)} m</td>${hasBandBaseline ? `<td>${fmt(v.baseline_dem?.rmse)} m</td>` : ''}</tr>`).join('') + '</table>' : '';
  const edgeHtml = m.edge_gradient_rmse !== undefined ? `<h3>Edge detail</h3><div class="kv"><b>Gradient RMSE</b><span>${fmt(m.edge_gradient_rmse, 3)} m/m</span><b>DEM baseline</b><span>${fmt(m.baseline_edge_gradient_rmse, 3)} m/m</span></div>` : '';
  const acc = `<div class="kv"><b>|error| ≤ 1 m</b><span>${fmt(main.within_1m * 100, 1)} %</span><b>|error| ≤ 2 m</b><span>${fmt(main.within_2m * 100, 1)} %</span><b>|error| ≤ 5 m</b><span>${fmt(main.within_5m * 100, 1)} %</span><b>Bias</b><span>${fmt(main.bias)} m</span></div>`;
  el.innerHTML = warn + copHtml + (aligned ? '<p class="note">Relative heights are fitted to this reference for shape diagnostics. These values are not operational metric accuracy.</p>' : '') + `<div class="cards">${cards.map(([l, v, u]) => `<div class="card"><div class="v">${v}<small> ${u}</small></div><div class="l">${l}</div></div>`).join('')}</div>` + baselineHtml +
    (S.meta.validation_plot ? `<h3>Estimated vs reference</h3><div class="charts">${scatterSvg(S.meta.validation_plot, S.meta.units === 'metre' ? 'm' : 'rel')}${histSvg(S.meta.validation_plot)}</div>` : '') +
    `<details class="validation-details"><summary>Detailed accuracy breakdown</summary>` + calWarning + table +
    (m.buildings ? `<p class="note">Per-building: ${m.buildings.n} footprints · median roof ${fmt(m.buildings.est_median, 1)} m estimated vs ${fmt(m.buildings.ref_median, 1)} m reference.</p>` : '') +
    landTable + heightTable + renderStratifiedValidation(m.stratified) + edgeHtml + acc + calHtml + `</details>` +
    `<p class="note">The Truth layer colours signed error against the supplied reference; Profile compares a selected cross-section.</p>`;
  addDemComparison(el);
}

function renderStratifiedValidation(result) {
  if (!result) return '<p class="note">Class-specific errors and uncertainty coverage need aligned reference labels and heights.</p>';
  const coverage = Object.entries(result.uncertainty_coverage || {}).map(([name, row]) =>
    `<tr><td>${escapeHtml(name)}</td><td>${row.n.toLocaleString()}</td><td>${fmt(row.one_sigma * 100,1)}%</td><td>${fmt(row.two_sigma * 100,1)}%</td></tr>`).join('');
  const classes = Object.entries(result.classes || {}).map(([name, row]) =>
    `<tr><td>${escapeHtml(name)}</td><td>${row.n.toLocaleString()}</td><td>${fmt(row.rmse)} m</td><td>${fmt(row.bias)} m</td></tr>`).join('');
  return `<p class="note">Route: ${escapeHtml(result.route)}. ${escapeHtml(result.height_band_basis || '')} ${escapeHtml(result.class_basis || '')}</p>`
    + (classes ? `<h3>Error by reference class</h3><table class="t"><tr><th>Class</th><th>Pixels</th><th>RMSE</th><th>Bias</th></tr>${classes}</table>` : '')
    + (coverage ? `<h3>Provisional uncertainty coverage</h3><table class="t"><tr><th>Group</th><th>Pixels</th><th>±1σ</th><th>±2σ</th></tr>${coverage}</table><p class="note">Raw signed errors, including bias. Coverage is diagnostic and does not certify confidence probabilities.</p>` : '');
}

function addDemComparison(el) {
  if (!S.demBase || S.meta?.units !== 'metre') return;
  const action = document.createElement('button');action.type='button';action.className='dem-comparison-action';
  const isCopernicus = /copernicus|cop30|glo.?30/i.test(JSON.stringify(S.meta.calibration || {}));
  action.textContent=isCopernicus?'Compare with Copernicus':'Compare with input DEM';
  action.title='Display-grid DSM minus the actual input DEM. Calibration consistency, not independent validation.';
  action.onclick=()=>{if(S.swipeActive)setSwipe(false);setMode('demdiff');toast('DSM − input DEM on the display grid. This checks calibration consistency; it is not independent accuracy.');};
  el.append(action);
}

// ------------------------------------------------------------------ UI wiring
function busy(on, text) { $('#busy').classList.toggle('hidden', !on); $('#stage').classList.toggle('scene-loading', on); $('#stage').setAttribute('aria-busy', String(on)); if (text) $('#busy-text').textContent = text; }
function showTab(t) {
  if (t === 'upload') {
    $('#help').classList.add('hidden');
    dialogManager.open('upload-modal');
    refreshLocalModel();
    return;
  }
  $('#upload-modal').classList.add('hidden');
  dialogManager?.sync();
  $$('.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === t));
  $$('.panel').forEach((p) => p.classList.toggle('hidden', p.id !== 'tab-' + t));
}
async function refreshLocalModel() {
  const note = $('#local-model-status');
  try {
    const res = await apiFetch('api/local-model', {silent:true});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const info = await res.json();
    const select = $('#upload-form').model;
    const prior = select.value;
    select.querySelector('option[data-local-model]')?.remove();
    if (info.ready) {
      const option = new Option('DepthWizard v6a · metric heights (recommended)', info.path);
      option.dataset.localModel = 'true';
      select.add(option, 0);
      if (prior === info.path || !select.dataset.userSelected) select.value = info.path;
    }
    note.textContent = info.error || (info.stage
      ? `${info.stage}${info.epochs_done ? ` · ${info.epochs_done} epochs complete` : ''}${info.ready ? ' · checkpoint available' : ''}`
      : info.ready ? 'Local GAMUS checkpoint available.' : 'Use a pretrained backbone or enter a local checkpoint path.');
  } catch {
    note.textContent = 'Use a pretrained backbone or enter a local checkpoint path.';
  }
}
$$('.tabs button').forEach((b) => b.onclick = () => showTab(b.dataset.tab));
$('#import-btn').onclick = () => showTab('upload');
$('#upload-close').onclick = () => $('#upload-modal').classList.add('hidden');
$$('[data-close-upload]').forEach((el) => el.onclick = () => $('#upload-modal').classList.add('hidden'));
$('#library-toggle').onclick = () => $('#app').classList.toggle('library-open');
function setMode(m) {
  if(m==='confidence'&&!S.confidence){toast('No ensemble confidence layer is available for this scene.');return;}
  if (S.swipeActive && S.swipeKind === 'model' && m !== 'optical') setSwipe(false, 'model');
  S.mode = m; $$('#layer-dock button[data-mode]').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  $('#layer-legend')?.classList.toggle('topo-active', ['topo', 'topogray'].includes(m));
  if (S.mesh) applyShading();
}
$$('#shade-mode button').forEach((b) => b.onclick = () => setMode(b.dataset.mode));
$$('#tool button').forEach((b) => b.onclick = () => {
  S.tool = b.dataset.tool; $$('#tool button').forEach((x) => x.classList.toggle('active', x === b)); clearTools();
});
$$('#nav-mode button').forEach((b) => b.onclick = () => setNav(b.dataset.nav === 'tour' && S.nav === 'tour' ? 'orbit' : b.dataset.nav));
$('#exag').oninput = (e) => {
  S.exag = +e.target.value; $('#exag-v').textContent = S.exag.toFixed(1) + '×';
  if (S.mesh) {
    applyHeights(); createBuildingMeshes(S.buildings); syncTreeLayer();
    if (S.mesh.material.normalMap) { const k = Math.min(4, S.exag); S.mesh.material.normalScale.set(k, k); }
  }
};
$('#smooth').oninput = (e) => { S.smoothingM = +e.target.value; $('#smooth-v').textContent = `${S.smoothingM.toFixed(2)} m`; if (S.h) updateRenderHeight(); };
$('#sun').oninput = (e) => { $('#sun-v').textContent = e.target.value + '° (manual)'; $('#time-v').textContent = 'manual'; setSun(+e.target.value); };
$('#contours').onchange = (e) => { uniforms.uContourOn.value = e.target.checked ? 1 : 0; };
$('#contour-int').oninput = (e) => {
  const step = Math.max(0.001, +e.target.value || 0.1);
  if (S.mode === 'topo' || S.mode === 'topogray') { S.topoManualStep = step; S.topoStep = step; }
  uniforms.uContourInt.value = step * verticalDisplayFactor();
};
$('#wire').onchange = (e) => { if (S.mesh) S.mesh.material.wireframe = e.target.checked; };
$('#despike').onchange = () => { if (S.h) updateRenderHeight(); };
$('#trees')?.addEventListener('change', (e) => {
  S.treesEnabled = e.target.checked;
  if (S.h) updateRenderHeight();
});
$('#exposure').oninput = (e) => { renderer.toneMappingExposure = +e.target.value; $('#exposure-v').textContent = (+e.target.value).toFixed(2); };
$('#quality').onchange = (e) => applyQuality(e.target.value);
function applyQuality(q) {
  frameBudget?.reset();
  S.quality = q;
  S.aoEnabled = true;
  const size = q === 'cinematic' ? 4096 : q === 'performance' ? 1024 : 2048;
  if (sun.shadow.mapSize.x !== size) {
    sun.shadow.mapSize.set(size, size);
    sun.shadow.map?.dispose(); sun.shadow.map = null;
  }
  renderer.shadowMap.enabled = q !== 'performance';
  renderer.setPixelRatio(q === 'performance' ? 1 : Math.min(devicePixelRatio, q==='balanced'?1.5:2));
  renderer.shadowMap.needsUpdate=true;
  resize();
  if (S.mesh) setSun(+$('#sun').value);
  updateCinematicScene();
  if (S.treeGroup) updateTreeLod(S.treeGroup, camera, { quality: q, viewportHeight: canvas.clientHeight, force: true });
  try { localStorage.setItem('dw-quality', q); } catch {}
}
{ let q = 'balanced'; try { q = localStorage.getItem('dw-quality') || q; } catch {} $('#quality').value = q; S.quality = q; setTimeout(() => applyQuality(q), 0); }

// small non-blocking notifications instead of alert()
function toast(msg, kind = 'info', ms = 5000, retry = null) {
  const t = document.createElement('div'); t.className = `toast ${kind}`; t.textContent = msg;
  if (retry) { const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Retry'; button.onclick = () => { t.remove(); Promise.resolve(retry()).catch(() => {}); }; t.append(button); }
  $('#toast-host').appendChild(t); setTimeout(() => t.remove(), ms);
}
window.toast = toast;
$('#reset').onclick = () => S.mesh && resetView();
$('#topdown').onclick = topDownView;
$('#compare-toggle').onclick = () => setComparison($('#comparison').classList.contains('hidden'));
$('#compare-close').onclick = () => setComparison(false);
$('#fullscreen').onclick = async () => {
  if (document.fullscreenElement === $('#stage')) await document.exitFullscreen();
  else await $('#stage').requestFullscreen();
  requestAnimationFrame(resize);
};
document.addEventListener('fullscreenchange', () => requestAnimationFrame(resize));
function toggleHelp() { if (dialogManager.isOpen('help')) dialogManager.close('help'); else dialogManager.open('help'); }
$('#help-btn').onclick = toggleHelp;
$('#help-close').onclick = () => $('#help').classList.add('hidden');
$('#dl-dsm').onclick = () => S.id && (location.href = `api/scenes/${S.id}/dsm`);
$('#dl-report').onclick = () => S.id && window.open(`api/scenes/${S.id}/report`, '_blank');
$$('#view-mode button').forEach((b) => b.onclick = () => setViewGeometry(b.dataset.view));
$('#swipe-toggle').onclick = () => setSwipe(!(S.swipeActive && S.swipeKind === 'dem'), 'dem');
let swipeDragging = false;
const swipeDiv = $('#swipe-divider');
if (swipeDiv) {
  swipeDiv.addEventListener('pointerdown', (e) => {
    swipeDragging = true;
    swipeDiv.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  window.addEventListener('pointermove', (e) => {
    if (!swipeDragging) return;
    const rect = canvas.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    updateSwipePosition(frac);
  });
  window.addEventListener('pointerup', () => {
    swipeDragging = false;
  });
}
$$('#project-stepper [data-tab-target]').forEach((el) => {
  el.onclick = () => showTab(el.dataset.tabTarget);
});

function closeExportMenu() { $('#export-menu').classList.add('hidden'); $('#export-menu-btn').setAttribute('aria-expanded', 'false'); }
function toggleExportMenu() {
  if (!S.id) return;
  const open = $('#export-menu').classList.toggle('hidden') === false;
  $('#export-menu-btn').setAttribute('aria-expanded', String(open));
}
$('#dl-dsm-header').onclick = toggleExportMenu;
$('#export-menu-btn').onclick = toggleExportMenu;
function saveView() {
  if (!S.id) return;
  renderer.render(scene, camera);
  const out = document.createElement('canvas'); out.width = canvas.width; out.height = canvas.height + 78;
  const ctx = out.getContext('2d'); ctx.drawImage(canvas, 0, 0);
  ctx.fillStyle = '#091b29'; ctx.fillRect(0, canvas.height, out.width, 78);
  ctx.fillStyle = '#e9f4f9'; ctx.font = `600 ${Math.max(20, Math.round(out.width / 72))}px system-ui`;
  ctx.fillText(`DepthWizard · ${S.meta.input}`, 20, canvas.height + 30);
  ctx.fillStyle = '#a7c4d4'; ctx.font = `${Math.max(15, Math.round(out.width / 96))}px system-ui`;
  const provenance = `${S.meta.calibration?.method === 'input-dem' ? 'Input DEM (not estimated)' : S.meta.units === 'metre' ? 'Metric DSM' : 'Relative rDSM'} · ${S.meta.calibration?.method || 'uncalibrated'} · ${S.meta.backbone || 'unknown model'} · ${S.mode} layer · ${S.exag.toFixed(1)}× display Z`;
  ctx.fillText(provenance, 20, canvas.height + 57, out.width - 40);
  const a = document.createElement('a'); a.href = out.toDataURL('image/png');
  a.download = `depthwizard_${S.id}_${S.mode}.png`; a.click();
}
$('#shot').onclick = saveView;
$$('#export-menu [data-export]').forEach((b) => b.onclick = async () => {
  closeExportMenu(); if (!S.id) return;
  const kind = b.dataset.export;
  if (kind === 'shot') {saveView();return;}
  const endpoints={all:'export-all.zip',dsm:'dsm',report:'report',evidence:'evidence',glb:'mesh.glb?resolution=256',obj:'mesh.obj.zip?resolution=256',cityjson:'buildings.city.json',ply:'points.ply',heightmap:'heightmap.png?bits=16'};
  const endpoint=endpoints[kind]||`product/${kind}`;
  const sceneId=S.id;
  const response=await apiFetch(`api/scenes/${sceneId}/${endpoint}`),blob=await response.blob();
  const disposition=response.headers.get('Content-Disposition')||'';
  const name=disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const extensions={all:'zip',dsm:'tif',report:'pdf',evidence:'json',glb:'glb',obj:'zip',cityjson:'json',ply:'ply',heightmap:'png'};
  const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;
  a.download=(name||`depthwizard_${sceneId}_${kind}.${extensions[kind]||'tif'}`).replace(/[\\/]/g,'_');
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  toast('Export ready. Your browser will save the file.');
});
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('.header-actions,#export-menu')) closeExportMenu();
});
fly.addEventListener('lock', updateNavigationHint);
fly.addEventListener('unlock', () => { S.keys = {}; updateNavigationHint(); });

addEventListener('keydown', (e) => {
  if (dialogManager?.isOpen()) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openCommandPalette(); return; }
  if (e.target.matches('input, select, textarea')) return;
  if (e.shiftKey && e.code === 'KeyG') {
    if (!e.repeat) setCoordinateGrid(!gridVisible);
    e.preventDefault(); return;
  }
  if (e.shiftKey && e.code === 'KeyC') {
    if (!e.repeat) setNav(S.nav === 'tour' ? 'orbit' : 'tour');
    e.preventDefault(); return;
  }
  if (e.key === 'Escape' && S.nav === 'tour' && !S.presentation) { setNav('orbit'); return; }
  if (e.shiftKey && /^Digit[1-9]$/.test(e.code)) {
    const m = ['optical', 'topo', 'height', 'hazard', 'ndsm', 'confidence', 'landslide', 'change', 'error'][Number(e.code.slice(-1)) - 1];
    const b = $(`#layer-dock button[data-mode="${m}"]`); if (b && !b.disabled && !b.classList.contains('layer-unavailable')) setMode(m);
    e.preventDefault(); return;
  }
  S.keys[e.code] = true;
  if ((S.nav === 'fly' || S.nav === 'walk') && ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'Space'].includes(e.code)) { e.preventDefault(); return; }
  const k = e.key.toLowerCase();
  if (k === 'o') setNav('orbit'); else if (k === 'f') setNav('fly'); else if (k === 't') setNav('tour');
  else if (k === 'd') topDownView(); else if (k === 'v') setComparison($('#comparison').classList.contains('hidden'));
  else if (k === 'r' && S.mesh) resetView(); else if (k === 'h') toggleHelp();
  else if (k === 'c' && S.mesh) setViewGeometry(S.viewGeometry === 'city' ? 'surface' : 'city');
  else if (k === 's' && S.mesh) $('#swipe-toggle').click();
  else if (k === 'e') $('#dl-dsm-header').click();
  else if (k === 'g') openGallery();
  else if ('123456'.includes(k)) setWorkspace(['explore', 'measure', 'disaster', 'buildings', 'calibrate', 'validate'][+k - 1]);
  else if (k === 'p') window.togglePresentation?.();
});
addEventListener('keyup', (e) => { S.keys[e.code] = false; });
addEventListener('blur', () => { S.keys = {}; });

// scenes list
async function refreshScenes(selectId, forceFetch = true) {
  const list = await fetchSceneList(forceFetch);
  const el = $('#scene-list');
  el.innerHTML = list.length ? '' : '<p class="muted">No scenes yet – use Upload.</p>';
  for (const s of list) {
    const name = sceneDisplayName(s.id, null, s.name);
    const d = document.createElement('div'); d.className = 'item'; d.dataset.id = s.id;
    d.innerHTML = `<img class="scene-thumb" alt=""><button class="scene-select" type="button" aria-pressed="false"><span class="n"></span><span class="m"></span></button><button class="x" type="button" title="Delete">✕</button>`;
    d.querySelector('.scene-thumb').src = `jobs/${encodeURIComponent(s.id)}/viewer/texture.jpg`;
    d.querySelector('.n').textContent = name;
    d.querySelector('.n').title = `${name} · Source: ${s.name}`;
    d.querySelector('.m').textContent = `${s.units === 'metre' ? 'DSM (m)' : 'rDSM'} · ${s.method ?? ''}${s.has_reference ? ' · reference' : ''}`;
    d.querySelector('.x').setAttribute('aria-label', `Delete ${name}`);
    d.querySelector('.scene-select').onclick = () => { $('#app').classList.remove('library-open'); loadScene(s.id); };
    d.querySelector('.x').onclick = async (e) => {
      e.stopPropagation(); if (!confirm(`Delete ${name}?`)) return;
      await apiFetch(`api/scenes/${s.id}`, { method: 'DELETE' }); refreshScenes();
    };
    el.appendChild(d);
  }
  $('#scene-count').textContent = list.length;
  const want = selectId || decodeURIComponent(location.hash.slice(1)) || (list.some(s => s.id === 'dc-glover-park') ? 'dc-glover-park' : list[0]?.id);
  missionUi?.scenesReady(list);
  if (want && list.some((s) => s.id === want) && want !== S.id) await loadScene(want);
  else $$('#scene-list .item').forEach((x) => {
    const active = x.dataset.id === S.id;
    x.classList.toggle('active', active);
    x.querySelector('.scene-select')?.setAttribute('aria-pressed', String(active));
  });
}

// upload
const form = $('#upload-form'), drop = $('#drop');
form.dem_source.addEventListener('change', () => { form.dataset.demSourceTouched = 'true'; });
form.image.onchange = () => { $('#drop-text').innerHTML = `<b>${escapeHtml(form.image.files[0]?.name ?? 'Drop satellite image')}</b>`; refreshImportPreview(); };
['dragover', 'dragenter'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('over')));
drop.addEventListener('drop', (e) => { e.preventDefault(); form.image.files = e.dataTransfer.files; form.image.onchange(); });
let activeProcessingJob = null;
async function trackProcessingJob(id, log) {
  activeProcessingJob = id;
  $('#job-controls').classList.remove('hidden'); $('#job-cancel').classList.remove('hidden');
  $('#job-cancel').disabled = false; $('#job-retry').classList.add('hidden');
  $('#job-stage').textContent = 'Starting processing…';
  for (;;) {
    await new Promise(resolve => setTimeout(resolve,800));
    const st = await (await apiFetch(`api/jobs/${id}`)).json();
    missionUi?.jobUpdate(st);
    $('#job-stage').textContent = st.state === 'cancelled' ? 'Cancelled · saved inputs are available for retry'
      : st.cancel_requested && st.state === 'running' ? 'Cancelling at next processing boundary…'
      : st.state === 'queued' ? `Queued · ${st.position || 0} ahead` : `${st.stage || st.state} · ${st.progress || 0}%`;
    log.textContent = (st.log || []).join('\n'); log.scrollTop = log.scrollHeight;
    if (st.state === 'done') { $('#job-controls').classList.add('hidden'); await refreshScenes(id); showTab('analyse'); return; }
    if (['error','cancelled'].includes(st.state)) {
      log.textContent += '\n'+(st.error || 'Cancelled');
      $('#job-cancel').classList.add('hidden'); $('#job-retry').classList.remove('hidden'); return;
    }
  }
}
$('#job-cancel').onclick = async () => {
  if (!activeProcessingJob) return;
  await apiFetch(`api/jobs/${activeProcessingJob}/cancel`,{method:'POST'}); $('#job-cancel').disabled = true;
};
$('#job-retry').onclick = async () => {
  if (!activeProcessingJob) return;
  $('#job-retry').disabled = true;
  const submit = form.querySelector('button[type=submit]'); submit.disabled = true;
  missionUi?.jobStart();
  try { const result = await (await apiFetch(`api/jobs/${activeProcessingJob}/retry`,{method:'POST'})).json();
    await trackProcessingJob(result.id,$('#job-log')); }
  catch (err) { missionUi?.jobUpdate({state:'error',error:err.message}); }
  finally { $('#job-retry').disabled = false; submit.disabled = false; }
};
form.onsubmit = async (e) => {
  e.preventDefault();
  if(form.dataset.detecting==='true'||form.dataset.invalidInput==='true')return;
  const fd = new FormData(form);
  fd.set('fetch_dem', form.fetch_dem.checked ? 'true' : 'false');
  if (form.dem.files.length && form.dataset.demSourceTouched !== 'true') fd.delete('dem_source');
  const customModel = String(fd.get('custom_model') || '').trim();
  if (customModel) fd.set('model', customModel);
  fd.delete('custom_model');
  // Uploaded evidence wins over an unnecessary network download. Relative
  // images retain the unchanged API contract but don't request a DEM.
  if(form.dem.files.length || form.dataset.inputPath==='relative')fd.set('fetch_dem','false');
  for (const k of ['dem', 'reference', 'gcp']) if (!form[k].files.length) fd.delete(k);
  const log = $('#job-log'); log.classList.remove('hidden'); log.textContent = 'Uploading…\n';
  const btn = form.querySelector('button[type=submit]'); btn.disabled = true;
  missionUi?.jobStart();
  try {
    const res = await apiFetch('api/process', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const { id } = await res.json();
    await trackProcessingJob(id, log);
  } catch (err) { log.textContent += '\n✕ ' + err.message;missionUi?.jobUpdate({state:'error',error:err.message}); }
  finally { btn.disabled = false; }
};

// ------------------------------------------------------------------ loop
function resize() {
  const r = canvas.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return;
  renderer.setSize(r.width, r.height, false);
  camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
  uniforms.uResolution.value.set(r.width, r.height);
  if (post.composer) { post.composer.setSize(r.width, r.height); post.gtao.setSize(r.width, r.height); }
}
new ResizeObserver(resize).observe(canvas);
const frameBudget = createFrameBudget();
let budgetLastFrame = 0;
let frames = 0, fpsT = 0, mmT = 0;
let idleSkip = 0;
renderer.setAnimationLoop(() => {
  const frameStarted = performance.now();
  const adjustment = frameBudget.record(frameStarted - budgetLastFrame, frameStarted,
    !document.hidden && S.quality === 'balanced' && !S.recording && !dialogManager?.isOpen()
    && (S.nav !== 'orbit' || orbit.autoRotate || performance.now()-lastActivity < 1500 || renderProfiler?.active));
  budgetLastFrame = frameStarted;
  if (adjustment) {
    S.aoEnabled = adjustment.level === 0;
    renderer.setPixelRatio(adjustment.level === 2 ? 1 : Math.min(devicePixelRatio, 1.5));
    renderer.shadowMap.enabled = adjustment.level < 2;
    renderer.shadowMap.needsUpdate = true;
    resize();
    $('#hud-fps').title = `Adaptive Balanced: level ${adjustment.level}, P95 ${adjustment.p95.toFixed(1)} ms, target 33.3 ms`;
  }
  const modalOpen = dialogManager?.isOpen();
  const busyAnim = S.nav !== 'orbit' || S.riseStart || S.cameraFlight || S.floodAnimating || S.missionOverlay
    || swipeDragging || S.recording || S.floodMesh || S.waterAnim || renderProfiler?.active;
  const suspendIdle = modalOpen || renderProfiler?.active;
  const idleRotation=suspendIdle ? false : boldUi?.tick(busyAnim || performance.now()-lastActivity<1500);
  if (suspendIdle) orbit.autoRotate = false;
  else missionUi?.tick(busyAnim || performance.now()-lastActivity<1500);
  if (!busyAnim && !idleRotation && performance.now() - lastActivity > 1500 && (idleSkip++ % 15) !== 0) { clock.getDelta(); return; }
  const dt = Math.min(clock.getDelta(), 0.1);
  if (S.mesh) {
    if (S.riseStart && S.buildingGroup) {
      renderer.shadowMap.needsUpdate=true;
      const t = Math.min(1, (performance.now() - S.riseStart) / S.riseDuration);
      const ease = 1 - (1 - t) ** 3;
      for (const b of S.buildingGroup.children) b.scale.y = Math.max(0.001, ease);
      if (t >= 1) S.riseStart = 0;
    }
    if (S.cameraFlight) {
      const f = S.cameraFlight, t = Math.min(1, (performance.now() - f.started) / (f.duration || 850)), e = t * t * (3 - 2 * t);
      camera.position.lerpVectors(f.fromCamera, f.toCamera, e);
      orbit.target.lerpVectors(f.fromTarget, f.toTarget, e);
      orbit.update();
      if (t >= 1) S.cameraFlight = null;
    }
    if (!modalOpen) {
      if (S.nav === 'orbit') { orbit.update(dt); clampCamera(); }
      else if (S.nav === 'fly' || S.nav === 'walk') updateFly(dt);
      else if (S.nav === 'tour') updateTour(dt);
    }
    updateCoordinateLabels();
    if (S.nav !== 'orbit') {
      if (Math.abs(camera.position.x) <= S.W / 2 && Math.abs(camera.position.z) <= S.H / 2) updateCoordinateReadout(camera.position.x, camera.position.z);
      else updateCoordinateReadout();
    }
    const agl = camera.position.y - (Math.abs(camera.position.x) < S.W / 2 && Math.abs(camera.position.z) < S.H / 2 ? terrainY(camera.position.x, camera.position.z) : 0);
    $('#hud-cam').textContent = S.meta.units === 'metre' ? `alt ${(agl / S.exag).toFixed(0)} m above surface` : 'relative vertical scale';
    if ((mmT += dt) > 0.1) { drawMinimap(); if ($('#stage').classList.contains('map-open')) drawMap(); mmT = 0; }
    if (S.missionOverlay) {
      const elapsed = (performance.now() - S.missionOverlay.userData.started) / 1000;
      for (const line of S.missionOverlay.children) {
        if (!line.userData.pathLength) continue;
        line.geometry.setDrawRange(0, Math.max(2, Math.min(line.userData.pathLength,
          Math.floor(elapsed * line.userData.pathLength / 2.5))));
      }
    }

    // Screen projection keeps the compass and centre-plane ground scale in
    // sync with zoom, camera tilt and displayed exaggeration.
    const centre = orbit.target.clone(), north = sceneNorth();
    const start=centre.clone().project(camera), end=centre.clone().addScaledVector(north,S.extent*.1).project(camera);
    const arrow=$('#north-arrow'), ax=(end.x-start.x)*canvas.clientWidth, ay=-(end.y-start.y)*canvas.clientHeight;
    if(Math.hypot(ax,ay)>1e-5)arrow.style.transform=`rotate(${Math.atan2(ax,-ay)*180/Math.PI}deg)`;
    arrow.title=S.meta.georeferenced?'North in the input CRS (grid north)':'Image up · not geographic north';
    arrow.querySelector('span').textContent=S.meta.georeferenced?'N':'UP';
    const a=new THREE.Vector3(),b=new THREE.Vector3();
    const ray=new THREE.Raycaster(), plane=new THREE.Plane(new THREE.Vector3(0,1,0),-centre.y);
    ray.setFromCamera(new THREE.Vector2(-60/canvas.clientWidth,0),camera);const ha=ray.ray.intersectPlane(plane,a);
    ray.setFromCamera(new THREE.Vector2(60/canvas.clientWidth,0),camera);const hb=ray.ray.intersectPlane(plane,b);
    const line=$('#scale-bar-line'),text=$('#scale-bar-text');
    if(ha&&hb&&a.distanceTo(b)>0){
      const pixelScene=!S.meta.georeferenced, factor=pixelScene?S.meta.src_w/S.W:1;
      const perPixel=a.distanceTo(b)*factor/60, nice=niceStep(perPixel*60);
      line.style.width=`${nice/perPixel}px`;
      text.textContent=pixelScene?`${fmt(nice,0)} px`:nice>=1000?`${fmt(nice/1000,1)} km`:`${fmt(nice,0)} m`;
      text.title='Ground-plane scale at the screen centre; oblique terrain varies with perspective.';
    }else{text.textContent='Scale unavailable';line.style.width='0';}

  }
  if (updateTreeLod(S.treeGroup, camera, { quality: S.quality === 'balanced' && S.aoEnabled === false ? 'performance' : S.quality,
    now: frameStarted, viewportHeight: canvas.clientHeight })) renderer.shadowMap.needsUpdate = true;
  const streamEnabled = S.mesh && S.meta?.units === 'metre' && Math.max(S.meta.src_w,S.meta.src_h) > 2048
    && S.viewGeometry === 'surface' && S.mode === 'optical' && !S.swipeActive && !$('#wire').checked;
  const streamKey = `${S.id}:${S.exag}:${S.base}`;
  if (streamEnabled && terrainStreamScene !== streamKey) {
    terrainStream?.dispose(); if (terrainStream) scene.remove(terrainStream.group);
    terrainStream = createTerrainStream({id:S.id,fetchApi:(...args)=>apiFetch(...args),W:S.W,H:S.H,
      worldY: h => worldY(h),maxTiles:48,maxLevel:Math.min(7,Math.ceil(Math.log2(Math.max(S.meta.src_w,S.meta.src_h)/256)))});
    terrainStreamScene = streamKey; scene.add(terrainStream.group);
  }
  if (terrainStream) {
    terrainStream.group.visible = Boolean(streamEnabled);
    const ready = streamEnabled && terrainStream.update(camera,canvas.clientHeight,frameStarted);
    S.mesh.visible = !ready;
  }
  const streamControls = Boolean(streamEnabled && terrainStream && !S.mesh.visible);
  if (S.streamControls !== streamControls) {
    S.streamControls = streamControls;
    $('#smooth').disabled = streamControls; $('#despike').disabled = streamControls;
    $('#mesh-detail').disabled = streamControls;
    $('#mesh-display-note').textContent = streamControls
      ? 'Large-scene streaming uses original DSM heights. Display smoothing is available on other layers.'
      : 'Display only · the DSM export and validation use original heights.';
  }
  renderer.info.autoReset = false; renderer.info.reset();
  if (S.swipeActive && S.baseMesh && S.mesh) {
    const w = canvas.clientWidth, h = canvas.clientHeight, split = Math.round(w * S.swipeX);
    const hideCity = S.buildingGroup?.visible;
    const hideTrees = S.treeGroup?.visible;
    renderer.setScissorTest(true);
    S.baseMesh.visible = true; S.mesh.visible = false;
    if (hideCity) S.buildingGroup.visible = false;
    if (hideTrees) S.treeGroup.visible = false;
    renderer.setScissor(0, 0, split, h); renderer.setViewport(0, 0, w, h); renderer.render(scene, camera);
    S.baseMesh.visible = false; S.mesh.visible = true;
    if (hideCity) S.buildingGroup.visible = true;
    if (hideTrees) S.treeGroup.visible = true;
    renderer.setScissor(split, 0, w - split, h); renderer.render(scene, camera);
    renderer.setScissorTest(false);
  } else if (S.quality !== 'performance' && S.aoEnabled !== false && post.composer && !S.recording) post.composer.render(dt);
  else renderer.render(scene, camera);
  renderProfiler?.record({ now: frameStarted, submissionMs: performance.now() - frameStarted,
    calls: renderer.info.render.calls, triangles: renderer.info.render.triangles, lod: getTreeLodDiagnostics(S.treeGroup) });
  if (S.floodMesh) waterUniforms.uTime.value = performance.now() / 1000;
  frames++; fpsT += dt;
  if (fpsT > 1) { $('#hud-fps').textContent = `${Math.round(frames / fpsT)} fps`; frames = 0; fpsT = 0; }
});

refreshScenes().catch(() => { $('#scene-list').innerHTML = '<p class="worse">Server not reachable – start with <code>python run.py</code>.</p>'; });
// ------------------------------------------------------------------ Anchors
let currentAnchors = [];

function renderAnchorPanel() {
  const panelGroup = $('#anchor-panel-group');
  if (!S.meta || S.meta.units !== 'metre' || !S.buildingToolsAvailable) {
    panelGroup.classList.add('hidden');
    $('#scale-badge')?.classList.add('hidden');
    return;
  }
  
  if (S.meta.calibration && S.meta.calibration.method === 'height-anchor') {
    panelGroup.classList.remove('hidden');
    const badge = $('#scale-badge');
    badge.classList.remove('hidden');
    const cal = S.meta.height_anchor || {};
    const n = cal.anchors ? cal.anchors.length : 0;
    const s = cal.s || 1.0;
    const stats = cal.stats || {};
    let txt = `Scale: ${n} anchor${n !== 1 ? 's' : ''} · s = ${s.toFixed(2)}`;
    if (n >= 3 && Number.isFinite(stats.loo_rmse)) {
      txt += ` · ±${stats.loo_rmse.toFixed(1)} m`;
    }
    badge.textContent = txt;
    if (stats.warning) {
      $('#anchor-warning').textContent = stats.warning;
      $('#anchor-warning').classList.remove('hidden');
    } else {
      $('#anchor-warning').classList.add('hidden');
    }
  } else if (currentAnchors.length > 0) {
    panelGroup.classList.remove('hidden');
  } else {
    panelGroup.classList.add('hidden');
    $('#scale-badge').classList.add('hidden');
  }

  const tbody = $('#anchor-table tbody');
  tbody.innerHTML = '';
  currentAnchors.forEach((a, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>#${a.building_id}</td>
      <td>${a.est.toFixed(1)}</td>
      <td>${a.known.toFixed(1)}</td>
      <td>${(a.known / a.est).toFixed(2)}</td>
      <td><button class="icon-btn" onclick="removeAnchor(${i})">×</button></td>
    `;
    tbody.appendChild(tr);
  });
}

window.removeAnchor = function(index) {
  currentAnchors.splice(index, 1);
  renderAnchorPanel();
};

window.addAnchor = function(id, est, known) {
  const existing = currentAnchors.findIndex(a => a.building_id === id);
  if (existing >= 0) {
    currentAnchors[existing].known = known;
  } else {
    currentAnchors.push({ building_id: id, est, known });
  }
  renderAnchorPanel();
};

$('#anchor-apply-btn').addEventListener('click', async () => {
  const btn = $('#anchor-apply-btn');
  btn.disabled = true;
  btn.textContent = 'Applying...';
  try {
    const res = await apiFetch(`/api/scenes/${S.id}/rescale`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anchors: currentAnchors.map(a => ({building_id: a.building_id, height_m: a.known})) })
    });
    if (!res.ok) throw new Error(await res.text());
    await reloadViewer();
  } catch (e) {
    toast(e.message, 'error', 8000);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Apply';
  }
});

$('#anchor-reset-btn').addEventListener('click', async () => {
  const btn = $('#anchor-reset-btn');
  btn.disabled = true;
  btn.textContent = 'Resetting...';
  try {
    const res = await apiFetch(`/api/scenes/${S.id}/rescale`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reset: true })
    });
    if (!res.ok) throw new Error(await res.text());
    currentAnchors = [];
    $('#scale-badge').classList.add('hidden');
    await reloadViewer();
  } catch (e) {
    toast(e.message, 'error', 8000);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Reset';
  }
});

async function reloadViewer() {
  if (S.id) {
    const id = S.id, geometry = S.viewGeometry, workspace = S.workspace;
    await loadScene(id);
    if(S.id!==id)return;
    if (geometry !== S.viewGeometry) setViewGeometry(geometry);
    setWorkspace(workspace);
  }
}

function updateAutoAnchorPanel() {
  const box = $('#automatic-anchor-group');
  const metric = S.meta?.units === 'metre' && S.buildingToolsAvailable;
  box.classList.toggle('hidden', !metric);
  if (!metric) return;
  const record = S.meta.auto_anchors || {}, diag = record.diagnostics || {};
  const osm = diag.osm?.accepted || 0, shadows = diag.shadow?.accepted || 0;
  const total = record.anchors?.length || 0;
  const applied = record.applied && S.meta.height_anchor?.source === 'automatic';
  const appliedAnchors = applied ? S.meta.height_anchor?.stats?.n_used || 0 : 0;
  $('#auto-anchor-status').textContent = total
    ? `${total} candidates found (${osm} OSM tags, ${shadows} shadows) · ${applied ? `${appliedAnchors} applied to scale` : 'none applied to scale'}. Tags and shadow geometry are provisional height estimates.`
    : applied ? `${appliedAnchors} automatic anchors were applied earlier; the latest scan found no current candidates. Check stored calibration provenance.`
      : `No automatic anchors available yet. ${diag.shadow?.skipped || diag.osm?.skipped || 'Sun metadata and OSM coverage may be absent.'}`;
  $('#auto-anchor-apply').disabled = !total || applied;
  const scaleBadge = $('#scale-badge');
  if (applied) {
    const usedOsm = (S.meta.height_anchor?.anchors || []).filter((a) => String(a.source || '').includes('OpenStreetMap')).length;
    if (usedOsm) scaleBadge.textContent += ` · ${usedOsm} OSM heights`;
  }
}
$('#auto-anchor-scan').onclick = async () => {
  const btn = $('#auto-anchor-scan'); btn.disabled = true;
  $('#auto-anchor-status').textContent = 'Scanning shadows and OSM height tags…';
  try {
    const r = await apiFetch(`/api/scenes/${S.id}/auto-anchors`, { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    S.meta.auto_anchors = await r.json();
    updateAutoAnchorPanel();
  } catch (e) { $('#auto-anchor-status').textContent = `Anchor scan unavailable: ${e.message}`; }
  finally { btn.disabled = false; }
};
$('#auto-anchor-apply').onclick = async () => {
  const record = S.meta?.auto_anchors, proposals = record?.anchors || [];
  const selected = proposals.filter((a) => a.confidence >= 0.65 &&
    (a.source === 'shadow geometry' || a.osm_tag === 'height'));
  if (selected.length < 3) {
    $('#auto-anchor-status').textContent = 'At least three reliable shadow or explicit OSM height anchors are needed for an automatic rescale.';
    return;
  }
  const btn = $('#auto-anchor-apply'); btn.disabled = true;
  try {
    const r = await apiFetch(`/api/scenes/${S.id}/rescale`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ automatic: true, anchors: selected.map((a) => ({ building_id: a.building_id, height_m: a.height_m,
        source: a.source, confidence: a.confidence })) }) });
    if (!r.ok) throw new Error(await r.text());
    await reloadViewer();
  } catch (e) { $('#auto-anchor-status').textContent = `Rescale unavailable: ${e.message}`; }
  finally { btn.disabled = false; }
};

function updateSceneSummary(floodCount = 0) {
  const el = $('#scene-summary');
  const list = S.buildings?.buildings || [];
  if (!S.meta) { el.classList.add('hidden'); return; }
  const tallest = list.reduce((m, b) => Math.max(m, +b.height_m || 0), 0);
  const conf = list.length ? list.reduce((t, b) => t + (+b.confidence || 0), 0) / list.length : null;
  const quality = conf == null ? 'unavailable' : conf >= 0.75 ? 'higher' : conf >= 0.5 ? 'moderate' : 'lower';
  const mode = S.meta.units === 'metre' ? 'metric DSM' : 'relative rDSM';
  const flood = S.floodActive ? ` · ${floodCount} buildings affected at ${fmt(+$('#flood-level').value, 1)} m` : '';
  el.innerHTML = `<strong>Scene summary</strong> · ${list.length} buildings${list.length ? ` · tallest ${fmt(tallest, 1)} ${S.units}` : ''}${flood} · ${quality} building reliability · ${mode}`;
  el.classList.remove('hidden');
  updateMissionHud();
}

const mapCanvas = $('#map-canvas'), mapCtx = mapCanvas.getContext('2d');
function mercator(lon, lat, z) {
  const n = 2 ** z, p = Math.max(-85.05, Math.min(85.05, lat)) * Math.PI / 180;
  return [(lon + 180) / 360 * n, (1 - Math.asinh(Math.tan(p)) / Math.PI) / 2 * n];
}
function mapProjection() {
  const corners = S.meta?.corners_lonlat;
  if (!corners || !$('#stage').classList.contains('map-open')) return null;
  const w = mapCanvas.clientWidth, h = mapCanvas.clientHeight;
  if (w < 20 || h < 20) return null;
  let zoom = 2;
  for (let z = 2; z <= 18; z++) {
    const points = corners.map(([lon, lat]) => mercator(lon, lat, z));
    const spanX = (Math.max(...points.map((p) => p[0])) - Math.min(...points.map((p) => p[0]))) * 256;
    const spanY = (Math.max(...points.map((p) => p[1])) - Math.min(...points.map((p) => p[1]))) * 256;
    if (spanX > w * 0.72 || spanY > h * 0.72) break;
    zoom = z;
  }
  const points = corners.map(([lon, lat]) => mercator(lon, lat, zoom));
  return { zoom, center: [(Math.min(...points.map((p) => p[0])) + Math.max(...points.map((p) => p[0]))) / 2,
                           (Math.min(...points.map((p) => p[1])) + Math.max(...points.map((p) => p[1]))) / 2], w, h, points };
}
function mapPixel(lon, lat, m) {
  const [x, y] = mercator(lon, lat, m.zoom);
  return [(x - m.center[0]) * 256 + m.w / 2, (y - m.center[1]) * 256 + m.h / 2];
}
function drawMap() {
  const m = mapProjection(); if (!m) return;
  if (mapCanvas.width !== Math.round(m.w) || mapCanvas.height !== Math.round(m.h)) {
    mapCanvas.width = Math.round(m.w); mapCanvas.height = Math.round(m.h);
  }
  S.mapView = m;
  mapCtx.fillStyle = '#102b3b'; mapCtx.fillRect(0, 0, m.w, m.h);
  // Offline geospatial inset uses the uploaded optical raster, never network
  // tiles. Keep the existing Mercator footprint and click-to-fly projection.
  const pts=S.meta.corners_lonlat.map(ll=>mapPixel(...ll,m));
  const [tl,tr,bl]=pts;
  if(S.texImg){
    mapCtx.save();mapCtx.beginPath();[0,1,3,2].forEach((i,k)=>k?mapCtx.lineTo(...pts[i]):mapCtx.moveTo(...pts[i]));mapCtx.closePath();mapCtx.clip();
    const tw=S.texImg.width,th=S.texImg.height;
    mapCtx.setTransform((tr[0]-tl[0])/tw,(tr[1]-tl[1])/tw,(bl[0]-tl[0])/th,(bl[1]-tl[1])/th,tl[0],tl[1]);
    mapCtx.drawImage(S.texImg,0,0);mapCtx.restore();
  }
  mapCtx.strokeStyle='rgba(183,201,216,.2)';mapCtx.lineWidth=1;
  for(let gx=32;gx<m.w;gx+=64){mapCtx.beginPath();mapCtx.moveTo(gx,0);mapCtx.lineTo(gx,m.h);mapCtx.stroke();}
  for(let gy=32;gy<m.h;gy+=64){mapCtx.beginPath();mapCtx.moveTo(0,gy);mapCtx.lineTo(m.w,gy);mapCtx.stroke();}
  const order = [0, 1, 3, 2];
  mapCtx.beginPath();
  order.forEach((i, k) => {
    const [px, py] = mapPixel(...S.meta.corners_lonlat[i], m);
    if (k === 0) mapCtx.moveTo(px, py); else mapCtx.lineTo(px, py);
  });
  mapCtx.closePath(); mapCtx.fillStyle = '#38bdf833'; mapCtx.fill();
  mapCtx.strokeStyle = '#45d6ef'; mapCtx.lineWidth = 2; mapCtx.stroke();
  const u = camera.position.x / S.W + 0.5, v = camera.position.z / S.H + 0.5;
  if (u >= 0 && u <= 1 && v >= 0 && v <= 1) {
    const ll = lonLatAt(S.meta.corners_lonlat, u, v);
    const [px, py] = mapPixel(ll[0], ll[1], m);
    mapCtx.beginPath(); mapCtx.arc(px, py, 6, 0, Math.PI * 2);
    mapCtx.fillStyle = '#f8b54d'; mapCtx.fill(); mapCtx.strokeStyle = '#08202d'; mapCtx.lineWidth = 2; mapCtx.stroke();
  }
  $('#map-status').textContent = `Offline optical map · EPSG:3857 display · source ${S.meta.crs || 'unknown'} · click within cyan footprint`; 
}
function updateMapAvailability() {
  const available = Boolean(S.meta?.corners_lonlat?.length === 4);
  $('#map-toggle').disabled = !available;
  if (!available) { $('#stage').classList.remove('map-open'); $('#map-panel').classList.add('hidden'); }
  else if ($('#stage').classList.contains('map-open')) requestAnimationFrame(drawMap);
}
$('#map-toggle').onclick = () => {
  if (!S.meta?.corners_lonlat) return;
  const on = !$('#stage').classList.contains('map-open');
  $('#stage').classList.toggle('map-open', on); $('#map-panel').classList.toggle('hidden', !on);
  $('#map-toggle').setAttribute('aria-pressed', String(on));
  if (on) requestAnimationFrame(() => { resize(); drawMap(); }); else requestAnimationFrame(resize);
};
mapCanvas.addEventListener('click', (e) => {
  const m = S.mapView; if (!m || !S.mesh) return;
  const rect = mapCanvas.getBoundingClientRect();
  const tx = (e.clientX - rect.left - m.w / 2) / 256 + m.center[0];
  const ty = (e.clientY - rect.top - m.h / 2) / 256 + m.center[1];
  const tl = m.points[0], tr = m.points[1], bl = m.points[2];
  const ax = tr[0] - tl[0], ay = tr[1] - tl[1], bx = bl[0] - tl[0], by = bl[1] - tl[1];
  const det = ax * by - ay * bx;
  if (Math.abs(det) < 1e-12) return;
  const dx = tx - tl[0], dy = ty - tl[1];
  const u = (dx * by - dy * bx) / det, v = (ax * dy - ay * dx) / det;
  if (u < 0 || u > 1 || v < 0 || v > 1) return;
  setNav('orbit');
  const target = new THREE.Vector3((u - 0.5) * S.W, terrainY((u - 0.5) * S.W, (v - 0.5) * S.H), (v - 0.5) * S.H);
  const offset = camera.position.clone().sub(orbit.target);
  S.cameraFlight = { started: performance.now(), fromCamera: camera.position.clone(), toCamera: target.clone().add(offset),
    fromTarget: orbit.target.clone(), toTarget: target };
});

async function openModelComparison() {
  if (!S.id) return;
  const sceneId = S.id;
  if (S.swipeActive && S.swipeKind === 'model') { setSwipe(false, 'model'); return; }
  if (!String(S.meta?.backbone || '').toLowerCase().includes('gamus')) {
    $('#model-compare-note').textContent = 'Load a scene processed with the GAMUS fine-tuned checkpoint first.';
    $('#model-compare-note').classList.remove('hidden'); return;
  }
  const button = $('#model-swipe-toggle');
  button.disabled = true; button.textContent = 'Preparing models…';
  try {
    let status = await (await apiFetch(`/api/scenes/${sceneId}/model-comparison`)).json();
    if (status.state === 'idle') {
      const r = await apiFetch(`/api/scenes/${sceneId}/model-comparison`, { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      status = await r.json();
    }
    while (status.state === 'queued' || status.state === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 1800));
      status = await (await apiFetch(`/api/scenes/${sceneId}/model-comparison`)).json();
      if (S.id !== sceneId) return;
    }
    if (status.state !== 'done') throw new Error(status.error || 'Pretrained inference did not finish.');
    const bin = await (await apiFetch(`jobs/${sceneId}/viewer/pretrained_height.bin?${Date.now()}`)).arrayBuffer();
    if (S.id !== sceneId) return;
    const heights = new Float32Array(bin);
    if (heights.length !== S.h.length) throw new Error('The model grids are not aligned.');
    S.modelBaseline = heights;
    const cm = status.comparison || {};
    const baselineRmse = cm.metrics?.absolute?.rmse;
    const oursRmse = S.meta.metrics?.absolute?.rmse;
    $('#model-compare-note').textContent = Number.isFinite(baselineRmse) && Number.isFinite(oursRmse)
      ? `Same optical image and reference · RMSE: pretrained ${fmt(baselineRmse, 2)} m · GAMUS ${fmt(oursRmse, 2)} m`
      : 'Same optical image · each model retains its own calibration · visual comparison only without a reference DSM';
    setMode('optical');
    setSwipe(true, 'model');
  } catch (e) {
    $('#model-compare-note').textContent = `Model comparison unavailable: ${e.message}`;
    $('#model-compare-note').classList.remove('hidden');
  } finally {
    button.disabled = S.id !== sceneId || !String(S.meta?.backbone || '').toLowerCase().includes('gamus');
    button.textContent = 'Model Compare';
  }
}
$('#model-swipe-toggle').onclick = openModelComparison;

function setIllustrativeTime(hour) {
  const h = Math.max(5, Math.min(19, +hour));
  const hr = Math.floor(h), min = Math.round((h - hr) * 60);
  $('#time-v').textContent = `${String(hr).padStart(2, '0')}:${String(min).padStart(2, '0')}`;
  const daylight = Math.max(0, Math.sin(Math.PI * (h - 6) / 12));
  const az = 90 + (h - 6) * 15;
  $('#sun').value = Math.round(az);
  $('#sun-v').textContent = `${Math.round(az)}° (illustrative)`;
  setSun(az, Math.max(3, 65 * daylight));
  sun.intensity = SUN_I * (0.12 + 0.88 * daylight);
  hemi.intensity = HEMI_I * (0.35 + 0.65 * daylight);
  const warm = 1 - Math.min(1, daylight * 2);
  sun.color.setRGB(1, 1 - 0.18 * warm, 1 - 0.4 * warm);
  const sky = new THREE.Color(0x0d1117).lerp(new THREE.Color(0x243b57), 0.4 * daylight);
  if (S.quality !== 'cinematic') { scene.background = sky; scene.fog.color.copy(sky); }
  if (post.sky?.visible) post.sky.material.uniforms.sunPosition.value.copy(sun.position).normalize();
}
$('#time-of-day').oninput = (e) => setIllustrativeTime(e.target.value);

async function initVr() {
  const btn = $('#vr-toggle');
  if (!navigator.xr || !window.isSecureContext) { btn.title = 'VR requires a supported headset/browser on HTTPS or localhost'; return; }
  try {
    if (!(await navigator.xr.isSessionSupported('immersive-vr'))) return;
    btn.disabled = false;
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType('local-floor');
    btn.onclick = async () => {
      if (renderer.xr.isPresenting) { await renderer.xr.getSession()?.end(); return; }
      if (S.swipeActive) setSwipe(false);
      const session = await navigator.xr.requestSession('immersive-vr', { optionalFeatures: ['local-floor', 'bounded-floor'] });
      await renderer.xr.setSession(session);
      btn.textContent = 'Exit VR';
      session.addEventListener('end', () => { btn.textContent = 'VR'; });
    };
  } catch (e) { btn.title = `VR unavailable: ${e.message}`; }
}
initVr();

function clearMissionOverlay() {
  if (!S.missionOverlay) return;
  scene.remove(S.missionOverlay);
  S.missionOverlay.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
  S.missionOverlay = null;
}
function drawMissionPaths(paths, color = 0xffb547) {
  clearMissionOverlay();
  const group = new THREE.Group(); group.userData.started = performance.now();
  for (const path of paths.slice(0, 24)) {
    if (!Array.isArray(path) || path.length < 2) continue;
    const points = path.map(([u, v]) => {
      const x = (u - 0.5) * S.W, z = (v - 0.5) * S.H;
      const ground = S.dtm ? worldY(sampleGrid(S.dtm, x, z)) : terrainY(x, z);
      return new THREE.Vector3(x, ground + Math.max(1.5, S.extent * 0.003), z);
    });
    const geo = new THREE.BufferGeometry().setFromPoints(points);
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color, depthTest: false }));
    line.renderOrder = 8; line.userData.pathLength = points.length;
    geo.setDrawRange(0, 2); group.add(line);
    for (const pt of [points[0], points[points.length - 1]]) {
      const marker = new THREE.Mesh(new THREE.SphereGeometry(Math.max(1, S.extent * 0.004), 12, 8),
        new THREE.MeshBasicMaterial({ color, depthTest: false }));
      marker.position.copy(pt); marker.renderOrder = 9; group.add(marker);
    }
  }
  S.missionOverlay = group; scene.add(group);
}
function showShelterMarkers(candidates) {
  clearMissionOverlay();
  const group = new THREE.Group(); group.userData.started = performance.now();
  for (const c of candidates.slice(0, 25)) {
    const b = S.buildings?.buildings?.find((item) => item.id === c.building_id);
    if (!b?.center) continue;
    const x = b.center[0], z = b.center[1];
    const marker = new THREE.Mesh(new THREE.ConeGeometry(Math.max(2, S.extent * 0.008), Math.max(5, S.extent * 0.025), 4),
      new THREE.MeshBasicMaterial({ color: 0x51e4a8, depthTest: false }));
    marker.position.set(x, worldY(b.roof_elevation_m) + Math.max(3, S.extent * 0.015), z);
    marker.renderOrder = 9; group.add(marker);
  }
  S.missionOverlay = group; scene.add(group);
}
function updateMissionAvailability() {
  const crs = String(S.meta?.crs || '').toUpperCase();
  const ready = S.meta?.units === 'metre' && S.meta?.georeferenced && S.meta?.has_dtm && S.dtm &&
    crs && !/EPSG:?(4326|3857)\b/.test(crs);
  for (const id of ['route-tool', 'shelter-tool', 'runout-tool', 'relay-tool']) $("#" + id).disabled = !ready;
  $('#runout-tool').disabled = !ready || !S.susc;
  $('#shelter-tool').disabled = !ready || !S.buildingToolsAvailable;
  if (!ready) $('#mission-result').textContent = 'Mission tools need aligned DSM and bare-ground DTM in a local projected metre CRS.';
}
async function missionRequest(action, point) {
  const u = point ? point.x / S.W + 0.5 : 0.5, v = point ? point.z / S.H + 0.5 : 0.5;
  const body = { action, u, v, water_level_m: +$('#flood-level').value,
    flood_source: S.floodSource || 'edge', max_uncertainty_m:+$('#route-max-sigma').value };
  if (S.floodSeed != null) {
    body.flood_seed_u = (S.floodSeed % S.gw) / Math.max(1, S.gw - 1);
    body.flood_seed_v = Math.floor(S.floodSeed / S.gw) / Math.max(1, S.gh - 1);
  }
  const res = await apiFetch(`/api/scenes/${S.id}/mission`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
async function runMission(action, point = null) {
  S.missionAction = null;
  $$('.mission-actions button').forEach((b) => b.classList.remove('active'));
  const out = $('#mission-result'); out.textContent = 'Analysing terrain…';
  try {
    if (action === 'route' || action === 'shelters') {
      if (S.floodSource === 'point' && S.floodSeed == null) {
        out.textContent = 'Click the terrain to place the point flood source before running this analysis.';
        return;
      }
      if (!S.floodActive) { S.floodActive = true; updateAnalysisTools(); }
    }
    const r = await missionRequest(action, point);
    if (action === 'route') {
      if (r.status === 'route_found' && r.route_uv?.length) {
        drawMissionPaths([r.route_uv], 0x53e1af);
        out.innerHTML = `<b>Dry route found</b> · ${fmt(r.length_m, 0)} m · climbs ${fmt(r.ascent_m, 1)} m. <span class="note">${escapeHtml(r.warning || 'Screening route only; verify access and hazards on the ground.')}</span>`;
      } else if (r.status === 'already_on_high_ground') {
        clearMissionOverlay();
        if (point) placeMarker(point.x, point.z);
        out.innerHTML = `<b>Already on high ground</b> · selected cell meets ${fmt(r.clearance_m, 1)} m clearance. <span class="note">This is terrain screening, not a verified refuge.</span>`;
      } else {
        const shelters = await missionRequest('shelters', point);
        showShelterMarkers(shelters.candidates || []);
        out.innerHTML = `<b>${escapeHtml(r.message || 'No dry route found on this grid')}</b> · ${shelters.candidate_count || 0} vertical-shelter candidates highlighted. <span class="note">Inspect structural safety, access and services before use.</span>`;
      }
    } else if (action === 'shelters') {
      showShelterMarkers(r.candidates || []);
      out.innerHTML = `<b>${r.candidate_count || 0} refuge candidates</b> · green roof markers. <span class="note">${escapeHtml(r.warning || 'Model-based screening only.')}</span>`;
    } else if (action === 'runout') {
      drawMissionPaths((r.paths || []).map((p) => p.path_uv), 0xff7867);
      out.innerHTML = r.status === 'no_high_sources'
        ? `<b>No high-susceptibility source cells</b> · none exceeded the ${fmt(r.assumptions?.susceptibility_threshold || 0.7, 2)} screening threshold. No downhill trace is inferred.`
        : `<b>${r.sources_traced || 0} downhill traces</b> · ${r.buildings_in_path_count || 0} buildings intersect or lie nearby. <span class="note">${escapeHtml(r.warning || 'Screening only.')}</span>`;
    } else if (action === 'relay') {
      const coverage = new Float32Array(S.gw * S.gh);
      for (const [row, start, end] of r.visible_rle || []) {
        const vr = Math.round(row / Math.max(1, r.grid_h - 1) * (S.gh - 1));
        const c0 = Math.ceil(start / Math.max(1, r.grid_w - 1) * (S.gw - 1));
        const c1 = Math.floor((end - 1) / Math.max(1, r.grid_w - 1) * (S.gw - 1));
        for (let c = c0; c <= c1; c++) if (vr >= 0 && vr < S.gh && c >= 0 && c < S.gw) coverage[vr * S.gw + c] = 1;
      }
      S.viewshed = coverage;
      if (point) placeMarker(point.x, point.z);
      setMode('viewshed');
      $('#probe-info').innerHTML = `<b>Relay observer</b><span>${fmt(r.observer_agl_m, 0)} m above surface</span><b>Visible area</b><span>${fmt(r.visible_area_m2 / 1e4, 2)} ha</span><span class="note" style="grid-column:1/-1">Yellow shows the API line-of-sight mask sampled to viewer resolution.</span>`;
      out.innerHTML = `<b>Relay line of sight</b> · ${fmt((r.visible_fraction || 0) * 100, 1)}% of analysed cells visible. <span class="note">${escapeHtml(r.warning || 'Line of sight only; radio propagation is not modelled.')}</span>`;
    }
  } catch (e) { out.textContent = `Analysis unavailable: ${e.message}`; }
}
for (const [id, action] of [['route-tool', 'route'], ['shelter-tool', 'shelters'], ['runout-tool', 'runout'], ['relay-tool', 'relay']]) {
  $('#' + id).onclick = () => {
    if (!S.id) return;
    if ((action === 'route' || action === 'shelters') && S.floodSource === 'point' && S.floodSeed == null) {
      $('#mission-result').textContent = 'Click the terrain to place the point flood source first.';
      S.missionAction = null;
      return;
    }
    if (action === 'shelters' || action === 'runout') { runMission(action); return; }
    S.missionAction = action;
    $$('.mission-actions button').forEach((b) => b.classList.toggle('active', b.id === id));
    $('#mission-result').textContent = action === 'route' ? 'Click a start point in the 3D terrain.' : 'Click a tower or drone location in the 3D terrain.';
  };
}


// ------------------------------------------------------------------ v3: mesh detail, GCP pins, gallery, presentation
$('#mesh-detail').onchange = () => { if (S.id) reloadViewer(); };

S.gcpPins = []; S.gcpMode = false;
function setGcpMode(on) {
  S.gcpMode = on; $('#gcp-pin').setAttribute('aria-pressed', String(on));
  $('#gcp-pin').textContent = on ? 'Pinning… (click terrain)' : 'Pin points';
  if (on) { S.missionAction = null; toast('Click the terrain to drop a ground-control pin, then type its known height.', 'info', 4000); }
}
$('#gcp-pin').onclick = () => setGcpMode(!S.gcpMode);
function addGcpPin(point) {
  const u = point.x / S.W + 0.5, v = point.z / S.H + 0.5;
  const surface = reportedHeight(sampleGrid(S.h, point.x, point.z));
  S.gcpPins.push({ x: point.x, z: point.z, u, v, surface, known: S.meta?.units === 'metre' ? +surface.toFixed(2) : NaN });
  drawGcpPins(); renderGcpTable(); fitGcp();
}
function drawGcpPins() {
  if (S.gcpGroup) { scene.remove(S.gcpGroup); S.gcpGroup.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); }); }
  S.gcpGroup = new THREE.Group();
  const s = S.extent / 220;
  S.gcpPins.forEach((p) => {
    const y = terrainY(p.x, p.z);
    const head = new THREE.Mesh(new THREE.SphereGeometry(s * 0.9, 16, 12), new THREE.MeshBasicMaterial({ color: 0x22d3ee }));
    head.position.set(p.x, y + s * 4, p.z);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(s * 0.12, s * 0.12, s * 4, 6), new THREE.MeshBasicMaterial({ color: 0x22d3ee }));
    stem.position.set(p.x, y + s * 2, p.z);
    S.gcpGroup.add(head, stem);
  });
  scene.add(S.gcpGroup); requestRender();
}
function renderGcpTable(residuals = []) {
  const tb = $('#gcp-table tbody'); tb.innerHTML = '';
  S.gcpPins.forEach((p, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${i + 1}</td><td>${fmt(p.surface, S.meta?.units === 'metre' ? 1 : 3)}</td><td><input type="number" step="0.1" value="${Number.isFinite(p.known) ? p.known : ''}" aria-label="Known height of pin ${i + 1}"></td><td>${residuals[i] !== undefined ? fmt(residuals[i], 2) : '–'}</td><td><button class="x" title="Remove pin">✕</button></td>`;
    tr.querySelector('input').onchange = (e) => { p.known = parseFloat(e.target.value); fitGcp(); };
    tr.querySelector('button').onclick = () => { S.gcpPins.splice(i, 1); drawGcpPins(); renderGcpTable(); fitGcp(); };
    tb.appendChild(tr);
  });
}
let gcpTimer = null;
function fitGcp() {
  clearTimeout(gcpTimer);
  gcpTimer = setTimeout(async () => {
    const pts = S.gcpPins.filter((p) => Number.isFinite(p.known)).map((p) => ({ u: p.u, v: p.v, height_m: p.known }));
    const out = $('#gcp-stats');
    $('#gcp-apply').disabled = true;
    if (pts.length < 2) { out.textContent = pts.length ? 'Add at least one more pin with a known height.' : ''; return; }
    try {
      const r = await apiFetch(`api/scenes/${S.id}/gcp`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ points: pts }) });
      const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
      out.classList.remove('muted');
      out.innerHTML = `<b>Fit</b><span>height = ${fmt(j.a, 3)} × surface + ${fmt(j.b, 2)}</span><b>R²</b><span>${fmt(j.r2, 3)}</span>
        <b>RMSE</b><span>${fmt(j.rmse_m, 2)} m (n=${j.n})</span><b>Leave-one-out</b><span>${j.loo_rmse_m != null ? fmt(j.loo_rmse_m, 2) + ' m' : 'needs 3+ pins'}</span>
        ${j.warning ? `<span class="note" style="grid-column:1/-1;color:#f5b454">${escapeHtml(j.warning)}</span>` : ''}`;
      const withKnown = S.gcpPins.filter((p) => Number.isFinite(p.known));
      const res = []; let k = 0; S.gcpPins.forEach((p, i) => { if (Number.isFinite(p.known)) res[i] = j.residuals_m[k++]; });
      renderGcpTable(res);
      $('#gcp-apply').disabled = !(j.a > 0);
    } catch (err) { out.textContent = String(err.message || err); }
  }, 250);
}
$('#gcp-apply').onclick = async () => {
  const pts = S.gcpPins.filter((p) => Number.isFinite(p.known)).map((p) => ({ u: p.u, v: p.v, height_m: p.known }));
  busy(true, 'Applying ground-control fit…');
  try {
    const r = await apiFetch(`api/scenes/${S.id}/gcp`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ points: pts, apply: true }) });
    const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
    toast(`Calibrated with ${j.n} pins · RMSE ${fmt(j.rmse_m, 2)} m${j.relative_input ? ' · scene is now metric' : ''}`, 'ok');
    setGcpMode(false); S.gcpPins = []; renderGcpTable(); await reloadViewer();
  } catch (err) { toast('GCP apply failed: ' + (err.message || err), 'error', 8000); }
  finally { busy(false); }
};
$('#gcp-reset').onclick = async () => {
  const r = await apiFetch(`api/scenes/${S.id}/gcp`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reset: true }) });
  const j = await r.json();
  S.gcpPins = []; renderGcpTable(); $('#gcp-stats').textContent = '';
  if (S.gcpGroup) { scene.remove(S.gcpGroup); S.gcpGroup = null; }
  if (j.reset) { toast('Ground-control calibration undone.', 'ok'); await reloadViewer(); }
  else toast(j.note || 'Nothing to undo.');
};

// first-run demo gallery
async function openGallery() {
  const trigger = document.activeElement;
  if ($('#gallery').classList.contains('hidden')) {
    S.galleryPrevNav = S.nav;
  }
  dialogManager.open('gallery', trigger); $('#app').classList.add('gallery-open');
  $('#gallery-grid').textContent = 'Loading scenes…';
  let list;
  try { list = await fetchSceneList(false); }
  catch (error) { if (dialogManager.isOpen('gallery')) $('#gallery-grid').textContent = 'Scenes could not be loaded. Close this gallery and try again.'; throw error; }
  if (!dialogManager.isOpen('gallery')) return;
  const grid = $('#gallery-grid'); grid.innerHTML = '';
  const featured = ['dc-glover-park','dc-capitol-hill','quesenbank-south-calibrated-v2','gamus-nyc','dc-glover-post','forest-north'];
  const picks = featured.map((id) => list.find((sc) => sc.id === id)).filter(Boolean);
  const presentation = {
    'dc-glover-park': ['Washington · Glover Park', 'URBAN · REFERENCE'],
    'dc-capitol-hill': ['Washington · Capitol Hill', 'DENSE URBAN'],
    'quesenbank-south-calibrated-v2': ['Quesenbank · Forest canopy', 'FOREST · METRIC'],
    'gamus-nyc': ['New York · Relative height', 'PLAIN PNG'],
    'dc-glover-post': ['Glover Park · Simulated impact', 'SIMULATED EVENT'],
    'forest-north': ['Quesenbank · North woodland', 'FOREST · REFERENCE'],
  };
  for (const sc of (picks.length ? picks : list.slice(0, 6))) {
    const card = document.createElement('button'); card.className = 'gallery-card'; card.type = 'button';
    const what = sc.units === 'metre'
      ? `Metric 3D surface · ${sc.method || 'calibrated'}${sc.has_reference ? ' · reference attached' : ''}`
      : 'Relative 3D surface from a plain image';
    const [title, tag] = presentation[sc.id] || [sceneDisplayName(sc.id, null, sc.name), sc.units === 'metre' ? 'METRIC DSM' : 'RELATIVE DSM'];
    card.innerHTML = `<div class="gallery-image"><img alt="" loading="lazy"><span class="gallery-tag"></span></div><b></b><small></small>`;
    card.querySelector('img').src = `jobs/${encodeURIComponent(sc.id)}/viewer/texture.jpg`;
    card.querySelector('.gallery-tag').textContent = tag;
    card.querySelector('b').textContent = title; card.querySelector('small').textContent = what;
    card.onclick = () => { closeGallery(); loadScene(sc.id); };
    grid.appendChild(card);
  }
  dialogManager.sync();
}
function closeGallery() {
  $('#gallery').classList.add('hidden'); $('#app').classList.remove('gallery-open');
  dialogManager?.sync();
  if (S.mesh && S.galleryPrevNav) setNav(S.galleryPrevNav);
  S.galleryPrevNav = null;
}
window.openGallery = openGallery;
$('#gallery-btn').onclick = openGallery;
$('#gallery-close').onclick = closeGallery;
$('#gallery-import').onclick = () => { closeGallery(); showTab('upload'); };

// presentation mode
window.togglePresentation = (on = !$('#app').classList.contains('presentation')) => {
  S.presentation = on;
  $('#app').classList.toggle('presentation', on);
  const t = $('#pres-title');
  if (on && S.meta) {
    if (!$('#comparison').classList.contains('hidden')) setComparison(false);
    if (!$('#map-panel').classList.contains('hidden')) $('#map-toggle').click();
    closeExportMenu();
    closeCommandPalette();$('#help').classList.add('hidden');$('#compare-popover').classList.add('hidden');
    t.innerHTML = `${escapeHtml($('#hero-title').textContent || S.meta.input || 'Scene')}<small>${S.meta.calibration?.method==='input-dem'?'Input DEM (not estimated)':S.meta.units === 'metre' ? 'Metric 3D surface model' : 'Relative heights'} · ${escapeHtml(S.meta.calibration?.evidence_level || 'unverified')} evidence · DepthWizard</small>`;
    let strip=$('#pres-metrics');if(!strip){strip=document.createElement('div');strip.id='pres-metrics';$('#stage').append(strip);}
    const m=S.meta.metrics?.absolute;strip.innerHTML=m?`<span>${fmt(m.rmse)} m<small>RMSE</small></span><span>${fmt(m.mae)} m<small>MAE</small></span><span>${fmt(m.r,3)}<small>PEARSON R</small></span>`:`<span>${escapeHtml(S.meta.units==='metre'?'No independent reference':'Relative heights')}<small>ACCURACY NOT SCORED</small></span>`;
    if(S.swipeActive)setSwipe(false);$('#welcome-card').classList.add('hidden');$('#evidence-popover').classList.add('hidden');
    t.classList.remove('hidden'); setNav(matchMedia('(prefers-reduced-motion: reduce)').matches?'orbit':'tour');
  } else { t.classList.add('hidden'); setNav('orbit'); }
  requestAnimationFrame(resize); requestRender();
};
$('#present-btn').onclick = () => window.togglePresentation();
addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.presentation) window.togglePresentation(false); });

// ------------------------------------------------------------------ Terrain Mission Control UI
// Reparent the existing controls after their handlers are bound. Each control
// keeps its original ID and event listener; rendering and analysis stay above.
const WORKSPACES = {
  explore: { title: 'Explore the surface', subtitle: 'Layers, light and camera', groups: ['surface-group'] },
  measure: { title: 'Measure', subtitle: 'Probe, profile and planning limits', groups: ['measure-group', 'height-limit-group', 'volume-group'] },
  disaster: { title: 'Flood & response', subtitle: 'Screen exposure and plan a response', groups: ['flood-group', 'disaster-group', 'change-group'] },
  buildings: { title: 'Buildings', subtitle: 'Inspect fitted structures', groups: ['building-inspector-group'] },
  calibrate: { title: 'Calibrate height', subtitle: 'Trace every metric-scale cue', groups: ['gcp-group', 'anchor-panel-group', 'automatic-anchor-group'] },
  validate: { title: 'Validate', subtitle: 'Compare estimate, DEM baseline and reference', groups: [] },
};
function setWorkspace(mode, openDrawer = true) {
  if (mode === 'buildings' && !S.buildingToolsAvailable) mode = 'explore';
  if (!WORKSPACES[mode]) return;
  if (openDrawer) $('#app').classList.remove('drawer-collapsed');
  S.workspace = mode; $('#app').dataset.workspace = mode;
  $$('#mode-rail [data-workspace]').forEach((b) => {
    b.classList.toggle('active', b.dataset.workspace === mode);
    b.setAttribute('aria-current', b.dataset.workspace === mode ? 'page' : 'false');
  });
  $('#drawer-title').textContent = WORKSPACES[mode].title;
  $('#drawer-subtitle').textContent = WORKSPACES[mode].subtitle;
  showTab(mode === 'validate' ? 'validate' : 'analyse');
  const active = new Set(WORKSPACES[mode].groups);
  $$('#tab-analyse > .group').forEach((group) => group.classList.toggle('mode-hidden', !active.has(group.id)));
  if (mode === 'buildings' && S.mesh && S.buildings?.count) setViewGeometry('city');
  if (mode === 'validate' && S.ref) setMode('error');
  updateMissionHud();
}
function setAnimatedStat(el, value, suffix = '', precision = null) {
  if (!el || !Number.isFinite(value)) { if (el) {el.textContent = '—';delete el.dataset.value;} return; }
  const target = Number(value), previous = Number(el.dataset.value);
  if (el.dataset.value && Math.abs(previous - target) < .0001 && el.dataset.suffix === suffix) return;
  el.dataset.value = String(target); el.dataset.suffix = suffix;
  const decimal = precision ?? (Math.abs(target) < 100 && !Number.isInteger(target) ? 1 : 0);
  const display = (n) => `${n.toLocaleString(undefined, { maximumFractionDigits: decimal, minimumFractionDigits: decimal })}${suffix}`;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !Number.isFinite(previous)) { el.textContent = display(target); return; }
  const start = performance.now(), initial = previous;
  const tick = (now) => {
    if (el.dataset.value !== String(target)) return;
    const t = Math.max(0, Math.min(1, (now - start) / 600)), eased = 1 - Math.pow(1 - t, 3);
    el.textContent = display(initial + (target - initial) * eased);
    if (t < 1) requestAnimationFrame(tick);
  }; requestAnimationFrame(tick);
}
function updateMissionHud() {
  if (!S.meta) return;
  const mode = S.workspace || 'explore', list = S.buildings?.buildings || [];
  const friendly = sceneDisplayName();
  $('#hero-title').textContent = friendly.replace(/\.(tiff?|png|jpe?g)$/i, '');
  $('#top-scene-name').textContent = friendly.replace(/\.(tiff?|png|jpe?g)$/i, '');
  const tallest = list.reduce((v, b) => Math.max(v, Number(b.height_m) || 0), 0);
  const metrics = S.meta.metrics?.absolute || S.meta.metrics?.affine_aligned;
  const pinCount = S.gcpPins?.length || 0;
  let values;
  if (mode === 'disaster') values = [['Flooded area', S.meta.units==='metre'?S.floodAreaHa || 0:undefined, ' ha'], ['Buildings hit', S.meta.units==='metre'?S.floodBuildings || 0:undefined, ''], ['People ≈', S.meta.units==='metre'?S.floodPeople || 0:undefined, '']];
  else if (mode === 'calibrate') values = [['RMSE vs reference', metrics?.rmse, metrics ? ' m' : ''], ['r²', metrics?.r !== undefined ? metrics.r ** 2 : undefined, ''], ['Pins', pinCount, '']];
  else if (mode === 'validate') values = [['RMSE', metrics?.rmse, metrics ? ' m' : ''], ['MAE', metrics?.mae, metrics ? ' m' : ''], ['Pearson r', metrics?.r, '']];
  else if (mode === 'buildings') values = [['Candidates', list.length, ''], ['Tallest', list.length ? tallest : undefined, ` ${S.units}`], ['Fitted roofs', list.filter((b) => b.roof_fit).length, '']];
  else values = [['RMSE',S.meta.metrics?.absolute?.rmse,S.meta.units==='metre'?' m':''],['Buildings',list.length,''],['Tallest',list.length?tallest:undefined,` ${S.units}`],[S.meta.georeferenced?'Scene area':'Image pixels', S.meta.georeferenced?S.W*S.H/1e6:S.meta.src_w*S.meta.src_h, S.meta.georeferenced?' km²':' px']];
  const signature = values.map(([label]) => label).join('|');
  if ($('#hero-stats').dataset.signature !== signature) {
    $('#hero-stats').dataset.signature = signature;
    $('#hero-stats').replaceChildren(...values.map(([label]) => {
      const box = document.createElement('div'); box.className = 'hero-stat';
      const number = document.createElement('b'), caption = document.createElement('span'); caption.textContent = label;
      box.append(number, caption); return box;
    }));
  }
  values.forEach(([label, val, suffix], i) => setAnimatedStat($('#hero-stats').children[i]?.querySelector('b'), val, suffix, ['Pearson r','r²'].includes(label)?3:null));
  const level = S.meta.calibration?.evidence_level || (S.meta.units === 'relative' ? 'relative' : 'unverified');
  $('#hero-caption').textContent = `${S.meta.calibration?.method==='input-dem'?'Input DEM (not estimated)':S.meta.units === 'metre' ? 'Metric DSM' : 'Relative surface'} · ${level} evidence · ${S.meta.crs || 'local coordinates'}`;
  if (S.meta.semantic_segmentation?.status === 'experimental') $('#hero-caption').textContent += ' · Experimental semantic masks';
  $$('#mode-rail [data-workspace="validate"]').forEach((b) => b.disabled = false);
}
function renderBuildingList() {
  let list = $('#building-list');
  if (!list) { list = document.createElement('div'); list.id = 'building-list'; $('#building-inspector-group').append(list); }
  const buildings = [...(S.buildings?.buildings || [])].sort((a, b) => (b.height_m || 0) - (a.height_m || 0));
  list.innerHTML = '<h3>By estimated height</h3>';
  if (!buildings.length) { list.append('No building candidates in this scene.'); return; }
  for (const b of buildings.slice(0, 25)) {
    const row = document.createElement('button'); row.type = 'button'; row.className = 'building-row';
    row.innerHTML = `<span>Building #${b.id}</span><strong>${fmt(b.height_m, 1)} ${S.units}</strong>`;
    row.onclick = () => {
      if (S.viewGeometry !== 'city') setViewGeometry('city');
      const mesh = S.buildingGroup?.children.find((item) => item.userData?.building?.id === b.id);
      if (mesh) selectBuilding(mesh);
    };
    list.append(row);
  }
}
function renderLayerPreviews() {
  if (!S.meta || !S.h) return;
  for (const button of $$('#layer-dock button[data-mode]')) {
    const mode = button.dataset.mode;
    if(!button.querySelector('.layer-label')){const text=button.textContent.trim();button.childNodes.forEach(n=>{if(n.nodeType===Node.TEXT_NODE)n.remove();});const label=document.createElement('span');label.className='layer-label';label.textContent=text;button.append(label);button.setAttribute('aria-label',text);}
    const unavailable = (mode === 'confidence' && !S.confidence) || (mode === 'error' && !S.ref) || (mode === 'landslide' && !S.susc) ||
      (mode === 'change' && !S.change) || (mode === 'ndsm' && !S.dtm) ||
      (mode === 'hazard' && S.meta.units !== 'metre');
    button.classList.toggle('layer-unavailable', unavailable);
    if (unavailable) continue;
    let thumb = button.querySelector('canvas.layer-thumb');
    if (!thumb) { thumb = document.createElement('canvas'); thumb.className = 'layer-thumb'; thumb.width = 64; thumb.height = 64; button.prepend(thumb); }
    const ctx = thumb.getContext('2d');
    if (mode === 'optical' && S.texImg) { ctx.drawImage(S.texImg, 0, 0, thumb.width, thumb.height); continue; }
    const frame = ctx.createImageData(thumb.width, thumb.height), lo = pct(S.h, .02), hi = pct(S.h, .98);
    for (let y = 0; y < thumb.height; y++) for (let x = 0; x < thumb.width; x++) {
      const r = Math.min(S.gh - 1, Math.floor(y / thumb.height * S.gh));
      const c = Math.min(S.gw - 1, Math.floor(x / thumb.width * S.gw));
      const i = r * S.gw + c, h = S.h[i], v = (h - lo) / (hi - lo || 1);
      let color;
      if (mode === 'topo' || mode === 'topogray') {
        color = ramp('topo', v);
        if (mode === 'topogray') { const g = .3 * color[0] + .59 * color[1] + .11 * color[2]; color = [g, g, g]; }
      } else if (mode === 'slope' || mode === 'hazard') {
        const slope = slopeAt(r, c).slope;
        color = mode === 'hazard' ? HAZARD[slope < 30 ? 0 : slope < 45 ? 1 : 2].color : ramp('slope', slope / 45);
      } else if (mode === 'confidence') color = ramp(mode, S.confidence?.[i] ?? .8);
      else if (mode === 'ndsm') color = ramp(mode, Math.max(0, h - S.dtm[i]) / Math.max(5, hi - lo));
      else if (mode === 'landslide') color = ramp(mode, S.susc[i]);
      else if (mode === 'change') color = ramp(mode, .5 + S.change[i] / 25);
      else if (mode === 'error') color = ramp(mode, .5 + (h - S.ref[i]) / 20);
      else color = ramp('height', v);
      const k = (y * thumb.width + x) * 4;
      frame.data[k] = Math.round(255 * color[0]); frame.data[k+1] = Math.round(255 * color[1]); frame.data[k+2] = Math.round(255 * color[2]); frame.data[k+3] = 255;
    }
    ctx.putImageData(frame, 0, 0);
  }
}
function refreshImportPreview() { return missionUi?.inspectInput(); }
function openCommandPalette() {
  dialogManager.open('command-palette'); $('#command-input').value = '';
  renderCommandResults(''); $('#command-input').focus();
}
function closeCommandPalette() { $('#command-palette').classList.add('hidden'); dialogManager?.sync(); }
function renderCommandResults(query) {
  const q = query.trim().toLowerCase(), choices = [];
  for (const [id, label] of [['explore','Explore terrain'],['measure','Measure heights and slopes'],['disaster','Flood and response'],['buildings','Buildings'],['calibrate','Calibrate scale'],['validate','Validate against reference']])
    choices.push({ label, tag:'Workspace', run:()=>setWorkspace(id) });
  for (const b of $$('#layer-dock button[data-mode]:not(.layer-unavailable)')) choices.push({ label:`Show ${b.textContent.trim()} layer`, tag:'Layer', run:()=>b.click() });
  choices.push({label:'Import imagery',tag:'Project',run:()=>showTab('upload')},{label:'Open scene gallery',tag:'Project',run:openGallery},
    {label:'Export everything ZIP',tag:'Export',run:()=>$('#export-menu [data-export="all"]').click()},
    {label:'Export GLB mesh',tag:'Export',run:()=>$('#export-menu [data-export="glb"]').click()},
    {label:'Export DSM GeoTIFF',tag:'Export',run:()=>$('#export-menu [data-export="dsm"]').click()},
    {label:'Presentation mode',tag:'View',run:()=>window.togglePresentation()},
    {label:'Pin ground control points',tag:'Calibration',run:()=>{setWorkspace('calibrate');$('#gcp-pin').click();}},
    {label:'DEM versus DSM swipe',tag:'Compare',run:()=>$('#swipe-toggle').click()},
    {label:'Pretrained versus GAMUS model',tag:'Compare',run:()=>$('#model-swipe-toggle').click()},
    {label:'Basemap',tag:'Compare',run:()=>$('#map-toggle').click()});
  for (const row of $$('#scene-list .item')) {
    const name = row.querySelector('.n')?.textContent || row.dataset.id;
    choices.push({label:`Open ${name}`,tag:'Scene',run:()=>row.querySelector('.scene-select')?.click()});
  }
  const matches = choices.filter((c) => !q || q.split(/\s+/).every((word) => c.label.toLowerCase().includes(word))).slice(0, 12);
  $('#command-results').replaceChildren(...matches.map((choice) => {
    const b = document.createElement('button'); b.type='button'; b.role='option';
    b.innerHTML = `<span></span><small></small>`; b.querySelector('span').textContent = choice.label; b.querySelector('small').textContent=choice.tag;
    b.onclick=()=>{closeCommandPalette();choice.run();}; return b;
  }));
  if (!matches.length) $('#command-results').textContent = 'No matching actions';
}
function initMissionLayout() {
  const view = $('#view-pill'), compare = $('#compare-actions');
  for (const id of ['nav-mode','topdown','reset','view-mode','fullscreen','present-btn','vr-toggle']) view.append($('#'+id));
  const compareTrigger = document.createElement('button'); compareTrigger.id='compare-trigger'; compareTrigger.type='button'; compareTrigger.textContent='Compare'; compareTrigger.title='Choose a comparison view';
  view.append(compareTrigger);
  compareTrigger.onclick=()=>$('#compare-popover').classList.toggle('hidden');
  for (const id of ['swipe-toggle','model-swipe-toggle','compare-toggle','map-toggle']) {
    const button=$('#'+id); compare.append(button);
    button.addEventListener('click',()=>{
      $('#compare-popover').classList.add('hidden');
      $('#app').classList.add('drawer-collapsed');
      if (id !== 'map-toggle' && $('#stage').classList.contains('map-open')) $('#map-toggle').click();
      if (id !== 'compare-toggle' && !$('#comparison').classList.contains('hidden')) setComparison(false);
      if (!['swipe-toggle','model-swipe-toggle'].includes(id) && S.swipeActive) setSwipe(false);
    });
  }
  const exagControl=document.createElement('div');exagControl.id='exag-control';
  exagControl.innerHTML='<button id="exag-trigger" type="button" title="Vertical display exaggeration">Z ×</button><div id="exag-popover"></div>';
  view.append(exagControl);
  $('#exag-popover').append($('#exag').closest('label'));
  $('#exag-trigger').onclick=()=>exagControl.classList.toggle('open');
  $('#layer-dock-actions').append($('#shade-mode'));
  $('#layer-legend').append($('#legend'));
  $('#stage').append($('#layer-legend'));
  const contourSettings = document.createElement('label');
  contourSettings.id = 'topo-contours'; contourSettings.textContent = 'Contours every';
  contourSettings.title = 'Every fifth contour is drawn more strongly';
  contourSettings.append($('#contour-int'), $('#contour-unit'));
  $('#layer-legend').append(contourSettings);
  for (const mode of ['topogray','slope','curvature']) $('#layer-more-menu').append($(`#shade-mode button[data-mode="${mode}"]`));
  $('#dock-more').onclick=()=>$('#layer-more-menu').classList.toggle('open');
  $('#surface-group h3').textContent='Lighting & surface';
  $('#surface-group').insertAdjacentHTML('afterbegin','<button id="explore-compare" type="button" class="primary drawer-primary">Compare surfaces</button>');
  $('#explore-compare').onclick=()=>$('#compare-trigger').click();
  $('#surface-group label:has(#contours)').classList.add('hidden');
  $('#flood-info').before($('#flood-group > p.note'));
  $('#record-tour').textContent='Record 30 s flythrough';
  const exportGroups = [
    ['Raster & analysis', ['dsm','dtm','ndsm','uncertainty','heightmap']],
    ['3D assets', ['glb','obj','cityjson','ply']],
    ['Evidence & sharing', ['report','evidence','shot']],
  ];
  for (const [heading, keys] of exportGroups) {
    const group = document.createElement('section'); group.className = 'export-category';
    const title = document.createElement('h3'); title.textContent = heading; group.append(title);
    for (const key of keys) group.append($(`#export-menu [data-export="${key}"]`));
    if (heading === 'Evidence & sharing') group.append($('#record-tour'));
    $('#export-menu').append(group);
  }
  // The header's backdrop filter establishes a containing block for fixed
  // descendants. Keep the sheet at app level so it anchors to the viewport.
  $('#app').append($('#export-menu'));
  $('#gallery-btn').classList.add('hidden');
  $('.brand').onclick=(e)=>{e.preventDefault();openGallery();};
  $('#library-toggle').addEventListener('click',()=>$('#library-toggle').setAttribute('aria-expanded',String($('#app').classList.contains('library-open'))));
  $('#scene-badge').onclick=()=>setWorkspace('calibrate');
  $('#scene-badge').onkeydown=(e)=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setWorkspace('calibrate');}};
  $('#drawer-toggle').onclick=()=>$('#app').classList.add('drawer-collapsed');
  $('#drawer-reopen').onclick=()=>$('#app').classList.remove('drawer-collapsed');
  $('#flood-details-toggle').onclick=()=>{const on=$('#flood-info').classList.toggle('details-open');$('#flood-details-toggle').setAttribute('aria-expanded',String(on));};
  $('#change-toggle').onclick=()=>{const on=$('#change-group').classList.toggle('details-open');$('#change-toggle').setAttribute('aria-expanded',String(on));};
  $('#hover-hud').onclick=()=>{S.hudPinned=false;$('#hover-hud').classList.remove('pinned');$('#hover-hud').classList.add('hidden');};
  $('#command-open').onclick=openCommandPalette;
  $('#command-input').oninput=(e)=>renderCommandResults(e.target.value);
  $('#command-input').onkeydown=(e)=>{if(e.key==='Enter'){$('#command-results button')?.click();e.preventDefault();}};
  $$('[data-close-command]').forEach((el)=>el.onclick=closeCommandPalette);
  $('#export-sheet-close').onclick=closeExportMenu;
  $('#rail-gallery').onclick=()=>$('#gallery-btn').click();
  $('#rail-import').onclick=()=>$('#import-btn').click();
  $$('#mode-rail [data-workspace]').forEach((b)=>b.onclick=()=>setWorkspace(b.dataset.workspace));
  addEventListener('keydown',(e)=>{
    if (dialogManager?.isOpen()) return;
    if(e.key==='Escape'){
      $('#app').classList.add('drawer-collapsed');
      closeCommandPalette();closeExportMenu();$('#compare-popover').classList.add('hidden');$('#help').classList.add('hidden');
      $('#layer-more-menu').classList.remove('open');$('#app').classList.remove('library-open');
      $('#upload-modal').classList.add('hidden');
      if(!$('#gallery').classList.contains('hidden')) closeGallery();
    }
  });
    setWorkspace('explore', false);
}
initMissionLayout();
missionUi = createMissionUi({ getState: () => S, camera, orbit, requestRender, setWorkspace, loadScene, resetView, setNav, setMode, toast });
boldUi = createBoldUi({getState:()=>S,orbit,canvas,requestRender});
dialogManager = createDialogManager({
  onOpen: () => { S.keys = {}; fly.unlock(); S.cameraFlight = null; orbit.enabled = false; orbit.autoRotate = false;
    if (renderProfiler?.active) renderProfiler.cancel('Capture cancelled: a dialog opened.'); },
  onClose: () => { S.keys = {}; orbit.enabled = S.nav === 'orbit' && !dialogManager.isOpen(); requestRender(); },
  onCommand: openCommandPalette,
});
const glContext = renderer.getContext(), gpuInfo = glContext.getExtension('WEBGL_debug_renderer_info');
renderProfiler = createRenderProfiler({ getContext: () => ({ scene: S.id, view: S.viewGeometry, layer: S.mode,
  quality: S.quality, navigation: S.nav, grid: [S.gw, S.gh], viewport: [canvas.clientWidth, canvas.clientHeight],
  adaptiveLevel:frameBudget.level, terrainStreaming:terrainStream?.group.visible ? terrainStream.diagnostics() : null,
  pixelRatio: renderer.getPixelRatio(), userAgent: navigator.userAgent,
  renderer: gpuInfo ? glContext.getParameter(gpuInfo.UNMASKED_RENDERER_WEBGL) : glContext.getParameter(glContext.RENDERER),
  hardwareNote: $('#profile-hardware').value.trim(), treeCandidates: getTreeLodDiagnostics(S.treeGroup)?.instanceCount ?? 0 }),
  onStatus: message => { $('#render-profile-status').textContent = message; if (!renderProfiler?.active) $('#render-profile-start').disabled = false; },
  onComplete: report => { $('#render-profile-start').disabled = false; $('#render-profile-download').disabled = false;
    $('#render-profile-json').textContent = JSON.stringify(report, null, 2); $('#render-profile-json').classList.remove('hidden');
    $('#render-profile-status').textContent = `${fmt(report.fps, 1)} fps · P95 ${fmt(report.frameP95Ms, 1)} ms · ${report.samples} frames. Download includes renderer and quality changes.`; },
});
$('#render-profile-start').onclick = () => { if (!S.mesh) { toast('Load a scene before recording performance.'); return; }
  renderProfiler.start(); $('#render-profile-start').disabled = true; requestRender(); };
$('#render-profile-download').onclick = () => renderProfiler.download();
// Catch rejected async UI actions at their event boundary. Network errors already
// have a retry toast; synchronous exceptions remain visible for debugging.
for (const el of $$('button,a,input,select,.brand')) {
  for(const event of ['onclick','onchange','oninput']){
    const handler=el[event];if(!handler)continue;
    el[event]=function(...args){const result=handler.apply(this,args);if(result?.catch)result.catch(error=>toast(missionUi.humanError(error.message),'error',9000,()=>el.click()));return result;};
  }
}
$('#storm-run').onclick = async () => {
  if (!S.id || S.meta.units !== 'metre' || !S.dtm) { toast('Drainage screening needs a metric scene and DTM.'); return; }
  const id = S.id, button = $('#storm-run'); button.disabled = true; $('#storm-result').textContent = 'Computing runoff…';
  try {
    const r = await (await apiFetch(`api/scenes/${id}/mission`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'rainfall',rainfall_mm:+$('#rainfall-mm').value,duration_min:+$('#storm-duration').value,
        infiltration_mm_hr:+$('#storm-infiltration').value,drainage_mm_hr:+$('#storm-drainage').value})})).json();
    if (S.id !== id) return;
    const maxDepth = Math.max(...r.depth_m.flat());
    $('#storm-result').textContent = `Rain ${fmt(r.rain_volume_m3,0)} m³ · losses ${fmt(r.loss_volume_m3,0)} m³ · stored ${fmt(r.stored_volume_m3,0)} m³ · maximum depth ${fmt(maxDepth,2)} m · mass balance ${fmt(r.mass_balance_error_m3,5)} m³. ${r.simulation_grid.join(' × ')} sampled cells. No upstream inflow/outfall; no real-event validation.`;
    const download = document.createElement('button'); download.type='button'; download.textContent='Download runoff evidence';
    download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`${id}-runoff.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    $('#storm-result').append(document.createElement('br'),download);
    const display=document.createElement('button');display.type='button';display.textContent='Show runoff depth in 3D';
    display.onclick=()=>{
      if(S.id!==id)return;
      if(S.missionOverlay?.userData.runoff){clearMissionOverlay();display.textContent='Show runoff depth in 3D';}
      else {clearMissionOverlay();S.missionOverlay=createRunoffOverlay(r,{W:S.W,H:S.H,worldY});
        S.missionOverlay.userData.runoff=true;S.missionOverlay.userData.exag=S.exag;scene.add(S.missionOverlay);
        display.textContent='Hide runoff depth';requestRender();}
    };
    $('#storm-result').append(display);
  } catch (error) { $('#storm-result').textContent=missionUi.humanError(error.message); }
  finally { button.disabled = false; }
};
savedViews = createSavedViews({getState:()=>S,camera,orbit,toast,restore:view=>{
  S.cameraFlight = null;
  $('#exag').value = view.exag; $('#exag').oninput({target:$('#exag')});
  setViewGeometry(view.geometry); setMode(view.layer); setNav('orbit');
  camera.up.fromArray(Array.isArray(view.up) && view.up.length===3 && view.up.every(Number.isFinite) ? view.up : [0,1,0]);
  camera.position.fromArray(view.camera); orbit.target.fromArray(view.target); orbit.update(); requestRender();
}});
$('#surface-group').append(savedViews.section);
const undoButton = document.createElement('button'); undoButton.type='button'; undoButton.textContent='Undo last calibration';
undoButton.onclick=async()=>{ if(!S.id)return; try { await apiFetch(`api/scenes/${S.id}/calibration/undo`,{method:'POST'}); await reloadViewer(); toast('Previous calibration restored.'); } catch(error) { toast(missionUi.humanError(error.message),'error'); } };
$('#gcp-group').append(undoButton);
$('#route-mask-upload').onclick=async()=>{
  const file=$('#route-mask-file').files[0]; if(!file || !S.id){toast('Choose a route constraint GeoTIFF first.');return;}
  const id=S.id,button=$('#route-mask-upload'),body=new FormData(); body.append('mask',file);button.disabled=true;
  try { const r=await(await apiFetch(`api/scenes/${id}/route-input/${$('#route-mask-kind').value}`,{method:'POST',body})).json();
    if(S.id===id) $('#route-mask-status').textContent=`Attached ${r.kind}: ${r.known_cells} known cells, ${r.unknown_cells} unknown. Access still needs field verification.`;
  } catch(error){$('#route-mask-status').textContent=missionUi.humanError(error.message);}
  finally{button.disabled=false;}
};
// shareable links: #scene-id opens that scene (also when the hash changes)
addEventListener('hashchange', () => { const id = decodeURIComponent(location.hash.slice(1)); if (id && id !== S.id && id !== loadingSceneId) loadScene(id); });
