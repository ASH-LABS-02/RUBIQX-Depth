import * as THREE from 'three';

// Water elevations come from the sampled runoff grid, not a scalar bathtub level.
export function createRunoffOverlay(result,{W,H,worldY}) {
  const [w,h]=result.simulation_grid, depth=result.depth_m.flat(), ground=result.ground_m.flat();
  if(depth.length!==w*h || ground.length!==w*h) throw new Error('Runoff grid size mismatch');
  const positions=new Float32Array(w*h*3),colors=new Float32Array(w*h*3),indices=[];
  const valid=ground.map((g,i)=>g!==null && Number.isFinite(g) && Number.isFinite(depth[i]));
  const factor=result.sampling_factor, color=new THREE.Color();
  for(let r=0;r<h;r++)for(let c=0;c<w;c++){
    const i=r*w+c;
    positions.set([(c*factor/(result.grid_w-1)-.5)*W,worldY(valid[i]?ground[i]+depth[i]+.02:0),
      (r*factor/(result.grid_h-1)-.5)*H],i*3);
    color.setHSL(.55+Math.min(depth[i]/3,1)*.06,.8,.5-Math.min(depth[i]/3,1)*.23);
    colors.set([color.r,color.g,color.b],i*3);
  }
  for(let r=0;r<h-1;r++)for(let c=0;c<w-1;c++){
    const a=r*w+c,b=a+1,d=a+w,e=d+1;
    for(const t of [[a,d,b],[b,d,e]]) if(t.every(i=>valid[i]) && t.some(i=>depth[i]>.001))indices.push(...t);
  }
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
  geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));geometry.setIndex(indices);geometry.computeVertexNormals();
  const material=new THREE.MeshStandardMaterial({vertexColors:true,transparent:true,opacity:.75,depthWrite:false,
    roughness:.25,metalness:.05,side:THREE.DoubleSide});
  return new THREE.Mesh(geometry,material);
}
