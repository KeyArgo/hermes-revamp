// Album and artist context menus: the same menu as a track row, acting on every track of the
// album or artist (disc/track order), with Play next (mode "next"), Add to queue (mode
// "append") and Add to playlist ▸ (saved playlists + New playlist…).
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
  // Fire an event on this element and let it bubble to its ancestors and the document.
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
  const notM = /:not\(\[([\w-]+)\]\)/.exec(rest);
  if (notM) {
    const have = notM[1].startsWith("data-") ? el.dataset[notM[1].slice(5)] : el.attrs[notM[1]];
    if (have !== undefined) return false;
    rest = rest.replace(notM[0], "");
  }
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

// Timers: setTimeout is a queue the test flushes (long-press, debounce, the menu's grace
// period); setInterval is a no-op so the view's scan poll never fires mid-test.
const pendingTimers = new Map();
let timerSeq = 0;

function flushTimers() {
  const due = [...pendingTimers.values()];
  pendingTimers.clear();
  for (const fn of due) fn();
}

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
const settle = async () => { for (let i = 0; i < 6; i += 1) await tick(); };

function byText(root, text) {
  return root.querySelectorAll("button").find((b) => String(b.textContent).trim() === text) || null;
}

const ALBUM_TRACKS = [
  { id: "t01", title: "Intro", artist: "The Artists", album: "Great Album", duration: 30, disc_no: 1, track_no: 1 },
  { id: "t02", title: "Second", artist: "The Artists", album: "Great Album", duration: 31, disc_no: 1, track_no: 2 },
  { id: "t03", title: "Third", artist: "The Artists", album: "Great Album", duration: 32, disc_no: 2, track_no: 1 },
  { id: "t04", title: "Fourth", artist: "The Artists", album: "Great Album", duration: 33, disc_no: 2, track_no: 2 },
];

function makeApi(log, overrides = {}) {
  const api = async (url, method = "GET", body) => {
    log.push({ url: String(url), method, body });
    const u = String(url);
    if (overrides[u]) return overrides[u];
    if (u.startsWith("/api/library/status")) return { count: 4, last_scan: 1, scanning: false };
    if (u.startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    if (u.startsWith("/api/library/tracks")) {
      const params = new URLSearchParams(u.split("?")[1] || "");
      if (params.get("album")) {
        return { tracks: ALBUM_TRACKS, total: ALBUM_TRACKS.length };
      }
      if (params.get("artist")) {
        return { tracks: ALBUM_TRACKS, total: ALBUM_TRACKS.length };
      }
      return { tracks: [], total: 0 };
    }
    if (u.startsWith("/api/library/albums")) {
      return [{ album: "Great Album", artist: "The Artists", year: 2001, tracks: 4 }];
    }
    if (u.startsWith("/api/library/artists")) {
      return [{ name: "The Artists", tracks: 4, albums: 1 }];
    }
    if (u === "/api/playlists") return [{ id: "p1", name: "Road trip", count: 2 }];
    if (u === "/api/playlists/p1") return { id: "p1", name: "Road trip", items: [{ kind: "track", title: "Old", url: "/api/media/old" }] };
    return {};
  };
  api.authedUrl = (p) => p;
  return api;
}

async function renderMusic(log) {
  const document = install();
  const { render } = await import("../web/views/library.js");
  const panel = new El("div");
  document.body.appendChild(panel);
  const root = new El("div");
  panel.appendChild(root);
  await render(root, makeApi(log), "music");
  await settle();
  return { document, root };
}

function openAlbumsTab(document) {
  const tab = document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "albums");
  assert.ok(tab, "Albums tab");
  tab.fire("click");
}

async function openAlbumMenu(log) {
  const { document, root } = await renderMusic(log);
  openAlbumsTab(document);
  await settle();
  const card = document.querySelector(".album-card");
  assert.ok(card, "album card");
  card.fire("contextmenu", { clientX: 40, clientY: 60 });
  await settle();
  const menu = document.querySelector(".track-menu");
  assert.ok(menu, "album context menu opened");
  return { document, root, card, menu };
}

test("right-clicking an album opens the shared menu with Play, Play next, Add to queue and Add to playlist", async () => {
  const log = [];
  const { menu } = await openAlbumMenu(log);
  const labels = menu.querySelectorAll('[role="menuitem"]').map((b) => b.textContent);
  assert.deepEqual(labels.slice(0, 4), ["Play", "Play next", "Add to queue", "Add to playlist ▸"]);
  assert.equal(menu.getAttribute("role"), "menu");
  assert.match(menu.querySelector(".track-menu-header").textContent, /Great Album · 4 tracks/);
  // The menu fetched the album in disc/track order, not the default artist sort.
  const fetch = log.find((c) => c.url.startsWith("/api/library/tracks?") && c.url.includes("album="));
  assert.ok(fetch, "album tracks requested");
  assert.match(fetch.url, /sort=album/);
  assert.match(fetch.url, /album=Great\+Album/);
  assert.match(fetch.url, /artist=The\+Artists/);
});

test("Play next queues the album in disc/track order with mode next", async () => {
  const log = [];
  const calls = [];
  globalThis.revampPlayerApi = {
    loadTracks: (items, o) => calls.push({ kind: "load", items, mode: o?.mode }),
    playItemsAt: (i, items) => calls.push({ kind: "playAt", index: i, items }),
  };
  const { menu } = await openAlbumMenu(log);
  byText(menu, "Play next").fire("click");
  await settle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].mode, "next");
  assert.deepEqual(calls[0].items.map((i) => i.track_id), ["t01", "t02", "t03", "t04"]);
  assert.equal(calls[0].items[0].album, "Great Album");
  assert.equal(globalThis.document.querySelector(".track-menu"), null, "menu closed after the pick");
});

test("Add to queue appends the album tracks", async () => {
  const log = [];
  const calls = [];
  globalThis.revampPlayerApi = {
    loadTracks: (items, o) => calls.push({ items, mode: o?.mode }),
    playItemsAt: () => {},
  };
  const { menu } = await openAlbumMenu(log);
  byText(menu, "Add to queue").fire("click");
  await settle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].mode, "append");
  assert.equal(calls[0].items.length, 4);
  assert.deepEqual(calls[0].items.map((i) => i.track_id), ["t01", "t02", "t03", "t04"]);
});

test("Play starts the album at its first track", async () => {
  const log = [];
  const calls = [];
  globalThis.revampPlayerApi = {
    loadTracks: () => {},
    playItemsAt: (i, items) => calls.push({ index: i, items }),
  };
  const { menu } = await openAlbumMenu(log);
  byText(menu, "Play").fire("click");
  await settle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].index, 0);
  assert.equal(calls[0].items.length, 4);
});

test("Add to playlist lists saved playlists plus New playlist and appends to the chosen one", async () => {
  const log = [];
  globalThis.revampPlayerApi = { loadTracks: () => {}, playItemsAt: () => {} };
  const { document, menu } = await openAlbumMenu(log);
  byText(menu, "Add to playlist ▸").fire("click");
  await settle();
  const sub = menu.querySelector(".track-menu-sub");
  assert.ok(sub, "submenu");
  assert.equal(sub.hidden, false);
  const items = sub.querySelectorAll('[role="menuitem"]').map((b) => b.textContent);
  assert.deepEqual(items, ["Road trip", "＋ New playlist…"]);

  byText(sub, "Road trip").fire("click");
  await settle();
  const put = log.find((c) => c.method === "PUT" && c.url === "/api/playlists/p1");
  assert.ok(put, "playlist PUT");
  assert.equal(put.body.items.length, 5, "existing item plus the album");
  assert.deepEqual(put.body.items.slice(1).map((i) => i.track_id), ["t01", "t02", "t03", "t04"]);
  const toastText = document.querySelector(".toast-host").children.at(-1).textContent;
  assert.equal(toastText, "Added 4 tracks to “Road trip”");
});

test("New playlist creates one with the album tracks", async () => {
  const log = [];
  globalThis.revampPlayerApi = { loadTracks: () => {}, playItemsAt: () => {} };
  const { document, menu } = await openAlbumMenu(log);
  byText(menu, "Add to playlist ▸").fire("click");
  await settle();
  byText(menu.querySelector(".track-menu-sub"), "＋ New playlist…").fire("click");
  await settle();
  const input = document.querySelector(".modal input");
  assert.ok(input, "name prompt");
  input.value = "Road trip";
  input.fire("keydown", { key: "Enter" });
  await settle();
  const post = log.find((c) => c.method === "POST" && c.url === "/api/playlists");
  assert.ok(post, "playlist POST");
  assert.equal(post.body.name, "Road trip");
  assert.equal(post.body.items.length, 4);
  const toastText = document.querySelector(".toast-host").children.at(-1).textContent;
  assert.equal(toastText, "Created “Road trip” with 4 tracks");
});

test("the ⋯ button opens the album menu without opening the album", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  openAlbumsTab(document);
  await settle();
  const card = document.querySelector(".album-card");
  const more = card.querySelector(".album-more");
  assert.ok(more, "album ⋯ button");
  more.fire("click");
  await settle();
  assert.ok(document.querySelector(".track-menu"), "menu opened");
  assert.ok(document.querySelector(".album-card"), "still on the album grid");
});

test("a long-press opens the album menu and swallows the click that follows", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  openAlbumsTab(document);
  await settle();
  const card = document.querySelector(".album-card");
  card.fire("touchstart", { touches: [{ clientX: 50, clientY: 70 }] });
  flushTimers();
  await settle();
  assert.ok(document.querySelector(".track-menu"), "menu opened by long-press");
  card.fire("click");
  await settle();
  assert.ok(document.querySelector(".album-card"), "the tap did not open the album");
});

test("Shift+F10 and the context-menu key open the album menu", async () => {
  const log = [];
  const { document } = await renderMusic(log);
  openAlbumsTab(document);
  await settle();
  const card = document.querySelector(".album-card");
  card.fire("keydown", { key: "F10", shiftKey: true });
  await settle();
  assert.ok(document.querySelector(".track-menu"), "menu opened with Shift+F10");
  document.querySelector(".track-menu").fire("keydown", { key: "Escape" });
  await settle();
  assert.equal(document.querySelector(".track-menu"), null);
  card.fire("keydown", { key: "ContextMenu" });
  await settle();
  assert.ok(document.querySelector(".track-menu"), "menu opened with the context-menu key");
});

test("Escape closes the menu and returns focus to the album card", async () => {
  const log = [];
  const { document, card } = await openAlbumMenu(log);
  document.activeElement = document.querySelector(".track-menu [role='menuitem']");
  document.activeElement.fire("keydown", { key: "Escape" });
  await settle();
  assert.equal(document.querySelector(".track-menu"), null);
  assert.equal(document.activeElement, card);
});

test("an artist row opens the same menu and queues all their tracks in album order", async () => {
  const log = [];
  const calls = [];
  globalThis.revampPlayerApi = {
    loadTracks: (items, o) => calls.push({ items, mode: o?.mode }),
    playItemsAt: () => {},
  };
  const { document } = await renderMusic(log);
  const tab = document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "artists");
  tab.fire("click");
  await settle();
  const row = document.querySelector("#lib-list tr");
  assert.ok(row, "artist row");
  row.fire("contextmenu", { clientX: 40, clientY: 60 });
  await settle();
  const menu = document.querySelector(".track-menu");
  assert.ok(menu, "artist menu");
  assert.match(menu.querySelector(".track-menu-header").textContent, /The Artists · 4 tracks/);
  const fetch = log.find((c) => c.url.startsWith("/api/library/tracks?") && c.url.includes("artist="));
  assert.ok(fetch, "artist tracks requested");
  assert.match(fetch.url, /sort=album/);
  byText(menu, "Play next").fire("click");
  await settle();
  assert.equal(calls[0].mode, "next");
  assert.deepEqual(calls[0].items.map((i) => i.track_id), ["t01", "t02", "t03", "t04"]);
  // The artist row also carries a ⋯ button for the same menu.
  assert.ok(document.querySelector("#lib-list tr .album-more"), "artist ⋯ button");
});
