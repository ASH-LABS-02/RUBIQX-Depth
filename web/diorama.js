import * as THREE from 'three';

// Decorative presentation geometry. Neither soil bands nor the base are data
// products; source DSM vertices, calibration, probes and exports are untouched.
export function createDiorama(scene) {
  const soil=document.createElement('canvas');soil.width=64;soil.height=256;
  const ctx=soil.getContext('2d');
  const bands=[[0,'#34383e'],[.22,'#51545a'],[.29,'#72634e'],[.35,'#40392f'],[.53,'#927450'],[.58,'#514333'],[.79,'#302c29'],[1,'#252424']];
  for(let y=0;y<256;y++){
    ctx.fillStyle=bands.find(([limit])=>y/255<=limit)[1];ctx.fillRect(0,y,64,1);
    for(let x=0;x<64;x++){const noise=((x*37+y*113+x*y*17)%101)/100;ctx.fillStyle=noise>.55?'rgba(225,213,184,.09)':'rgba(0,0,0,.10)';ctx.fillRect(x,y,1,1);}
  }
  const texture=new THREE.CanvasTexture(soil);texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=THREE.RepeatWrapping;
  const walls=new THREE.Mesh(new THREE.BufferGeometry(),new THREE.MeshStandardMaterial({map:texture,roughness:1,side:THREE.DoubleSide}));
  walls.castShadow=true;walls.receiveShadow=true;walls.name='Decorative soil cross-section';
  const rim=new THREE.LineSegments(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({color:0x75d2da,transparent:true,opacity:.48}));
  const base=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.MeshStandardMaterial({color:0x22272e,roughness:1,side:THREE.DoubleSide}));base.rotation.x=-Math.PI/2;base.receiveShadow=true;
  const groundMaterial=new THREE.MeshStandardMaterial({color:0x0c1720,roughness:1,transparent:true,depthWrite:false});
  const groundUniforms={uExtent:{value:1},uGrid:{value:100}};
  groundMaterial.onBeforeCompile=shader=>{
    Object.assign(shader.uniforms,groundUniforms);
    shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nvarying vec3 vGround;').replace('#include <worldpos_vertex>','#include <worldpos_vertex>\nvGround=(modelMatrix*vec4(transformed,1.0)).xyz;');
    shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying vec3 vGround;uniform float uExtent,uGrid;').replace('#include <color_fragment>',`#include <color_fragment>
      vec2 q=vGround.xz/uGrid;
      vec2 d=abs(fract(q-.5)-.5)/max(fwidth(q),vec2(.0001));
      float line=1.-min(min(d.x,d.y),1.);
      float fade=1.-smoothstep(uExtent*1.4,uExtent*5.,length(vGround.xz));
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.16,.26,.31),line*.23*fade);
      diffuseColor.a*=fade;`);
  };
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(1,1),groundMaterial);ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;
  const contact=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.ShaderMaterial({transparent:true,depthWrite:false,uniforms:{},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',fragmentShader:'varying vec2 vUv;void main(){vec2 d=max(abs(vUv-.5)-vec2(.32),0.);float a=.5*(1.-smoothstep(0.,.18,length(d)));gl_FragColor=vec4(0.,0.,0.,a);}'}));contact.rotation.x=-Math.PI/2;
  scene.add(ground,contact,base,walls,rim);
  function update(s,position,floor){
    const v=[],uv=[],outline=[],ceiling=Math.max(s.extent*.045,(s.hmax-s.base)*s.exag),depth=ceiling-floor;
    const point=(r,c)=>{const i=r*s.gw+c;return[position.getX(i),position.getY(i),position.getZ(i)];};
    const edges=[[],[],[],[]],steps=Math.min(48,Math.max(s.gw,s.gh)-1);
    for(let k=0;k<=steps;k++){
      const c=Math.round(k/steps*(s.gw-1)),r=Math.round(k/steps*(s.gh-1));
      edges[0].push(point(0,c));edges[1].push(point(s.gh-1,c));edges[2].push(point(r,0));edges[3].push(point(r,s.gw-1));
    }
    for(const edge of edges)for(let k=0;k<edge.length-1;k++){
      const a=edge[k],b=edge[k+1],af=[a[0],floor,a[2]],bf=[b[0],floor,b[2]];
      for(const p of [a,b,af,b,bf,af]){v.push(...p);uv.push((p[0]+p[2])/24,(p[1]-floor)/depth);}
      outline.push(a[0],a[1]+s.extent*.00015,a[2],b[0],b[1]+s.extent*.00015,b[2]);
    }
    walls.geometry.dispose();walls.geometry=new THREE.BufferGeometry();walls.geometry.setAttribute('position',new THREE.Float32BufferAttribute(v,3));walls.geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));walls.geometry.computeVertexNormals();walls.geometry.computeBoundingSphere();
    rim.geometry.dispose();rim.geometry=new THREE.BufferGeometry();rim.geometry.setAttribute('position',new THREE.Float32BufferAttribute(outline,3));rim.geometry.computeBoundingSphere();
    base.scale.set(s.W,s.H,1);base.position.y=floor;
    ground.scale.setScalar(s.extent*24);ground.position.y=floor-s.extent*.001;
    contact.scale.set(s.W*1.5,s.H*1.5,1);contact.position.y=ground.position.y+s.extent*.0001;
    groundUniforms.uExtent.value=s.extent;
    // For unreferenced imagery the grid is a display aid, not a surveyed scale.
    groundUniforms.uGrid.value=s.meta.georeferenced?100:Math.max(1,s.extent/5);
  }
  return {walls,update};
}
