// Coordinates use the source affine, including rotation and shear.
export function coordinateFrame(meta) {
  const w = meta.src_w || meta.grid_w, h = meta.src_h || meta.grid_h;
  const t = meta.georeferenced && meta.transform;
  const a = t ? [t[0] * w, t[1] * h, t[2], t[3] * w, t[4] * h, t[5]] : [w, 0, 0, 0, h, 0];
  const geographic = Boolean(t && meta.corners_lonlat?.every(([lon, lat], i) => {
    const u = i % 2, v = i >> 1;
    return Math.abs(a[0] * u + a[1] * v + a[2] - lon) < 1e-5 &&
      Math.abs(a[3] * u + a[4] * v + a[5] - lat) < 1e-5;
  }));
  return { a, geographic, georeferenced: Boolean(t), crs: t ? meta.crs || 'Input CRS' : 'Image pixels' };
}

export function coordinateAt(meta, u, v) {
  const frame = coordinateFrame(meta), a = frame.a;
  const E = a[0] * u + a[1] * v + a[2], N = a[3] * u + a[4] * v + a[5];
  let ll = null;
  if (frame.geographic) ll = [E, N];
  else if (frame.georeferenced && meta.corners_lonlat?.length === 4) {
    const [tl, tr, bl, br] = meta.corners_lonlat;
    ll = [0, 1].map(i => (tl[i] + (tr[i] - tl[i]) * u) * (1 - v) + (bl[i] + (br[i] - bl[i]) * u) * v);
  }
  return { ...frame, E, N, ll };
}

const stepFor = span => {
  const x = Math.max(span / 5, 1e-9), p = 10 ** Math.floor(Math.log10(x)), d = x / p;
  return (d <= 1 ? 1 : d <= 2 ? 2 : d <= 5 ? 5 : 10) * p;
};

export function coordinateGrid(meta) {
  const frame = coordinateFrame(meta), a = frame.a, lines = [];
  if (!a.every(Number.isFinite) || Math.abs(a[0] * a[4] - a[1] * a[3]) < 1e-15) return lines;
  const corners = [[0, 0], [1, 0], [1, 1], [0, 1]];
  for (let axis = 0; axis < 2; axis++) {
    const offset = axis * 3;
    const valueAt = ([u, v]) => a[offset] * u + a[offset + 1] * v + a[offset + 2];
    const values = corners.map(valueAt), lo = Math.min(...values), hi = Math.max(...values), step = stepFor(hi - lo);
    for (let value = Math.ceil(lo / step) * step, n = 0; value < hi && n < 12; value += step, n++) {
      const uv = [];
      for (let i = 0; i < 4; i++) {
        const p = corners[i], q = corners[(i + 1) % 4], delta = valueAt(q) - valueAt(p);
        if (Math.abs(delta) < 1e-15) continue;
        const f = (value - valueAt(p)) / delta;
        if (f < -1e-9 || f > 1 + 1e-9) continue;
        const hit = [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
        if (!uv.some(p => Math.hypot(p[0] - hit[0], p[1] - hit[1]) < 1e-8)) uv.push(hit);
      }
      if (uv.length !== 2) continue;
      const name = frame.geographic ? ['Lon', 'Lat'][axis] : frame.georeferenced ? ['E', 'N'][axis] : ['Col', 'Row'][axis];
      const decimals = Math.max(0, -Math.floor(Math.log10(step)));
      lines.push({ uv, axis, value, label: `${name} ${value.toFixed(Math.min(6, decimals))}${frame.geographic ? '°' : ''}` });
    }
  }
  return lines;
}

// Exact source-CRS probes. Each request replaces the previous hover query.
export function createCoordinateProbe({ fetchApi, getScene, onResult }) {
  let timer, controller, sequence = 0;
  return {
    probe(u, v) {
      clearTimeout(timer); controller?.abort();
      const id = getScene(), seq = ++sequence;
      timer = setTimeout(async () => {
        controller = new AbortController();
        try {
          const response = await fetchApi(`api/scenes/${encodeURIComponent(id)}/coordinates`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({points:[[u,v]]}), signal: controller.signal, silent:true });
          const result = await response.json();
          if (response.ok && id === getScene() && seq === sequence) onResult(result.points[0]);
        } catch (_) { /* Relative scenes or pointer moves have no geographic readout. */ }
      }, 90);
    },
    reset() { sequence++; clearTimeout(timer); controller?.abort(); }
  };
}
