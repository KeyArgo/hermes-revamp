// A small fake DOM for the library view tests (same one as library_track_rows.test.mjs),
// plus a scan-poll timer the test can fire by hand.
const VOID = new Set(["input", "br", "img"]);

export class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.id = "";
    this.className = "";
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this._ls = [];
    this._text = "";
    this.hidden = false;
    this.value = "";
    this.tabIndex = 0;
  }
  // Like the real DOM: setting the text replaces the children; reading it joins theirs.
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v) { this.children = []; this._text = String(v ?? ""); }
  get parentNode() { return this.parent; }
  get classList() {
    const el = this;
    const names = () => String(el.className || "").split(/\s+/).filter(Boolean);
    return {
      contains: (c) => names().includes(c),
      add: (c) => { if (!names().includes(c)) el.className = `${el.className} ${c}`.trim(); },
      remove: (c) => { el.className = names().filter((x) => x !== c).join(" "); },
      toggle: (c, force) => {
        const on = force === undefined ? !names().includes(c) : !!force;
        on ? el.classList.add(c) : el.classList.remove(c);
        return on;
      },
    };
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === "id") this.id = String(v);
    if (k === "class") this.className = String(v);
    if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v);
  }
  getAttribute(k) { return this.attrs[k] ?? null; }
  append(...kids) { for (const k of kids) this.appendChild(k); }
  appendChild(k) {
    if (k == null || typeof k === "string") return k;
    k.parent = this;
    this.children.push(k);
    return k;
  }
  prepend(k) {
    if (k == null || typeof k === "string") return k;
    k.parent = this;
    this.children.unshift(k);
    return k;
  }
  replaceChildren(...kids) {
    this.children = [];
    for (const k of kids) this.appendChild(k);
  }
  remove() {
    const p = this.parent;
    if (!p) return;
    p.children = p.children.filter((c) => c !== this);
    this.parent = null;
  }
  get isConnected() {
    let n = this;
    while (n) { if (n === globalThis.document.body || n === globalThis.document) return true; n = n.parent; }
    return false;
  }
  contains(node) {
    let n = node;
    while (n) { if (n === this) return true; n = n.parent; }
    return false;
  }
  addEventListener(type, fn, opts) {
    const rec = { type, fn };
    this._ls.push(rec);
    const signal = opts && opts.signal;
    if (!signal) return;
    const drop = () => { this._ls = this._ls.filter((r) => r !== rec); };
    if (signal.aborted) drop();
    else signal.addEventListener("abort", drop);
  }
  removeEventListener(type, fn) {
    this._ls = this._ls.filter((r) => !(r.type === type && r.fn === fn));
  }
  fire(type, props = {}) {
    const ev = {
      type, target: this, currentTarget: this,
      preventDefault() { ev.defaultPrevented = true; },
      stopPropagation() { ev.stopped = true; },
      ...props,
    };
    let node = this;
    while (node && !ev.stopped) {
      for (const rec of [...(node._ls || [])]) if (rec.type === type) rec.fn(ev);
      node = node.parent;
    }
    return ev;
  }
  focus() { globalThis.document.activeElement = this; }
  click() { this.fire("click"); }
  blur() { if (globalThis.document.activeElement === this) globalThis.document.activeElement = null; }
  getBoundingClientRect() { return { left: 20, top: 30, right: 140, bottom: 80, width: 120, height: 50 }; }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel); }
  set innerHTML(html) { this.children = []; parseInto(this, String(html)); }
  get innerHTML() { return this.textContent; }
  closest(sel) {
    let n = this;
    while (n && n.tag) { if (matchesToken(n, sel.trim())) return n; n = n.parent; }
    return null;
  }
}

function matchesToken(el, tok) {
  if (!el || !el.tag || el.tag === "#text") return false;
  let rest = tok.trim();
  const attrM = /\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/.exec(rest);
  if (attrM) {
    const name = attrM[1];
    const want = attrM[2] ?? attrM[3] ?? attrM[4];
    const have = name.startsWith("data-") ? el.dataset[name.slice(5)] : el.attrs[name];
    if (have === undefined) return false;
    if (want !== undefined && String(have) !== want) return false;
    rest = rest.replace(attrM[0], "");
  }
  const clsM = /\.([\w-]+)/.exec(rest);
  if (clsM) {
    if (!String(el.className || "").split(/\s+/).includes(clsM[1])) return false;
    rest = rest.replace(clsM[0], "");
  }
  if (rest.startsWith("#")) return el.id === rest.slice(1);
  const tagM = /^([\w-]+)$/.exec(rest);
  if (tagM) return el.tag === tagM[1];
  return rest.trim() === "";
}

function queryAll(root, sel) {
  const parts = sel.trim().split(/\s+/);
  const out = [];
  const walk = (node, index) => {
    for (const child of node.children || []) {
      if (!child.tag || child.tag === "#text") continue;
      if (matchesToken(child, parts[index])) {
        if (index === parts.length - 1) out.push(child);
        else walk(child, index + 1);
      }
      walk(child, index);
    }
  };
  walk(root, 0);
  return out;
}

function parseInto(parent, html) {
  let i = 0;
  const takeNodes = () => {
    const nodes = [];
    while (i < html.length) {
      if (html.startsWith("</", i)) break;
      if (html[i] === "<") {
        const end = html.indexOf(">", i);
        let raw = html.slice(i + 1, end).trim();
        i = end + 1;
        const self = raw.endsWith("/");
        raw = raw.replace(/\/$/, "").trim();
        const tag = raw.split(/\s+/)[0].toLowerCase();
        const el = new El(tag);
        const attr = /([:@\w-]+)(?:="([^"]*)")?/g;
        let m;
        while ((m = attr.exec(raw))) {
          if (m[1] === tag) continue;
          el.setAttribute(m[1], m[2] ?? "");
        }
        if (!self && !VOID.has(tag)) {
          for (const kid of takeNodes()) el.appendChild(kid);
          if (html.startsWith("</", i)) i = html.indexOf(">", i) + 1;
        }
        nodes.push(el);
      } else {
        const next = html.indexOf("<", i);
        const text = html.slice(i, next === -1 ? html.length : next);
        i = next === -1 ? html.length : next;
        if (text.trim()) {
          const t = new El("#text");
          t.textContent = text;
          nodes.push(t);
        }
      }
    }
    return nodes;
  };
  for (const n of takeNodes()) parent.appendChild(n);
}

// Timers: setTimeout is a queue the test flushes (long-press, debounce); setInterval
// callbacks are kept so a test can run the view's scan poll when it wants.
const pendingTimers = new Map();
let timerSeq = 0;
export function flushTimers() {
  const due = [...pendingTimers.values()];
  pendingTimers.clear();
  for (const fn of due) fn();
}

export const observers = [];
export const intervals = [];
export function install() {
  const body = new El("body");
  const document = {
    body,
    hidden: false,
    activeElement: null,
    _ls: [],
    createElement: (tag) => new El(tag),
    createTextNode: (text) => { const t = new El("#text"); t.textContent = String(text); return t; },
    addEventListener(type, fn, opts) {
      const rec = { type, fn };
      this._ls.push(rec);
      const signal = opts && opts.signal;
      if (!signal) return;
      const drop = () => { this._ls = this._ls.filter((r) => r !== rec); };
      if (signal.aborted) drop();
      else signal.addEventListener("abort", drop);
    },
    removeEventListener(type, fn) {
      this._ls = this._ls.filter((r) => !(r.type === type && r.fn === fn));
    },
    querySelector: (sel) => queryAll(body, sel)[0] || null,
    querySelectorAll: (sel) => queryAll(body, sel),
    getElementById: (id) => queryAll(body, `#${id}`)[0] || null,
  };
  body.parent = document;
  globalThis.document = document;
  globalThis.MutationObserver = class {
    constructor(cb) { this.cb = cb; }
    observe() {}
    disconnect() {}
  };
  observers.length = 0;
  globalThis.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.observes = 0; this.disconnects = 0; observers.push(this); }
    observe() { this.observes += 1; }
    disconnect() { this.disconnects += 1; }
  };
  globalThis.window = globalThis;
  globalThis.innerWidth = 1200;
  globalThis.innerHeight = 800;
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.dispatchEvent = () => true;
  globalThis.requestAnimationFrame = (fn) => fn(0);
  globalThis.setTimeout = (fn) => { pendingTimers.set(++timerSeq, fn); return timerSeq; };
  globalThis.clearTimeout = (id) => { pendingTimers.delete(id); };
  intervals.length = 0;
  globalThis.setInterval = (fn) => { intervals.push(fn); return intervals.length; };
  globalThis.clearInterval = () => {};
  return document;
}

export const tick = () => new Promise((resolve) => setImmediate(resolve));
export const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
