// modal.js — in-page modal + confirm + toast helpers (no alert/confirm/prompt: sandboxed iframe).
// All functions return Promises or elements; nothing blocks.

let backdrop = null;

function ensureBackdrop() {
  if (!backdrop) {
    backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.addEventListener('mousedown', (e) => {
      if (e.target === backdrop) closeBackdrop(null);
    });
    document.body.appendChild(backdrop);
  }
  return backdrop;
}

// Escape key closes the topmost open modal.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && backdrop) {
    const top = backdrop.lastElementChild;
    if (top && top._resolve) closeBackdrop(null);
  }
});

function closeBackdrop(value, viaEl) {
  if (!backdrop) return;
  const el = viaEl || backdrop.lastElementChild;
  if (el && el._resolve) {
    const resolve = el._resolve;
    delete el._resolve;
    el.classList.add('modal-closing');
    setTimeout(() => {
      el.remove();
      // Forget the removed backdrop too, or every later modal is appended to a detached
      // node and never shows (the second dialog after a page load silently did nothing).
      if (backdrop && !backdrop.firstElementChild) { backdrop.remove(); backdrop = null; }
    }, 140);
    resolve(value);
  }
}

/**
 * openModal({title, body: Node|string, actions: [{label, value, primary, submit}], onClose})
 * Returns a Promise resolved with the clicked action's `value` (or null on dismiss).
 */
export function openModal({ title, body, actions = null, onClose = null, wide = false }) {
    const wrap = document.createElement('div');
    wrap.className = 'modal' + (wide ? ' modal-wide' : '');
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');

    const h = document.createElement('h2');
    h.className = 'modal-title';
    h.textContent = title || '';
    wrap.appendChild(h);

    const content = document.createElement('div');
    content.className = 'modal-body';
    if (typeof body === 'string') {
      const p = document.createElement('p');
      p.textContent = body;
      content.appendChild(p);
    } else if (body) content.appendChild(body);
    wrap.appendChild(content);

    if (actions) {
      const bar = document.createElement('div');
      bar.className = 'modal-actions';
      for (const a of actions) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn' + (a.danger ? ' btn-danger' : '') + (a.primary ? ' btn-primary' : '');
        b.textContent = a.label;
        b.addEventListener('click', () => {
          let ok = true;
          if (a.submit) {
            // Validate: call a.validate() if the form provides one; cancel close on false.
            const form = wrap.querySelector('form');
            if (form && typeof form.reportValidity === 'function' && !form.reportValidity()) ok = false;
          }
          if (ok) closeBackdrop(a.value, wrap);
        });
        bar.appendChild(b);
      }
      wrap.appendChild(bar);
    }

    wrap._close = (v) => closeBackdrop(v, wrap);
    const bd = ensureBackdrop();
    bd.appendChild(wrap);
    if (onClose) wrap._onClose = onClose;

    // Focus first input/button for keyboard users.
    requestAnimationFrame(() => {
      const input = wrap.querySelector('input, select, textarea');
      if (input) input.focus();
      else {
        const btn = wrap.querySelector('button');
        if (btn) btn.focus();
      }
    });

    return new Promise((resolve) => { wrap._resolve = resolve; });
  }

  /** textPrompt(title, {placeholder, value, label}) -> Promise<string|null> */
  export function textPrompt(title, { placeholder = '', value = '', label = 'Name', type = 'text' } = {}) {
    const body = document.createElement('div');
    body.className = 'form-row';
    const lab = document.createElement('label');
    lab.textContent = label;
    const input = document.createElement('input');
    input.type = type;
    input.value = value;
    input.placeholder = placeholder;
    input.required = true;
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const btn = [...document.querySelectorAll('.modal .btn-primary')].pop();
        if (btn) btn.click();
      }
    });
    body.appendChild(lab);
    body.appendChild(input);
    return openModal({
      title,
      body,
      actions: [
        { label: 'Cancel', value: null },
        { label: 'OK', value: () => input.value, primary: true, submit: true },
      ],
    }).then((v) => (typeof v === 'function' ? v() : v));
  }

  /** confirmDialog(message, {title, okLabel}) -> Promise<bool> */
  export function confirmDialog(message, { title = 'Confirm', okLabel = 'Delete' } = {}) {
    return openModal({
      title,
      body: message,
      actions: [
        { label: 'Cancel', value: false },
        { label: okLabel, value: true, primary: true, danger: okLabel === 'Delete' },
      ],
    }).then((v) => !!v);
  }

  // ---- Toasts -----------------------------------------------------------
  const toastHost = () => {
    let host = document.querySelector('.toast-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'toast-host';
      host.setAttribute('role', 'status');
      host.setAttribute('aria-live', 'polite');
      document.body.appendChild(host);
    }
    return host;
  };

  export function toast(message, { kind = 'info', ttl = 4000 } = {}) {
    const host = toastHost();
    const el = document.createElement('div');
    el.className = `toast toast-${kind}`;
    el.textContent = message;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('toast-show'));
    setTimeout(() => {
      el.classList.remove('toast-show');
      setTimeout(() => el.remove(), 300);
    }, ttl);
    return el;
  }
