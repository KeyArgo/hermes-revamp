// Track rows behave like the album cards and artist rows: a long-press or the ⋯
// button opens the song menu (Play next, queue, playlist), and an opened album
// keeps its "← All albums" control when the list is redrawn. A failed "Load more"
// leaves the button able to try again.
import assert from "node:assert/strict";
import test from "node:test";

// ---------------------------------------------------------------- a small fake DOM
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
    this.textContent = "";
    this.hidden = false;
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
// is a no-op so the view's scan poll never fires mid-test.
const pendingTimers = new Map();
let timerSeq = 0;
function flushTimers() {
  const due = [...pendingTimers.values()];
  pendingTimers.clear();
  for (const fn of due) fn();
}

const observers = [];
function install() {
  const body = new El("body");
  const document = {
    body,
    hidden: false,
    activeElement: null,
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
  globalThis.setInterval = () => 0;
  globalThis.clearInterval = () => {};
  return document;
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };

const track = (i, extra = {}) => ({
  id: `t${i}`, title: `Song ${i}`, artist: "The Artists", album: "Great Album",
  duration: 60, disc_no: 1, track_no: i, ...extra,
});

function makeApi(log, opts = {}) {
  const api = async (url, method = "GET", body) => {
    log.push({ url: String(url), method, body });
    const u = String(url);
    if (u.startsWith("/api/library/status")) return { count: 250, last_scan: 1, scanning: false };
    if (u.startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    if (u.startsWith("/api/library/tracks")) {
      const params = new URLSearchParams(u.split("?")[1] || "");
      const offset = Number(params.get("offset") || 0);
      if (params.get("album")) {
        const all = opts.albumTracks || [track(1), track(2), track(3)];
        const start = Number(params.get("offset") || 0);
        const lim = Number(params.get("limit") || 200);
        return { tracks: all.slice(start, start + lim), total: all.length };
      }
      if (offset > 0 && opts.failMore) {
        opts.failMore = false;
        throw new Error("offline");
      }
      const count = offset === 0 ? 200 : 50;
      return { tracks: Array.from({ length: count }, (_, k) => track(offset + k)), total: 250 };
    }
    if (u.startsWith("/api/library/albums")) {
      return [{ album: "Great Album", artist: "The Artists", year: 2001, tracks: 3, art_track_id: "t1" }];
    }
    if (u.startsWith("/api/library/artists")) return [{ name: "The Artists", tracks: 3, albums: 1 }];
    if (u === "/api/playlists") return [];
    return {};
  };
  api.authedUrl = (p) => p;
  return api;
}

async function renderMusic(log, opts = {}) {
  const document = install();
  const { render } = await import("../web/views/library.js");
  const panel = new El("div");
  document.body.appendChild(panel);
  const root = new El("div");
  panel.appendChild(root);
  await render(root, makeApi(log, opts), "music");
  await settle();
  return { document, root };
}

const byText = (root, text) =>
  root.querySelectorAll("button").find((b) => String(b.textContent).trim() === text) || null;

// ------------------------------------------------------------------------- 8

test("a track row has the ⋯ button and opens the song menu for that row", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  const row = document.querySelector("#lib-list tr.trow");
  assert.ok(row, "a track row");
  const more = row.querySelector(".album-more");
  assert.ok(more, "the track row's ⋯ button");
  more.fire("click");
  await settle();
  const menu = document.querySelector(".track-menu");
  assert.ok(menu, "the menu opened");
  assert.match(menu.querySelector(".track-menu-header").textContent, /1 song/);
  const labels = menu.querySelectorAll('[role="menuitem"]').map((b) => b.textContent);
  assert.deepEqual(labels.slice(0, 4), ["Play", "Play next", "Add to queue", "Like"]);
  assert.ok(labels.includes("Add to playlist ▸"), "the playlist submenu is there too");
  // The row was selected, so the menu acts on it and not on the whole list.
  assert.ok(document.querySelector("#lib-list tr.trow").classList.contains("selected"));
});

test("a long-press on a track row opens the song menu and swallows the tap", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  const row = document.querySelector("#lib-list tr.trow");
  row.fire("touchstart", { touches: [{ clientX: 50, clientY: 70 }] });
  flushTimers();
  await settle();
  const menu = document.querySelector(".track-menu");
  assert.ok(menu, "menu opened by long-press");
  assert.match(menu.querySelector(".track-menu-header").textContent, /1 song/);
  // The tap that follows a long-press is swallowed, so the row does not also play
  // or clear the menu the press just opened.
  const end = row.fire("touchend", { touches: [] });
  assert.equal(end.defaultPrevented, true, "the tap after the long-press is swallowed");
});

// ------------------------------------------------------------------------- 7

test("an opened album keeps its way back when a row is selected", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  const tab = document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "albums");
  tab.fire("click");
  await settle();
  const card = document.querySelector(".album-card");
  card.fire("click");
  await settle();
  assert.equal(document.querySelector("#lib-list tr.trow")?.children.length >= 6, true, "album tracks listed");
  assert.ok(byText(document.querySelector("#lib-list"), "← All albums"), "back control on the album");

  document.querySelector("#lib-list tr.trow").fire("click");
  await settle();
  assert.ok(byText(document.querySelector("#lib-list"), "← All albums"),
    "the back control survives selecting a row (the list is redrawn)");
});

test("an opened album keeps its way back when Load more redraws it", async () => {
  const log = [];
  const albumTracks = Array.from({ length: 250 }, (_, k) => track(k));
  const { document } = await renderMusic(log, { albumTracks });
  const tab = document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "albums");
  tab.fire("click");
  await settle();
  document.querySelector(".album-card").fire("click");
  await settle();
  assert.ok(byText(document.querySelector("#lib-list"), "← All albums"), "back control on the album");
  const more = byText(document.querySelector("#lib-list"), "Load more (50 left)");
  assert.ok(more, "the album pages through its own tracks");
  more.fire("click");
  await settle();
  assert.ok(byText(document.querySelector("#lib-list"), "← All albums"),
    "the back control survives the Load more redraw");
});

// ------------------------------------------------------------------------- 6

test("a failed Load more leaves the button able to try again", async () => {
  const log = [];
  const { document } = await renderMusic(log, { failMore: true });
  const status = document.querySelector("#lib-status");
  const more = byText(document.querySelector("#lib-list"), "Load more (50 left)");
  assert.ok(more, "the Load more button");
  const observer = observers.at(-1);
  assert.ok(observer, "the scroll marker is watched");
  const firstObserve = observer.observes;

  more.fire("click");
  await settle();
  assert.match(status.textContent, /couldn't load more/);
  assert.equal(observer.observes, firstObserve + 1, "the observer is armed again for a retry");

  more.fire("click");
  await settle();
  const moreRequests = log.filter((c) => c.url.startsWith("/api/library/tracks?") && c.url.includes("offset=200"));
  assert.equal(moreRequests.length, 2, "the same button retried the request");
  assert.equal(status.textContent, "250 / 250", "the retry landed");
});
