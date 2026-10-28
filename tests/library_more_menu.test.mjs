// The library More menu's document click listener has to leave with the view.
import assert from "node:assert/strict";
import test from "node:test";

const VOID = new Set(["input", "br", "img"]);

class El {
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
    this._mos = [];
    this.textContent = "";
    this.hidden = false;
    this.open = false;
    this.value = "";
    this.tabIndex = 0;
  }
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
  replaceChildren(...kids) {
    this.children = [];
    for (const k of kids) this.appendChild(k);
  }
  remove() {
    const p = this.parent;
    if (!p) return;
    p.children = p.children.filter((c) => c !== this);
    this.parent = null;
    for (const mo of [...(p._mos || [])]) mo.cb([]);
  }
  get isConnected() {
    let n = this;
    while (n) { if (n === document.body || n === document) return true; n = n.parent; }
    return false;
  }
  contains(node) {
    let n = node;
    while (n) { if (n === this) return true; n = n.parent; }
    return false;
  }
  addEventListener(type, fn) { this._ls.push({ type, fn }); }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel); }
  set innerHTML(html) { this.children = []; parseInto(this, String(html)); }
  get innerHTML() { return this.textContent; }
  closest(sel) {
    let n = this;
    while (n && n.tag) { if (matches(n, sel)) return n; n = n.parent; }
    return null;
  }
}

function matches(el, sel) {
  if (!el || !el.tag || el.tag === "#text") return false;
  if (sel.startsWith(".")) return String(el.className || "").split(/\s+/).includes(sel.slice(1));
  if (sel.startsWith("#")) return el.id === sel.slice(1);
  const m = /^([a-zA-Z0-9-]+)(?:\.([a-zA-Z0-9_-]+))?$/.exec(sel);
  if (!m) return false;
  if (el.tag !== m[1]) return false;
  if (m[2] && !String(el.className || "").split(/\s+/).includes(m[2])) return false;
  return true;
}

function queryAll(root, sel) {
  const parts = sel.trim().split(/\s+/);
  const out = [];
  const walk = (node, index) => {
    for (const child of node.children || []) {
      if (!child.tag || child.tag === "#text") continue;
      if (matches(child, parts[index])) {
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

function install() {
  const body = new El("body");
  const document = {
    body,
    hidden: false,
    _ls: [],
    createElement: (tag) => new El(tag),
    addEventListener(type, fn, opts) {
      const rec = { type, fn };
      this._ls.push(rec);
      const signal = opts && opts.signal;
      if (!signal) return;
      const drop = () => { this._ls = this._ls.filter((r) => r !== rec); };
      if (signal.aborted) drop();
      else signal.addEventListener("abort", drop);
    },
    querySelector: () => null,
    getElementById: () => null,
  };
  body.parent = document;
  globalThis.document = document;
  globalThis.MutationObserver = class {
    constructor(cb) { this.cb = cb; }
    observe(node) { node._mos.push(this); }
    disconnect() {}
  };
  const winLs = [];
  globalThis.addEventListener = (type, fn) => { winLs.push({ type, fn }); };
  globalThis.removeEventListener = (type, fn) => {
    const i = winLs.findIndex((r) => r.type === type && r.fn === fn);
    if (i >= 0) winLs.splice(i, 1);
  };
  globalThis.window = globalThis;
  globalThis.requestAnimationFrame = (fn) => fn(0);
  return document;
}

test("more menu document listener is removed when the library view is torn down", async () => {
  const document = install();
  const { render } = await import("../web/views/library.js");
  const panel = new El("div");
  document.body.appendChild(panel);
  const root = new El("div");
  panel.appendChild(root);
  const api = async (url) => {
    if (String(url).startsWith("/api/library/status")) return { count: 0, last_scan: 1, scanning: false };
    if (String(url).startsWith("/api/library/tracks")) return { tracks: [], total: 0 };
    if (String(url).startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    return {};
  };
  await render(root, api, "music");
  const clicks = () => document._ls.filter((r) => r.type === "click");
  assert.equal(clicks().length, 1);
  root.remove();
  assert.equal(root.isConnected, false);
  assert.equal(clicks().length, 0);
});
