const $=s=>document.querySelector(s);
const paths={orbit:'<circle cx="12" cy="12" r="7"/><path d="m8 3-4 4 4 1m8 13 4-4-4-1"/>',fly:'<path d="m3 10 18-7-7 18-3-8-8-3Zm8 3 10-10"/>',tour:'<path d="M8 4v16l13-8-13-8Z"/>',top:'<path d="m3 8 9-5 9 5-9 5-9-5Zm0 4 9 5 9-5M3 16l9 5 9-5"/>',reset:'<path d="M4 10a8 8 0 1 1 1 8M4 4v6h6"/>',mesh:'<path d="m3 18 4-12 6 3 5-5 3 14-7 3-11-3Zm4-12 7 15M3 18l10-9 8 9"/>',city:'<path d="M3 21V9h6v12M9 21V3h6v18m0 0V7h6v14M6 12v2m6-8v2m6 3v2M1 21h22"/>',compare:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16m-6-9 2 2-2 2m12-4-2 2 2 2"/>',exag:'<path d="M12 3v18m-4-4 4 4 4-4M8 7l4-4 4 4M3 12h3m12 0h3"/>',full:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',present:'<rect x="3" y="3" width="18" height="14" rx="2"/><path d="m7 22 5-5 5 5"/>',vr:'<path d="M3 7h18v9h-5l-4-3-4 3H3V7Z"/><circle cx="7" cy="11" r="1"/><circle cx="17" cy="11" r="1"/>'};
export function createBoldUi({getState,orbit,canvas,requestRender}){
  let lastInput=performance.now(),first=true,fadeTimer=null,slow=0,priorFrame=0;
  const arrival=document.createElement('div');arrival.id='arrival';arrival.className='hidden';arrival.setAttribute('aria-hidden','true');
  arrival.innerHTML='<h1>DepthWizard</h1><p id="arrival-subtitle">One satellite image → a 3D world</p><small id="arrival-evidence"></small>';
  $('#stage').append(arrival);
  const tools=[...document.querySelectorAll('#nav-mode button')].map(b=>[b,b.dataset.nav,b.textContent.trim()]);
  tools.push(...[...document.querySelectorAll('#view-mode button')].map(b=>[b,b.dataset.view==='city'?'city':'mesh',b.dataset.view==='city'?'Roof-fit City':'DSM Mesh']));
  for(const [id,key,name]of [['topdown','top','Top down'],['reset','reset','Reset view'],['compare-trigger','compare','Compare'],['exag-trigger','exag','Vertical exaggeration'],['fullscreen','full','Fullscreen viewer'],['present-btn','present','Present'],['vr-toggle','vr','VR']])tools.push([$('#'+id),key,name]);
  for(const [button,key,name]of tools){
    button.setAttribute('aria-label',name);button.dataset.v3='true';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.innerHTML=paths[key]||paths.orbit;
    const tip=document.createElement('span');tip.className='v3-tool-label';tip.textContent=button.title||name;button.replaceChildren(svg,tip);
  }
  $('#drawer-toggle').textContent='×';$('#drawer-toggle').setAttribute('aria-label','Close inspector');
  const mapShell=document.createElement('div');mapShell.id='minimap-shell';$('#minimap').before(mapShell);mapShell.append($('#minimap'));
  const mapNorth=document.createElement('span');mapNorth.id='minimap-north';mapNorth.innerHTML='▲<small>N</small>';mapShell.append(mapNorth);
  const syncDock=()=>$('#app').style.setProperty('--dock-width',`${$('#layer-dock').getBoundingClientRect().width}px`);
  new ResizeObserver(syncDock).observe($('#layer-dock'));syncDock();
  function stopArrival(){clearTimeout(fadeTimer);arrival.classList.add('hidden');$('#stage').classList.remove('arriving');}
  function input(){lastInput=performance.now();orbit.autoRotate=false;stopArrival();}
  for(const event of ['pointerdown','pointermove','wheel','keydown','touchstart','input','change'])addEventListener(event,input,{passive:true,capture:true});
  orbit.autoRotateSpeed=.1;
  function sceneLoaded(){
    lastInput=performance.now();orbit.autoRotate=false;
    const s=getState(),relative=s.meta.units!=='metre';
    const t=s.meta.transform,det=t?t[0]*t[4]-t[1]*t[3]:0;
    const nx=det?-t[1]/det*s.W/s.meta.src_w:0,nz=det?t[0]/det*s.H/s.meta.src_h:-1;
    mapNorth.style.transform=`rotate(${Math.atan2(nx,-nz)*180/Math.PI}deg)`;
    mapNorth.querySelector('small').textContent=s.meta.georeferenced?'N':'UP';mapNorth.title=s.meta.georeferenced?'Grid north in the optical image':'Image up · no geographic north';
    $('#arrival-subtitle').textContent=`One satellite image → ${relative?'a relative':'an estimated'} 3D world`;
    $('#arrival-evidence').textContent=relative?'Relative units · metric calibration not available':`${s.meta.calibration?.evidence_level||'unverified'} evidence · calibration and validation remain visible`;
    if(first){first=false;if(!matchMedia('(prefers-reduced-motion: reduce)').matches){arrival.classList.remove('hidden');$('#stage').classList.add('arriving');fadeTimer=setTimeout(stopArrival,5000);}}
  }
  function tick(active){
    const now=performance.now(),s=getState();
    const idle=s.mesh&&s.nav==='orbit'&&!s.presentation&&!s.swipeActive&&!s.cameraFlight&&!s.floodAnimating&&!s.waterAnim&&!s.recording&&!document.hidden&&now-lastInput>=20000&&$('#app').classList.contains('drawer-collapsed')&&$('#upload-modal').classList.contains('hidden')&&$('#gallery').classList.contains('hidden')&&!matchMedia('(prefers-reduced-motion: reduce)').matches;
    orbit.autoRotate=Boolean(idle);
    const dt=priorFrame?now-priorFrame:0;priorFrame=now;
    if(active&&dt>22&&dt<1000)slow+=dt;else slow=0;
    if(slow>2000){$('#app').classList.add('v3-solid-glass');slow=0;}
    return Boolean(idle);
  }
  return {sceneLoaded,tick};
}
