import * as THREE from 'three';

// A bounded quadtree. Parents stay visible until every child is ready; errors
// retain the parent. Analysis/probing still uses the saved authoritative grid.
export function createTerrainStream({ id, fetchApi, W, H, worldY, maxLevel = 4, maxTiles = 64 }) {
  const group = new THREE.Group(), controller = new AbortController();
  const nodes = new Map();
  let pending = 0, stopped = false, lastUpdate = 0, failures = 0;
  const root = { level:0, col:0, row:0, children:null, mesh:null, loading:false };
  const dispose = node => {
    node.disposed = true;
    node.children?.forEach(dispose);
    if (node.mesh) { group.remove(node.mesh); node.mesh.geometry.dispose(); node.mesh.material.map?.dispose(); node.mesh.material.dispose(); }
    const key = `${node.level}/${node.col}/${node.row}`;
    if (nodes.get(key) === node) nodes.delete(key);
  };
  async function load(node) {
    if (node.disposed || node.loading || node.mesh || pending >= 3 || stopped || nodes.size >= maxTiles) return;
    node.loading = true; pending++;
    const key = `${node.level}/${node.col}/${node.row}`;
    nodes.set(key, node);
    try {
      const base = `api/scenes/${encodeURIComponent(id)}/tiles/${key}/`;
      const [hr, tr] = await Promise.all(['height','texture'].map(kind => fetchApi(base+kind,
        { signal:controller.signal, silent:true })));
      if (!hr.ok || !tr.ok) throw new Error('Terrain tile unavailable');
      const heights = new Float32Array(await hr.arrayBuffer()), size = Number(hr.headers.get('X-Grid-W'));
      if (heights.length !== size * size) throw new Error('Terrain tile size mismatch');
      const bitmap = await createImageBitmap(await tr.blob(), {imageOrientation:'flipY'});
      if (stopped || node.disposed) { bitmap.close(); return; }
      const texture = new THREE.Texture(bitmap); texture.colorSpace = THREE.SRGBColorSpace; texture.needsUpdate = true;
      const n = 2**node.level, geometry = new THREE.PlaneGeometry(W/n,H/n,size-1,size-1);
      geometry.rotateX(-Math.PI/2);
      const p = geometry.attributes.position;
      const finite = heights.filter(Number.isFinite);
      if (!finite.length) { geometry.dispose(); texture.dispose(); bitmap.close(); throw new Error('Terrain tile contains no valid elevation'); }
      const fallback = finite.reduce((a,b)=>Math.min(a,b),Infinity);
      for (let i=0;i<heights.length;i++) p.setY(i, worldY(Number.isFinite(heights[i]) ? heights[i] : fallback));
      // Nodata is a hole, not an invented zero-elevation cliff.
      const index = geometry.index.array, validTriangles = [];
      for (let i=0;i<index.length;i+=3) if ([index[i],index[i+1],index[i+2]].every(k=>Number.isFinite(heights[k])))
        validTriangles.push(index[i],index[i+1],index[i+2]);
      geometry.setIndex(validTriangles);
      p.needsUpdate = true; geometry.computeVertexNormals(); geometry.computeBoundingSphere();
      const material = new THREE.MeshStandardMaterial({ map:texture, roughness:.95, side:THREE.DoubleSide });
      material.addEventListener('dispose', () => bitmap.close());
      node.mesh = new THREE.Mesh(geometry,material);
      node.mesh.position.set(((node.col+.5)/n-.5)*W,0,((node.row+.5)/n-.5)*H);
      node.mesh.receiveShadow = true;
      group.add(node.mesh);
    } catch (error) {
      if (error.name !== 'AbortError') failures++;
      if (nodes.get(key) === node) nodes.delete(key);
      node.failedUntil = performance.now()+5000;
    } finally { node.loading = false; pending--; }
  }
  const frustum = new THREE.Frustum(), matrix = new THREE.Matrix4(), center = new THREE.Vector3();
  function visit(node,camera,viewport) {
    if (!node.mesh) { if (performance.now() >= (node.failedUntil || 0)) load(node); return; }
    node.mesh.updateMatrixWorld();
    const visible = frustum.intersectsObject(node.mesh);
    node.mesh.visible = visible;
    const n = 2**node.level;
    center.copy(node.mesh.position); center.y = node.mesh.geometry.boundingSphere.center.y;
    const distance = Math.max(1,camera.position.distanceTo(center));
    const projected = Math.max(W,H)/n*viewport/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov/2))*distance);
    const refine = visible && projected > 350 && node.level < maxLevel;
    if (!refine && node.children) { node.children.forEach(dispose); node.children = null; }
    if (!refine) return;
    if (!node.children && nodes.size+4 <= maxTiles) node.children = [0,1,2,3].map(k =>
      ({level:node.level+1,col:node.col*2+k%2,row:node.row*2+(k>>1),mesh:null,loading:false,children:null}));
    if (!node.children) return;
    node.children.forEach(child => { if (!child.mesh) load(child); });
    if (node.children.every(child => child.mesh)) {
      node.mesh.visible = false;
      node.children.forEach(child => visit(child,camera,viewport));
    } else node.children.forEach(child => { if(child.mesh) child.mesh.visible = false; });
  }
  return { group,
    update(camera, viewport, now = performance.now()) {
      if (stopped || now-lastUpdate < 150) return Boolean(root.mesh);
      lastUpdate = now;
      matrix.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse); frustum.setFromProjectionMatrix(matrix);
      visit(root,camera,viewport);
      return Boolean(root.mesh);
    },
    diagnostics() { return {tiles:nodes.size,pending,failures,maxTiles,maxLevel}; },
    dispose() { stopped = true; controller.abort(); dispose(root); }
  };
}
