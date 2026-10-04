import { inspectTiff } from './import-metadata.js?v=20261001-v1';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const escape = (s) => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function createMissionUi(ctx) {
  const {getState, camera, orbit, requestRender, setWorkspace, loadScene, setNav, setMode, toast} = ctx;
  const form = $('#upload-form');
  let previewUrl=null, inputGeneration=0, openingPlayed=false, flyTimer=null, jobStarted=0, jobTimer=null;
  let lastJob=null, priorTiming=null, slowFor=0, lastFrame=0, reducedQuality=false;
  let jobInputDem=false,jobHasReference=false;

  // One drop and one click; all original named inputs and their listeners survive.
  const drop = $('#drop');
  const details = [...form.querySelectorAll('details')];
  const run = form.querySelector('button[type=submit]');
  const basic = details[0], reference = details[1], options = details[2];
  const gcp = basic.querySelector('input[name=gcp]').closest('label');
  const advanced = document.createElement('details'); advanced.id='import-advanced';
  advanced.innerHTML='<summary>Advanced options</summary><p class="note">Optional evidence and processing controls.</p>';
  advanced.append(gcp, ...reference.children, ...options.children);
  [...advanced.querySelectorAll('summary')].slice(1).forEach(s=>s.remove());
  const demKind = advanced.querySelector('[name=dem_kind]').closest('label');
  const match = advanced.querySelector('[name=match_dem_30m]').closest('label');
  basic.append(demKind,match);
  basic.open=false;
  basic.querySelector('summary').textContent='Scale evidence · auto-download Copernicus';
  basic.querySelector('[name=dem]').closest('label').firstChild.textContent='No internet? Drop a DEM file here instead';
  const intro=document.createElement('div'); intro.id='import-intro';
  intro.innerHTML='<div id="import-paths" aria-label="Input paths"><button type="button" data-path="geo" aria-pressed="false"><strong>GeoTIFF</strong><span>Metric heights with calibration</span></button><button type="button" data-path="relative" aria-pressed="false"><strong>PNG / JPG</strong><span>Relative heights</span></button></div><p class="note">The file chooses the path automatically. GeoTIFF coordinates locate the image; a DEM or surveyed anchors establish elevation scale.</p>';
  const preview=document.createElement('div');
  preview.innerHTML='<img id="import-preview" class="hidden" alt="Selected optical image"><p id="import-detection" class="note" role="status">Drop an optical image to inspect its metadata.</p><p id="import-resolution-warning" class="note hidden"></p>';
  const progress=document.createElement('section'); progress.id='import-processing';progress.className='hidden';
  progress.setAttribute('aria-label','Processing status');
  progress.innerHTML='<p id="import-job-state" role="status">Ready</p><div id="import-stage-list" aria-label="Processing stages"><span>Reading</span><span>Depth</span><span>Calibration</span><span>DSM</span><span>3D model</span><span>Validation</span></div><div id="import-progress"><i></i></div><p id="import-elapsed" class="note"></p><div id="import-error" class="hidden" role="alert"></div>';
  const logDetails=document.createElement('details');logDetails.id='import-log-details';
  logDetails.innerHTML='<summary>Details · processing log</summary>';logDetails.append($('#job-log'));
  const stageProgress=document.createElement('div');stageProgress.id='job-progress';stageProgress.className='hidden';stageProgress.innerHTML='<i></i>';
  $('#app-header').append(stageProgress);
  form.dataset.flow='single';form.replaceChildren(intro,drop,preview,run,basic,advanced,progress);
  form.append(logDetails);
  run.textContent='Generate 3D scene';
  form.fetch_dem.checked=true;form.dem_source.value='COP30';form.dem_kind.value='auto';form.match_dem_30m.checked=true;form.tta.value='4';
  form.model.addEventListener('change',()=>form.model.dataset.userSelected='true');
  form.sun_elevation.closest('.two-col').insertAdjacentHTML('beforebegin','<p class="note">Sun angles: optional — not needed for reconstruction.</p>');
  $$('#import-paths button').forEach(b=>b.onclick=()=>{ form.image.click(); });
  $('#upload-title').textContent='Import imagery';
  $('#upload-title').nextElementSibling.textContent='One image. Explore, validate and export.';

  async function inspectInput() {
    const generation=++inputGeneration, file=form.image.files[0];
    const image=$('#import-preview'), detection=$('#import-detection'), warning=$('#import-resolution-warning');
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl=null;image.classList.add('hidden');warning.classList.add('hidden');form.dataset.detecting='false';
    if (!file) { detection.textContent='Drop an optical image to inspect its metadata.';return; }
    const isTiff=/\.tiff?$/i.test(file.name);
    if (!/\.(tiff?|png|jpe?g)$/i.test(file.name)) {
      detection.textContent='Choose a PNG, JPG or TIFF image.';form.dataset.invalidInput='true';return;
    }
    form.dataset.invalidInput='false';form.dataset.detecting='true';run.disabled=true;
    detection.textContent='Reading image metadata…';
    let info;
    try {
      if (isTiff) info=await inspectTiff(file);
      else {
        const bitmap=await createImageBitmap(file);
        info={width:bitmap.width,height:bitmap.height,georeferenced:false,bands:3};bitmap.close();
      }
      if(generation!==inputGeneration)return;
      const path=info.georeferenced?'geo':'relative';
      form.dataset.inputPath=path;
      form.dataset.inputDem=String(info.inputDem===true);
      $$('#import-paths button').forEach(b=>{b.classList.toggle('selected',b.dataset.path===path);b.setAttribute('aria-pressed',String(b.dataset.path===path));});
      basic.classList.toggle('hidden',!info.georeferenced);
      form.fetch_dem.checked=info.georeferenced&&!info.inputDem;
      form.match_dem_30m.checked=info.georeferenced&&!info.inputDem;
      form.fetch_dem.disabled=!info.georeferenced||info.inputDem;
      const pixel=info.gsd?`${info.gsd.approximate?'≈':''}${info.gsd.x.toFixed(2)} × ${info.gsd.y.toFixed(2)} m/px`:'pixel size checked by server';
      const shape=info.width&&info.height?`${info.width.toLocaleString()} × ${info.height.toLocaleString()} px`:'dimensions checked by server';
      detection.textContent=info.inputDem
        ? `${shape} · single-band TIFF → Input DEM (not estimated). ${info.crs||'Spatial reference checked by server'}.`
        : info.georeferenced
          ? `Georeferenced RGB · ${info.crs||'custom CRS'} · ${pixel} · ${shape}. DEM / anchors calibrate metric heights.`
          : `${shape} · no georeferencing → relative heights. Horizontal pixel size does not turn relative heights into metres.`;
      if(info.gsd) {
        const lo=Math.min(info.gsd.x,info.gsd.y),hi=Math.max(info.gsd.x,info.gsd.y);
        if(lo<.35||hi>10) { warning.textContent='Outside the 0.35–10 m/px evaluation range. Review image resolution before processing.';warning.classList.remove('hidden'); }
        else if(hi>2.5) { warning.textContent='Coarse image: heights come mainly from the DEM; building detail is limited.';warning.classList.remove('hidden'); }
      }
      if(!isTiff) {previewUrl=URL.createObjectURL(file);image.src=previewUrl;image.classList.remove('hidden');}
      run.textContent=info.inputDem?'Open input DEM':info.georeferenced?'Generate metric scene':'Generate relative scene';
    } catch(error) {
      if(generation!==inputGeneration)return;
      form.dataset.inputPath='unknown';basic.classList.remove('hidden');
      form.fetch_dem.disabled=false;form.fetch_dem.checked=isTiff;
      detection.textContent=`${error.message} Georeferencing and final units will be determined by the server.`;
    } finally {
      if(generation===inputGeneration){form.dataset.detecting='false';run.disabled=false;}
    }
  }

  function humanError(raw) {
    const text=String(raw||'The scene could not be processed.');
    try { const j=JSON.parse(text); if(typeof j.detail==='string')return humanError(j.detail);if(Array.isArray(j.detail))return 'Check the image and processing options, then try again.';}catch{}
    const coverage=text.match(/(?:coverage|covers)[^\d]*(\d+(?:\.\d+)?)\s*%/i);
    if(coverage)return `DEM covers only ${coverage[1]}% of the image. Drop a DEM for this area or enable Auto-download.`;
    if(/api.key|opentopography|download.*dem|dem.*download/i.test(text))return 'DEM download is unavailable. Drop a local DEM file for this area, then retry.';
    if(/cuda.*memory|out of memory|memoryerror/i.test(text))return 'This image exceeds available processing memory. Use a smaller image or a smaller model.';
    if(/checkpoint|weights|model.*not found/i.test(text))return 'The selected model is unavailable. Choose an installed model or supply a valid checkpoint.';
    return text.split('\n').filter(Boolean).slice(-1)[0].slice(0,260);
  }
  function elapsed() {
    if(!jobStarted)return;
    const seconds=Math.floor((performance.now()-jobStarted)/1000);
    const history=priorTiming?` · previous scene: ${Math.round(priorTiming)} s (estimate only)`:' · completion estimate unavailable';
    $('#import-elapsed').textContent=`Elapsed ${seconds}s${history}`;
  }
  function jobStart() {
    jobInputDem=form.dataset.inputDem==='true';jobHasReference=!!form.reference.files.length;
    clearInterval(jobTimer);jobStarted=performance.now();jobTimer=setInterval(elapsed,1000);
    $('#import-processing').classList.remove('hidden');$('#import-error').classList.add('hidden');
    $('#import-log-details').open=false;$('#job-log').classList.remove('hidden');
    $$('#import-stage-list span').forEach(e=>{e.className='';e.removeAttribute('aria-current');});
    jobUpdate({state:'uploading',log:[]});elapsed();
    run.textContent='Processing…';
  }
  function jobUpdate(st) {
    lastJob=st;
    const log=(st.log||[]).join('\n').toLowerCase();
    const state=$('#import-job-state');
    state.textContent=st.state==='queued'?`Queued — ${st.position?`${st.position} job${st.position===1?'':'s'} ahead`:'starting next'}`
      : st.state==='uploading'?'Uploading image…':st.state==='done'?'Scene ready':['error','cancelled'].includes(st.state)?'Processing stopped':st.stage||'Reconstructing scene…';
    // Backend logs are the evidence of progress; a timer never advances the stages.
    const checks=[/reading image/,/relative height/,/scale calibration/,/extracting lod1|validating against reference|analytics:/,/done in/,/done in/];
    let reached=-1;checks.forEach((re,i)=>{if(re.test(log))reached=i;});
    const done=st.state==='done',active=!done&&!['error','cancelled'].includes(st.state);
    $$('#import-stage-list span').forEach((e,i)=>{
      const completed=done||(i<reached);
      e.classList.toggle('complete',completed);e.classList.toggle('active',!done&&i===reached);
      if(i===reached&&!done)e.setAttribute('aria-current','step');else e.removeAttribute('aria-current');
      if(i===5&&!jobHasReference&&done){e.textContent='No reference';e.classList.remove('complete');e.classList.add('skipped');}
      else if(jobInputDem&&i===1){e.textContent='Not estimated';e.classList.remove('complete');e.classList.add('skipped');}
      else if(jobInputDem&&i===2){e.textContent='Input DEM';e.classList.remove('complete');e.classList.add('skipped');}
      else e.textContent=['Reading','Depth','Calibration','DSM','3D model','Validation'][i];
    });
    const fraction=done?100:Math.max(0,(reached+1)/6*100);
    $('#import-progress i').style.width=`${fraction}%`;$('#job-progress i').style.width=`${fraction}%`;
    $('#job-progress').classList.toggle('hidden',!active);
    if(!active){clearInterval(jobTimer);elapsed();}
    if(done)run.textContent='Generate another scene';
    if(st.state==='cancelled'){
      state.textContent='Processing cancelled · saved inputs can be retried';
      run.textContent=jobInputDem?'Open input DEM':form.dataset.inputPath==='geo'?'Generate metric scene':'Generate relative scene';
    }
    if(st.state==='error'){
      const error=$('#import-error');error.replaceChildren();error.classList.remove('hidden');
      const message=document.createElement('p');message.textContent=humanError(st.error);error.append(message);
      const retry=document.createElement('button');retry.type='button';retry.textContent='Retry processing';retry.onclick=()=>form.requestSubmit();error.append(retry);
      run.textContent='Generate 3D scene';
    }
  }

  // Evidence explanation is readable without entering the calibration workspace.
  const evidence=document.createElement('section');evidence.id='evidence-popover';evidence.className='hidden';
  evidence.setAttribute('role','dialog');evidence.setAttribute('aria-label','Scene calibration evidence');
  evidence.innerHTML='<div><h2>Calibration evidence</h2><button type="button" aria-label="Close evidence">×</button></div><p id="evidence-description"></p><p id="evidence-datum" class="note"></p><button id="evidence-calibrate" type="button">Review calibration</button>';
  $('#app').append(evidence);evidence.querySelector('[aria-label="Close evidence"]').onclick=()=>evidence.classList.add('hidden');
  $('#evidence-calibrate').onclick=()=>{evidence.classList.add('hidden');setWorkspace('calibrate');};
  const explainEvidence=()=>{
    const s=getState(),cal=s.meta?.calibration||{},relative=s.meta?.units!=='metre';
    const method=cal.method||'unverified';
    const description=relative?'No absolute height calibration is available; surface heights are in relative units.'
      : method==='input-dem'?'This is the uploaded input DEM, visualised directly; no elevation estimation was performed.'
      : /gcp/.test(method)?'Surveyed ground-control points set the elevation scale; trust depends on point quality, coverage and independent validation.'
      : /anchor/.test(method)?'Supplied building heights set the structure scale; these anchors are calibration inputs, not independent accuracy evidence.'
      : cal.dem_kind==='surface'?'A surface DEM constrains metric elevations at its resolution; finer structural detail remains model estimated.'
      : /learned/.test(method)||cal.learned_scale_k?'A terrain DEM sets the ground elevation and a learned scale sets structural heights; this is approximate evidence.'
      : /dem/.test(method)?'An input DEM and scene prior constrain metric heights; building detail remains approximate.'
      : 'Metric scale is reported by the backend; review its calibration method and evidence before relying on heights.';
    $('#evidence-description').textContent=description;
    $('#evidence-datum').textContent=`Evidence: ${cal.evidence_level||'unverified'} · ${s.meta?.vertical_datum||cal.vertical_datum||'vertical datum not supplied'}`;
    evidence.classList.toggle('hidden');$('#scene-badge').setAttribute('aria-expanded',String(!evidence.classList.contains('hidden')));
  };
  $('#scene-badge').onclick=explainEvidence;
  $('#scene-badge').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();explainEvidence();}};

  // Compact onboarding leaves the scene visible; choice and dismissal are remembered.
  const welcome=document.createElement('section');welcome.id='welcome-card';welcome.className='hidden';welcome.setAttribute('aria-label','Welcome to DepthWizard');
  welcome.innerHTML='<div class="welcome-heading"><h2>One image. A surface to explore.</h2><button type="button" aria-label="Dismiss welcome">×</button></div><ol><li><b>1</b><span>Import an image</span></li><li><b>2</b><span>Explore in 3D</span></li><li><b>3</b><span>Validate & export</span></li></ol><div id="welcome-demos"></div><div class="welcome-actions"><button id="welcome-import" type="button" class="primary">Import imagery</button><button id="welcome-explore" type="button">Explore this scene</button></div><p>Press <kbd>?</kbd> for controls · Team RUBIQX</p>';
  $('#stage').append(welcome);
  function dismissWelcome(){welcome.classList.add('hidden');try{localStorage.setItem('dw-welcome-v2','1');}catch{}}
  welcome.querySelector('[aria-label="Dismiss welcome"]').onclick=dismissWelcome;
  $('#welcome-explore').onclick=dismissWelcome;
  $('#welcome-import').onclick=()=>{dismissWelcome();$('#rail-import').click();};
  function scenesReady(scenes){
    $('#welcome-demos').replaceChildren();
    for(const id of ['dc-glover-park','nyc-relative']){
      const sc=scenes.find(x=>x.id===id)||scenes.find(x=>id==='nyc-relative'&&x.units!=='metre');
      if(!sc)continue;
      const b=document.createElement('button');b.type='button';b.textContent=sc.units==='metre'?'Try metric demo':'Try relative demo';
      b.onclick=()=>{dismissWelcome();loadScene(sc.id);};$('#welcome-demos').append(b);
    }
    let seen=false;try{seen=localStorage.getItem('dw-welcome-v2')==='1';}catch{}
    if(!seen)welcome.classList.remove('hidden');
  }

  const exaggeration=document.createElement('span');exaggeration.id='exaggeration-note';exaggeration.className='hidden';$('#stage').append(exaggeration);
  const presets=document.createElement('div');presets.id='exag-presets';
  for(const value of [1,1.5,2,3]){const b=document.createElement('button');b.type='button';b.textContent=`${value}×`;b.onclick=()=>{const slider=$('#exag');slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));};presets.append(b);}
  $('#exag-popover').append(presets);
  function exaggerationChanged(){const s=getState();exaggeration.textContent=`×${s.exag.toFixed(1)} exaggerated`;exaggeration.classList.toggle('hidden',s.exag===1);const trigger=$('#exag-trigger');if(trigger.dataset.v3)trigger.dataset.value=`${s.exag.toFixed(1)}×`;else trigger.textContent=`${s.exag.toFixed(1)}×`;}
  $('#exag').addEventListener('input',exaggerationChanged);

  function sceneLoaded(){
    const s=getState();evidence.classList.add('hidden');$('#scene-badge').setAttribute('aria-expanded','false');exaggerationChanged();
    if(Number.isFinite(s.meta?.timing_s?.total))priorTiming=s.meta.timing_s.total;
    slowFor=0;lastFrame=0;reducedQuality=false;
    const metric=s.meta?.units==='metre';
    $('#contour-unit').textContent=metric?'m':'relative units';
    const badge=s.meta?.calibration?.method==='input-dem'?'Input DEM (not estimated)':metric?'Metric elevation':'Relative heights';
    $('#minimap').title=`${badge} · click to move camera`;
    $('#export-menu [data-export="dsm"] small').textContent=s.meta?.calibration?.method==='input-dem'?'Original input grid · not estimated':metric?'Original estimated grid':'Relative surface grid · unitless';
    $('#export-menu [data-export="dsm"]').firstChild.textContent=metric?'DSM GeoTIFF ':'rDSM GeoTIFF ';
    $('#export-menu [data-export="uncertainty"]').disabled=!s.confidence||s.meta?.has_uncertainty===false;
    $('#export-menu [data-export="uncertainty"] small').textContent=metric?'Provisional σ · two-scene calibration':'Unitless ensemble spread · not an error bar';
    $('#export-menu [data-export="ndsm"]').disabled=!metric||s.meta?.calibration?.method==='input-dem';
    if(!openingPlayed&&!reducedMotion()){
      openingPlayed=true;
      const toCamera=camera.position.clone(),toTarget=orbit.target.clone();
      camera.position.copy(toCamera).sub(toTarget).multiplyScalar(1.6).add(toTarget);
      orbit.update();
      s.cameraFlight={started:performance.now(),duration:5000,opening:true,fromCamera:camera.position.clone(),toCamera,
        fromTarget:orbit.target.clone(),toTarget};requestRender();
    }
  }
  function cancelOpening(){const s=getState();if(s.cameraFlight?.opening){s.cameraFlight=null;requestRender();}}
  ['pointermove','pointerdown','wheel','keydown','touchstart'].forEach(ev=>addEventListener(ev,cancelOpening,{capture:true,passive:true}));
  function navigationChanged(mode){clearTimeout(flyTimer);$('#fly-hint').classList.remove('faded');if(mode==='fly'||mode==='walk')flyTimer=setTimeout(()=>$('#fly-hint').classList.add('faded'),5000);}
  function tick(active){
    const now=performance.now(),s=getState(),frame=lastFrame?now-lastFrame:0;lastFrame=now;
    if(!active||!s.mesh||s.cameraFlight||reducedQuality||document.hidden){slowFor=0;return;}
    // Frame cadence includes CPU rendering and GPU presentation, and excludes idle skips.
    slowFor=frame>33&&frame<1000?slowFor+frame:0;
    if(s.quality==='cinematic'&&slowFor>2000){reducedQuality=true;$('#quality').value='balanced';$('#quality').dispatchEvent(new Event('change'));toast('Switched to Balanced quality to keep navigation responsive.');}
    else if(s.quality==='balanced'&&slowFor>2000&&s.aoEnabled!==false){s.aoEnabled=false;slowFor=0;toast('Reduced ambient occlusion to keep navigation responsive.');requestRender();}
  }
  addEventListener('keydown',e=>{
    if(e.target.matches('input,select,textarea')||e.target.isContentEditable)return;
    if(e.key==='?'){e.preventDefault();$('#help').classList.toggle('hidden');}
    if(e.key==='Home'){e.preventDefault();ctx.resetView();requestRender();}
    if(e.key==='Escape'){dismissWelcome();evidence.classList.add('hidden');}
  });
  $('#help h2').textContent='Explore DepthWizard';
  $('#help table').insertAdjacentHTML('beforeend','<tr><td>Quick start</td><td>Import an image → explore layers → validate with a reference → export.</td></tr><tr><td>Home / ?</td><td>Home resets the camera · ? opens these controls · double-click terrain to fly there.</td></tr>');
  inspectInput();
  return {inspectInput,jobStart,jobUpdate,sceneLoaded,scenesReady,navigationChanged,tick,humanError,exaggerationChanged};
}
