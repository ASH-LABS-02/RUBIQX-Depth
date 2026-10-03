// Camera navigation only: estimated footprints are obstacles, not surveyed access.
export function createWalkController({ W, H, groundWm = W, groundHm = H, groundAt,
  polygons = [], radiusM = .35, maxSlopeDeg = 35, maxStepM = .45 }) {
  const sx = W / groundWm, sz = H / groundHm, cell = 20;
  const grid = new Map(), rings = [];
  const key = (x, z) => `${Math.floor(x / sx / cell)},${Math.floor(z / sz / cell)}`;
  for (const polygon of polygons) {
    if (!polygon?.length || polygon.length < 3 || polygon.some(p => !p.every(Number.isFinite))) continue;
    const ring = polygon.map(([x, z]) => [x / sx, z / sz]);
    const xs = ring.map(p => p[0]), zs = ring.map(p => p[1]);
    const box = { ring, x0: Math.min(...xs) - radiusM, x1: Math.max(...xs) + radiusM,
      z0: Math.min(...zs) - radiusM, z1: Math.max(...zs) + radiusM };
    rings.push(box);
    for (let x = Math.floor(box.x0 / cell); x <= Math.floor(box.x1 / cell); x++)
      for (let z = Math.floor(box.z0 / cell); z <= Math.floor(box.z1 / cell); z++) {
        const k = `${x},${z}`;
        if (!grid.has(k)) grid.set(k, []);
        grid.get(k).push(box);
      }
  }
  const bounds = (x, z) => Math.abs(x) <= W / 2 - radiusM * sx && Math.abs(z) <= H / 2 - radiusM * sz;
  function obstacle(x, z) {
    const mx = x / sx, mz = z / sz;
    for (const box of grid.get(key(x, z)) || []) {
      if (mx < box.x0 || mx > box.x1 || mz < box.z0 || mz > box.z1) continue;
      let inside = false;
      for (let i = 0, j = box.ring.length - 1; i < box.ring.length; j = i++) {
        const [ax, az] = box.ring[j], [bx, bz] = box.ring[i];
        if ((bz > mz) !== (az > mz) && mx < (ax - bx) * (mz - bz) / (az - bz) + bx) inside = !inside;
        const dx = bx - ax, dz = bz - az, len2 = dx * dx + dz * dz;
        const t = Math.max(0, Math.min(1, len2 ? ((mx - ax) * dx + (mz - az) * dz) / len2 : 0));
        if (Math.hypot(mx - ax - t * dx, mz - az - t * dz) < radiusM) return true;
      }
      if (inside) return true;
    }
    return false;
  }
  const gradeLimit = Math.tan(maxSlopeDeg * Math.PI / 180);
  function valid(x, z, checkSlope = false) {
    if (!bounds(x, z) || obstacle(x, z) || !Number.isFinite(groundAt(x, z))) return false;
    if (!checkSlope) return true;
    const d = .6, x0 = Math.max(-W / 2, x - d * sx), x1 = Math.min(W / 2, x + d * sx);
    const z0 = Math.max(-H / 2, z - d * sz), z1 = Math.min(H / 2, z + d * sz);
    const gx = (groundAt(x1, z) - groundAt(x0, z)) / ((x1 - x0) / sx);
    const gz = (groundAt(x, z1) - groundAt(x, z0)) / ((z1 - z0) / sz);
    return Number.isFinite(gx + gz) && Math.hypot(gx, gz) <= gradeLimit;
  }
  function spawn(x, z) {
    x = Math.max(-W / 2 + radiusM * sx, Math.min(W / 2 - radiusM * sx, x));
    z = Math.max(-H / 2 + radiusM * sz, Math.min(H / 2 - radiusM * sz, z));
    if (valid(x, z, true)) return { x, z, ground: groundAt(x, z) };
    // Search nearby first; a coarse scene scan covers roof-centred entry views.
    for (let r = 1; r <= 32; r++) {
      const radius = r * 1.5, count = Math.max(12, Math.ceil(radius * 4));
      for (let n = 0; n < count; n++) {
        const a = n / count * Math.PI * 2, px = x + Math.cos(a) * radius * sx, pz = z + Math.sin(a) * radius * sz;
        if (valid(px, pz, true)) return { x: px, z: pz, ground: groundAt(px, pz) };
      }
    }
    let best = null, nearest = Infinity;
    for (let r = 0; r < 40; r++) for (let c = 0; c < 40; c++) {
      const px = ((c + .5) / 40 - .5) * W, pz = ((r + .5) / 40 - .5) * H;
      const distance = Math.hypot((px - x) / sx, (pz - z) / sz);
      if (distance < nearest && valid(px, pz, true)) { nearest = distance; best = { x: px, z: pz, ground: groundAt(px, pz) }; }
    }
    return best;
  }
  function move(x, z, dx, dz) {
    // Substeps prevent tunnelling through narrow walls at low frame rates.
    const count = Math.max(1, Math.ceil(Math.hypot(dx / sx, dz / sz) / .18));
    dx /= count; dz /= count;
    function reachable(px, pz) {
      if (!valid(px, pz)) return false;
      const distance = Math.hypot((px - x) / sx, (pz - z) / sz);
      const rise = Math.abs(groundAt(px, pz) - groundAt(x, z));
      return rise <= maxStepM && rise <= Math.max(.015, distance * gradeLimit);
    }
    for (let n = 0; n < count; n++) {
      if (reachable(x + dx, z + dz)) { x += dx; z += dz; }
      else if (Math.abs(dx) >= Math.abs(dz)) {
        if (reachable(x + dx, z)) x += dx;
        if (reachable(x, z + dz)) z += dz;
      } else {
        if (reachable(x, z + dz)) z += dz;
        if (reachable(x + dx, z)) x += dx;
      }
    }
    return { x, z, ground: groundAt(x, z) };
  }
  return { spawn, move, valid, obstacle, groundAt, footprintCount: rings.length,
    limits: { radiusM, maxSlopeDeg, maxStepM } };
}
