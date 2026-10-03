const DIALOGS = [
  { id: 'upload-modal', initial: '#upload-close', close: '#upload-close', fallback: '#rail-import,#import-btn' },
  { id: 'command-palette', initial: '#command-input', close: '[data-close-command]', fallback: '#command-open' },
  { id: 'gallery', initial: '#gallery-close', close: '#gallery-close', fallback: '#rail-gallery,#gallery-btn' },
  { id: 'help', initial: '#help-close', close: '#help-close', fallback: '#help-btn' },
];
const FOCUSABLE = 'a[href],area[href],button,input:not([type="hidden"]),select,textarea,summary,[tabindex],[contenteditable]:not([contenteditable="false"]),audio[controls],video[controls]';

// Existing handlers can continue toggling .hidden; explicit open() also saves
// the trigger before focus changes or an asynchronous gallery request finishes.
export function createDialogManager({ onOpen, onClose, onCommand } = {}) {
  const dialogs = DIALOGS.map((config) => ({ ...config, element: document.getElementById(config.id) }))
    .filter((dialog) => dialog.element);
  const isolated = new Map(), pendingTriggers = new Map();
  let stack = [], lastInteraction = document.activeElement, syncing = false, destroyed = false;
  for (const dialog of dialogs) {
    dialog.originalTabIndex = dialog.element.getAttribute('tabindex');
    dialog.originalModal = dialog.element.getAttribute('aria-modal');
    dialog.originalZIndex = dialog.element.style.zIndex;
    if (!dialog.element.hasAttribute('tabindex')) dialog.element.tabIndex = -1;
  }

  const top = () => stack[stack.length - 1];
  const shown = (element) => element.isConnected && !element.hidden && !element.classList.contains('hidden')
    && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
  const usable = (element) => element instanceof HTMLElement && shown(element)
    && !element.matches(':disabled,[aria-disabled="true"]') && !element.closest('[inert]');
  function tabbable(dialog) {
    return [...dialog.element.querySelectorAll(FOCUSABLE)].filter((element) => usable(element)
      && (element.tabIndex >= 0 || (element.isContentEditable && !element.hasAttribute('tabindex'))))
      .sort((a, b) => (a.tabIndex > 0 ? a.tabIndex : Infinity) - (b.tabIndex > 0 ? b.tabIndex : Infinity));
  }
  function focus(element) {
    if (!usable(element)) return false;
    element.focus({ preventScroll: true });
    return document.activeElement === element;
  }
  function focusDialog(dialog, preferred) {
    const inside = (element) => dialog.element.contains(element) && usable(element);
    const target = inside(preferred) ? preferred : inside(dialog.lastFocus) ? dialog.lastFocus
      : dialog.element.querySelector(dialog.initial);
    if (!focus(target)) focus(tabbable(dialog)[0]) || focus(dialog.element);
    dialog.lastFocus = document.activeElement;
  }
  function restoreAttribute(element, name, value) {
    if (value === null) element.removeAttribute(name);
    else element.setAttribute(name, value);
  }
  function restoreIsolation(element, state) {
    restoreAttribute(element, 'inert', state.inert);
    restoreAttribute(element, 'aria-hidden', state.ariaHidden);
  }
  function background(dialog) {
    const nodes = new Set();
    if (!dialog) return nodes;
    // Gallery lives inside #stage, so suspend siblings at every ancestor rather
    // than making the entire application (and its dialog) inert.
    let branch = dialog.element;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (sibling !== branch && !sibling.matches('script,style,link')) nodes.add(sibling);
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    return nodes;
  }
  function resolveTrigger(element, opening) {
    const visited = new Set();
    while (element) {
      const owner = dialogs.find((dialog) => dialog.element.contains(element));
      if (!owner || (owner !== opening && shown(owner.element))) return element;
      if (visited.has(owner)) return null;
      visited.add(owner);
      element = owner.trigger;
    }
    return null;
  }

  function sync() {
    if (destroyed || syncing) return;
    syncing = true;
    try {
      const previousTop = top();
      const closed = stack.filter((dialog) => !shown(dialog.element));
      stack = stack.filter((dialog) => shown(dialog.element));
      const opened = dialogs.filter((dialog) => shown(dialog.element) && !stack.includes(dialog));
      for (const dialog of opened) {
        dialog.trigger = resolveTrigger(pendingTriggers.get(dialog) || lastInteraction || document.activeElement, dialog);
        dialog.lastFocus = null;
        pendingTriggers.delete(dialog);
        stack.push(dialog);
      }
      const current = top(), outside = background(current);
      // Restore the new dialog's ancestor path before moving focus into it.
      for (const [element, state] of isolated) {
        if (!outside.has(element)) {
          restoreIsolation(element, state);
          isolated.delete(element);
        }
      }
      for (const dialog of dialogs) {
        const index = stack.indexOf(dialog);
        if (index >= 0) {
          dialog.element.setAttribute('aria-modal', String(dialog === current));
          const zIndex = String(1000 + index);
          if (dialog.element.style.zIndex !== zIndex) dialog.element.style.zIndex = zIndex;
        } else {
          restoreAttribute(dialog.element, 'aria-modal', dialog.originalModal);
          if (dialog.element.style.zIndex !== dialog.originalZIndex) dialog.element.style.zIndex = dialog.originalZIndex;
        }
      }
      for (const dialog of closed) onClose?.(dialog.id, dialog.element);
      for (const dialog of opened) onOpen?.(dialog.id, dialog.element);
      if (current) {
        if (!current.element.contains(document.activeElement) || !usable(document.activeElement)) {
          const returning = previousTop !== current ? resolveTrigger(previousTop?.trigger, null) : null;
          focusDialog(current, returning);
        }
      } else if (previousTop) {
        const trigger = resolveTrigger(previousTop.trigger, null);
        if (!focus(trigger)) focus([...document.querySelectorAll(previousTop.fallback)].find(usable));
      }
      for (const element of outside) {
        if (!isolated.has(element)) {
          isolated.set(element, { inert: element.getAttribute('inert'), ariaHidden: element.getAttribute('aria-hidden') });
          element.setAttribute('inert', '');
          element.setAttribute('aria-hidden', 'true');
        }
      }
    } finally { syncing = false; }
  }

  function open(id, trigger = document.activeElement) {
    const dialog = dialogs.find((item) => item.id === id);
    if (!dialog || destroyed) return;
    const savedTrigger = resolveTrigger(trigger, dialog);
    // Reconcile a close before reopening in the same task, so its old trigger
    // and background state cannot leak into the next dialog session.
    if (!shown(dialog.element)) sync();
    if (stack.includes(dialog) && shown(dialog.element)) {
      // Ctrl+K can bring an existing palette forward without clearing its query.
      if (top() !== dialog) {
        stack = stack.filter((item) => item !== dialog);
        stack.push(dialog);
      }
      sync();
      focusDialog(dialog, dialog.element.querySelector(dialog.initial));
      return;
    }
    pendingTriggers.set(dialog, savedTrigger);
    dialog.element.classList.remove('hidden');
    dialog.element.hidden = false;
    sync();
  }
  function close(id = top()?.id) {
    const dialog = dialogs.find((item) => item.id === id);
    if (!dialog || destroyed) return;
    if (!shown(dialog.element)) { sync(); return; }
    const control = dialog.element.querySelector(dialog.close);
    if (control) control.click();
    if (shown(dialog.element)) dialog.element.classList.add('hidden');
    sync();
  }
  function captureKey(event) {
    sync();
    const current = top();
    if (!current) { lastInteraction = document.activeElement; return; }
    if (event.type === 'keydown' && !event.isComposing) {
      const command = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
      if (event.key === 'Escape' || event.key === 'Tab' || command) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (command) {
          if (current.id === 'command-palette') focusDialog(current, current.element.querySelector(current.initial));
          else if (onCommand) onCommand();
          else open('command-palette');
        } else if (event.key === 'Escape') {
          if (!event.repeat) close(current.id);
        } else {
          const controls = tabbable(current), index = controls.indexOf(document.activeElement);
          const next = event.shiftKey ? (index <= 0 ? controls.length - 1 : index - 1) : (index + 1) % controls.length;
          if (!focus(controls[next])) focus(current.element);
        }
        return;
      }
    }
    if (!current.element.contains(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      focusDialog(current);
    }
  }
  // Let target input/button handlers run, then stop keys before they reach the
  // application's window shortcuts. Default text editing remains available.
  function stopGlobalKeys(event) {
    if (top()) event.stopImmediatePropagation();
  }
  function capturePointer(event) {
    sync();
    const current = top();
    if (current && event.isTrusted && !current.element.contains(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.type !== 'wheel') focusDialog(current);
    } else if (event.type === 'pointerdown') {
      lastInteraction = event.target.closest?.(FOCUSABLE) || document.activeElement;
    }
  }
  function containFocus(event) {
    if (syncing) return;
    sync();
    const current = top();
    if (current && !current.element.contains(event.target)) focusDialog(current);
    else if (current) current.lastFocus = event.target;
    else lastInteraction = event.target;
  }
  const observer = new MutationObserver((changes) => {
    // Camera readouts replace text every frame; only changed elements affect
    // dialog visibility, focusable controls, or background isolation.
    if (changes.some((change) => change.type === 'attributes'
      || [...change.addedNodes, ...change.removedNodes].some((node) => node.nodeType === Node.ELEMENT_NODE))) sync();
  });
  observer.observe(document.body, { subtree: true, childList: true });
  for (const dialog of dialogs) observer.observe(dialog.element, { subtree: true, childList: true, attributes: true,
    attributeFilter: ['class', 'hidden', 'style', 'disabled', 'open', 'tabindex'] });
  for (const type of ['keydown', 'keyup']) {
    window.addEventListener(type, captureKey, true);
    document.addEventListener(type, stopGlobalKeys);
  }
  for (const type of ['pointerdown', 'click', 'wheel']) window.addEventListener(type, capturePointer, { capture: true, passive: false });
  document.addEventListener('focusin', containFocus, true);
  sync();
  return {
    open, close, sync,
    isOpen: (id) => id ? stack.some((dialog) => dialog.id === id) : stack.length > 0,
    destroy() {
      destroyed = true;
      observer.disconnect();
      for (const type of ['keydown', 'keyup']) {
        window.removeEventListener(type, captureKey, true);
        document.removeEventListener(type, stopGlobalKeys);
      }
      for (const type of ['pointerdown', 'click', 'wheel']) window.removeEventListener(type, capturePointer, true);
      document.removeEventListener('focusin', containFocus, true);
      for (const [element, state] of isolated) restoreIsolation(element, state);
      for (const dialog of dialogs) {
        restoreAttribute(dialog.element, 'tabindex', dialog.originalTabIndex);
        restoreAttribute(dialog.element, 'aria-modal', dialog.originalModal);
        dialog.element.style.zIndex = dialog.originalZIndex;
      }
      isolated.clear(); stack = [];
    },
  };
}
