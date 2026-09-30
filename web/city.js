// DepthWizard – LoD1 city model, building inspector, connected flood, 3D swipe.
// Pure functions that build Three.js objects from the viewer's grids; app.js owns state.
import * as THREE from 'three';

// ---------------------------------------------------------------- LoD1 buildings
// labels: Uint16 grid (bw × bh), 0 = none; table: buildings.json entries.
// toWorld(u, v) -> [x, z] for normalised image coords; worldY(h) maps metres to scene Y.
export function buildCityMesh({ labels, bw, bh, table, W, H, worldY, texture, heightColor }) {
  const byId = new Map(table.map((b) => [b.id, b]));
  const pos = [], uv = [], col = [], bid = [];
  const ranges = new Map();
  const X = (c) => (c / bw - 0.5) * W, Z = (r) => (r / bh - 0.5) * H;
  const U = (c) => c / bw, V = (r) => 1 - r / bh;
  const push = (id, verts, tint) => {
    if (!ranges.has(id)) ranges.set(id, []);
    const start = pos.length / 3;
    for (const [x, y, z, u, v] of verts) { pos.push(x, y, z); uv.push(u, v); col.push(...tint); bid.push(id); }
    ranges.get(id).push([start, verts.length]);
  };
  const roofY = (b) => worldY(b.base + b.height);
  const footY = (b) => worldY(b.base - Math.max(1.5, b.height * 0.15));
  // roofs: merge horizontal runs of the same label
  for (let r = 0; r < bh; r++) {
    let c = 0;
    while (c < bw) {
      const id = labels[r * bw + c];
      if (!id || !byId.has(id)) { c++; continue; }
      let e = c + 1;
      while (e < bw && labels[r * bw + e] === id) e++;
      const b = byId.get(id), y = roofY(b), t = heightColor ? heightColor(b) : [1, 1, 1];
      const a = [X(c), y, Z(r), U(c), V(r)], bb = [X(e), y, Z(r), U(e), V(r)];
      const cc = [X(e), y, Z(r + 1), U(e), V(r + 1)], d = [X(c), y, Z(r + 1), U(c), V(r + 1)];
      push(id, [a, d, bb, bb, d, cc], t);
      c = e;
    }
  }
  // walls: boundaries between a building cell and anything else, merged along the edge
  const wall = (id, x0, z0, x1, z1, u0, v0, u1, v1) => {
    const b = byId.get(id), top = roofY(b), bot = footY(b);
    const t = heightColor ? heightColor(b).map((v) => v * 0.72) : [0.62, 0.62, 0.64];
    push(id, [[x0, top, z0, u0, v0], [x0, bot, z0, u0, v0], [x1, top, z1, u1, v1],
      [x1, top, z1, u1, v1], [x0, bot, z0, u0, v0], [x1, bot, z1, u1, v1]], t);
  };
  // horizontal edges (between row r-1 and r)
  for (let r = 0; r <= bh; r++) {
    for (const side of [0, 1]) {            // 0: building below edge (row r), 1: building above (row r-1)
      let c = 0;
      while (c < bw) {
        const inside = side === 0 ? (r < bh ? labels[r * bw + c] : 0) : (r > 0 ? labels[(r - 1) * bw + c] : 0);
        const other = side === 0 ? (r > 0 ? labels[(r - 1) * bw + c] : 0) : (r < bh ? labels[r * bw + c] : 0);
        if (!inside || inside === other || !byId.has(inside)) { c++; continue; }
        let e = c + 1;
        while (e < bw) {
          const i2 = side === 0 ? (r < bh ? labels[r * bw + e] : 0) : (r > 0 ? labels[(r - 1) * bw + e] : 0);
          const o2 = side === 0 ? (r > 0 ? labels[(r - 1) * bw + e] : 0) : (r < bh ? labels[r * bw + e] : 0);
          if (i2 !== inside || o2 === inside) break;
          e++;
        }
        const vv = V(side === 0 ? r + 0.5 : r - 0.5);
        wall(inside, X(c), Z(r), X(e), Z(r), U(c), vv, U(e), vv);
        c = e;
      }
    }
  }
  // vertical edges (between column c-1 and c)
  for (let c = 0; c <= bw; c++) {
    for (const side of [0, 1]) {
      let r = 0;
      while (r < bh) {
        const inside = side === 0 ? (c < bw ? labels[r * bw + c] : 0) : (c > 0 ? labels[r * bw + c - 1] : 0);
        const other = side === 0 ? (c > 0 ? labels[r * bw + c - 1] : 0) : (c < bw ? labels[r * bw + c] : 0);
        if (!inside || inside === other || !byId.has(inside)) { r++; continue; }
        let e = r + 1;
        while (e < bh) {
          const i2 = side === 0 ? (c < bw ? labels[e * bw + c] : 0) : (c > 0 ? labels[e * bw + c - 1] : 0);
          const o2 = side === 0 ? (c > 0 ? labels[e * bw + c - 1] : 0) : (c < bw ? labels[e * bw + c] : 0);
          if (i2 !== inside || o2 === inside) break;
          e++;
        }
        const uu = U(side === 0 ? c + 0.5 : c - 0.5);
        wall(inside, X(c), Z(r), X(c), Z(e), uu, V(r), uu, V(e));
        r = e;
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  geo.setAttribute('bid', new THREE.Float32BufferAttribute(bid, 1));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({ map: heightColor ? null : texture, vertexColors: true,
    roughness: 0.85, metalness: 0.02, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true; mesh.receiveShadow = true;
  mesh.userData = { ranges, baseColors: Float32Array.from(col), byId };
  return mesh;
}

export function highlightBuilding(mesh, id) {
  const { ranges, baseColors } = mesh.userData;
  const colors = mesh.geometry.attributes.color;
  colors.array.set(baseColors);
  if (id && ranges.has(id)) {
    for (const [start, count] of ranges.get(id)) {
      for (let i = start; i < start + count; i++) { colors.array[i * 3] = 1.0; colors.array[i * 3 + 1] = 0.62; colors.array[i * 3 + 2] = 0.1; }
    }
  }
  colors.needsUpdate = true;
}

// ---------------------------------------------------------------- connected flood
// ground: Float32 grid (gw×gh) of the surface water can reach (bare ground where
// known). Water spreads from `seeds` (cell indices) through 4-connected cells whose
// ground is below `level`. Returns Uint8 mask.
export function floodFill(ground, gw, gh, level, seeds) {
  const mask = new Uint8Array(gw * gh);
  const stack = [];
  for (const s of seeds) if (ground[s] <= level && !mask[s]) { mask[s] = 1; stack.push(s); }
  while (stack.length) {
    const i = stack.pop(), r = (i / gw) | 0, c = i - r * gw;
    const nb = [c > 0 ? i - 1 : -1, c < gw - 1 ? i + 1 : -1, r > 0 ? i - gw : -1, r < gh - 1 ? i + gw : -1];
    for (const j of nb) if (j >= 0 && !mask[j] && ground[j] <= level) { mask[j] = 1; stack.push(j); }
  }
  return mask;
}

// default water source: the lowest 2 % of boundary cells (a river leaving the scene)
export function boundarySeeds(ground, gw, gh) {
  const idx = [];
  for (let c = 0; c < gw; c++) { idx.push(c, (gh - 1) * gw + c); }
  for (let r = 1; r < gh - 1; r++) { idx.push(r * gw, r * gw + gw - 1); }
  idx.sort((a, b) => ground[a] - ground[b]);
  return idx.slice(0, Math.max(4, Math.round(idx.length * 0.02)));
}

export function waterMesh(mask, gw, gh, W, H) {
  const data = new Uint8Array(gw * gh * 4);
  for (let i = 0; i < gw * gh; i++) { const v = mask[i] ? 255 : 0; data[i * 4] = v; data[i * 4 + 1] = v; data[i * 4 + 2] = v; data[i * 4 + 3] = 255; }
  const alpha = new THREE.DataTexture(data, gw, gh, THREE.RGBAFormat);
  alpha.flipY = false; alpha.magFilter = THREE.LinearFilter; alpha.minFilter = THREE.LinearFilter; alpha.needsUpdate = true;
  const geo = new THREE.PlaneGeometry(W, H); geo.rotateX(-Math.PI / 2);
  // PlaneGeometry after rotation: uv v=1 at z=-H/2 (row 0). DataTexture row 0 is v=0 -> flip in shader via repeat.
  alpha.repeat.set(1, -1); alpha.offset.set(0, 1); alpha.wrapT = THREE.RepeatWrapping;
  const mat = new THREE.MeshStandardMaterial({ color: 0x2f7fd0, transparent: true, opacity: 0.72, alphaMap: alpha,
    roughness: 0.15, metalness: 0.1, depthWrite: false, side: THREE.DoubleSide, emissive: 0x0a2440 });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 3; mesh.receiveShadow = true;
  return mesh;
}

// ---------------------------------------------------------------- small helpers
export function lonLatAt(corners, u, v) {
  if (!corners) return null;
  const [tl, tr, bl, br] = corners;
  const top = [tl[0] + (tr[0] - tl[0]) * u, tl[1] + (tr[1] - tl[1]) * u];
  const bot = [bl[0] + (br[0] - bl[0]) * u, bl[1] + (br[1] - bl[1]) * u];
  return [top[0] + (bot[0] - top[0]) * v, top[1] + (bot[1] - top[1]) * v];
}

export function niceLength(m) {
  const p = Math.pow(10, Math.floor(Math.log10(m)));
  for (const k of [5, 2, 1]) if (k * p <= m) return k * p;
  return p;
}

export function scatterSvg(plot, units = 'm') {
  if (!plot) return '';
  const w = 300, h = 240, xs = plot.ref, ys = plot.pred;
  let lo = Infinity, hi = -Infinity;
  for (const v of xs.concat(ys)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const span = (hi - lo) || 1;
  const X = (v) => 36 + (v - lo) / span * (w - 46), Y = (v) => h - 28 - (v - lo) / span * (h - 38);
  let pts = '';
  for (let i = 0; i < xs.length; i++) pts += `<circle cx="${X(xs[i]).toFixed(1)}" cy="${Y(ys[i]).toFixed(1)}" r="1.2"/>`;
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Estimated versus reference heights">
    <rect x="36" y="10" width="${w - 46}" height="${h - 38}" class="frame"/>
    <line x1="${X(lo)}" y1="${Y(lo)}" x2="${X(hi)}" y2="${Y(hi)}" class="ideal"/>
    <g class="pts">${pts}</g>
    <text x="${w / 2}" y="${h - 6}" text-anchor="middle">reference (${units})</text>
    <text x="10" y="${h / 2}" transform="rotate(-90 10 ${h / 2})" text-anchor="middle">estimated (${units})</text>
    <text x="38" y="${h - 30}" class="tick">${lo.toFixed(0)}</text><text x="${w - 12}" y="${h - 30}" class="tick" text-anchor="end">${hi.toFixed(0)}</text></svg>`;
}

export function histSvg(plot) {
  if (!plot) return '';
  const w = 300, h = 170, { hist, edges } = plot, m = Math.max(...hist) || 1, bw = (w - 20) / hist.length;
  let bars = '';
  hist.forEach((c, i) => {
    const mid = (edges[i] + edges[i + 1]) / 2, bh = c / m * (h - 40);
    bars += `<rect x="${(10 + i * bw).toFixed(1)}" y="${(h - 26 - bh).toFixed(1)}" width="${(bw - 1).toFixed(1)}" height="${bh.toFixed(1)}" class="${mid > 0 ? 'pos' : 'neg'}"/>`;
  });
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Error histogram">${bars}
    <line x1="${w / 2}" y1="8" x2="${w / 2}" y2="${h - 26}" class="ideal"/>
    <text x="10" y="${h - 8}" class="tick">${edges[0].toFixed(1)} m</text>
    <text x="${w / 2}" y="${h - 8}" text-anchor="middle">estimated − reference</text>
    <text x="${w - 10}" y="${h - 8}" class="tick" text-anchor="end">+${edges[edges.length - 1].toFixed(1)} m</text></svg>`;
}
