import * as THREE from 'three';

// Display-only camera path. Heights always clear the estimated surface.
export function cinematicPath({ W, H, extent, top, centreY, angle, terrainY }) {
  const points = [], targets = [];
  for (let i = 0; i <= 8; i++) {
    const t = i / 8, a = angle + t * Math.PI * 1.65;
    const radius = t < 0.5 ? 0.82 - t * 0.8 : 0.42 + (t - 0.5) * 0.7;
    const x = Math.sin(a) * W * radius, z = Math.cos(a) * H * radius;
    const ground = terrainY(x, z);
    const y = Math.max(top + extent * (0.08 + 0.34 * Math.abs(2 * t - 1)), ground + extent * 0.1);
    points.push(new THREE.Vector3(x, y, z));
    targets.push(new THREE.Vector3(x * 0.16, centreY, z * 0.16));
  }
  return {
    position: new THREE.CatmullRomCurve3(points, false, 'centripetal'),
    target: new THREE.CatmullRomCurve3(targets, false, 'centripetal'),
  };
}
