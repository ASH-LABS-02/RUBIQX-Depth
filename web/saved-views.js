// Local saved viewpoints contain display state only, never source heights.
export function createSavedViews({ getState, camera, orbit, restore, toast }) {
  const section = document.createElement('details'); section.className = 'saved-views';
  section.innerHTML = '<summary>Saved viewpoints</summary><label>View name<input maxlength="60" placeholder="North ridge"></label><div class="btns"><button type="button" data-save>Save current</button><button type="button" data-delete>Delete selected</button></div><label>Stored views<select aria-label="Stored viewpoints"></select></label><button type="button" data-load>Restore view</button>';
  const input = section.querySelector('input'), select = section.querySelector('select');
  const read = () => { try { const views=JSON.parse(localStorage.getItem('dw-views:'+getState().id) || '[]');
    return Array.isArray(views) ? views.filter(v=>v && typeof v.id==='string' && typeof v.name==='string'
      && Array.isArray(v.camera) && v.camera.length===3 && Array.isArray(v.target) && v.target.length===3) : [];
  } catch (_) { return []; } };
  function refresh() { select.replaceChildren(...read().map(v => new Option(v.name,v.id))); }
  section.querySelector('[data-save]').onclick = () => {
    const state = getState(); if (!state.id) return;
    const views = read();
    views.push({id:String(Date.now()),name:input.value.trim() || `View ${views.length+1}`,
      camera:camera.position.toArray(),up:camera.up.toArray(),target:orbit.target.toArray(),layer:state.mode,
      geometry:state.viewGeometry,exag:state.exag,anchorScale:state.meta?.height_anchor?.s || 1});
    try { localStorage.setItem('dw-views:'+state.id,JSON.stringify(views.slice(-20))); refresh(); toast('View saved on this browser.'); }
    catch (_) { toast('Browser storage is unavailable.','error'); }
  };
  section.querySelector('[data-load]').onclick = () => {
    const view = read().find(v => v.id===select.value); if (!view) return;
    if (![...view.camera,...view.target,view.exag].every(Number.isFinite)) return;
    restore(view);
    if (view.anchorScale !== (getState().meta?.height_anchor?.s || 1)) toast('Restored viewpoint; calibration changed since it was saved.');
  };
  section.querySelector('[data-delete]').onclick = () => {
    try { localStorage.setItem('dw-views:'+getState().id,JSON.stringify(read().filter(v => v.id!==select.value))); refresh(); }
    catch (_) { toast('Browser storage is unavailable.','error'); }
  };
  return { section, refresh };
}
