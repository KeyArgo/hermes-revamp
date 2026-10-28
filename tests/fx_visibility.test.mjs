// Theme effects default off, and the canvas loop stops whenever the page is not
// actually on screen. Fake timers and a tiny DOM — no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pending = new Map();
let rafSeq = 1;
function installClock() {
  globalThis.requestAnimationFrame = (fn) => {
    const id = rafSeq++;
    pending.set(id, fn);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => pending.delete(id);
}
function flush(times = 4) {
  let ran = 0;
  for (let i = 0; i < times; i++) {
    const batch = [...pending.entries()];
    pending.clear();
    if (!batch.length) break;
    for (const [, fn] of batch) {
      ran += 1;
      fn(32 * (i + 1));
    }
  }
  return ran;
}

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.id = "";
    this.className = "";
    this.attrs = {};
    this._ls = [];
    this._mo = [];
    this.style = {};
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
    this.width = 0;
    this.height = 0;
  }
  get classList() {
    const el = this;
    return {
      contains: (c) => el.className.split(/\s+/).includes(c),
      add: (c) => {
        if (!el.className.split(/\s+/).includes(c)) el.className = `${el.className} ${c}`.trim();
        for (const mo of el._mo) mo.cb([]);
      },
      remove: (c) => {
        el.className = el.className.split(/\s+/).filter((x) => x && x !== c).join(" ");
        for (const mo of el._mo) mo.cb([]);
      },
      toggle: (c, force) => {
        const on = force === undefined ? !el.className.split(/\s+/).includes(c) : force;
        on ? el.classList.add(c) : el.classList.remove(c);
        return on;
      },
    };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return this.attrs[k] ?? (k === "id" ? this.id : null); }
  append(...kids) { for (const k of kids) this.appendChild(k); }
  appendChild(k) { if (typeof k === "string") return k; k.parent = this; this.children.push(k); return k; }
  prepend(k) { k.parent = this; this.children.unshift(k); return k; }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  get isConnected() {
    let n = this;
    while (n) { if (n === document.body || n === document) return true; n = n.parent; }
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
  dispatch(type, event) {
    for (const rec of [...this._ls]) if (rec.type === type) rec.fn(event);
  }
  getContext() {
    const proxy = new Proxy(() => proxy, { get: () => proxy });
    return proxy;
  }
}
class IO {
  constructor(cb) { this.cb = cb; IO.latest = this; }
  observe() {}
  unobserve() {}
  disconnect() {}
}
class MO {
  constructor(cb) { this.cb = cb; }
  observe(node) { node._mo.push(this); }
  disconnect() {}
}

const store = new Map();
function installDom() {
  const body = new El("body");
  const root = new El("html");
  root.style = { setProperty() {}, removeProperty() {}, cssText: "" };
  const document = {
    body, documentElement: root, hidden: false, _ls: [],
    createElement: (tag) => new El(tag),
    dispatch(type, event) { for (const rec of this._ls) if (rec.type === type) rec.fn(event); },
    querySelector(sel) {
      let found = null;
      const walk = (el) => {
        if (found || !el || el === document) return;
        const cls = String(el.className || "").split(/\s+/);
        if (sel.startsWith(".") && cls.includes(sel.slice(1))) { found = el; return; }
        if (sel.startsWith("#") && el.id === sel.slice(1)) { found = el; return; }
        for (const c of el.children || []) walk(c);
      };
      walk(body);
      return found;
    },
  };
  // Honor AbortSignal on document listeners.
  document.addEventListener = (type, fn, opts) => {
    const rec = { type, fn };
    document._ls.push(rec);
    const signal = opts && opts.signal;
    if (!signal) return;
    const drop = () => { document._ls = document._ls.filter((r) => r !== rec); };
    if (signal.aborted) drop();
    else signal.addEventListener("abort", drop);
  };
  globalThis.document = document;
  globalThis.IntersectionObserver = IO;
  globalThis.MutationObserver = MO;
  globalThis.innerWidth = 800;
  globalThis.innerHeight = 600;
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  globalThis.window = globalThis;
  globalThis.parent = globalThis;
  globalThis.addEventListener = (type, fn) => {
    window._winLs = window._winLs || [];
    window._winLs.push({ type, fn });
  };
  globalThis.dispatchWindow = (type, event) => {
    for (const rec of window._winLs || []) if (rec.type === type) rec.fn(event);
  };
  body.parent = document;
}

installClock();
installDom();

test.describe("theme fx visibility", { concurrency: 1 }, () => {
  let setFx;
  let fxShown;
  test.before(async () => {
    const fx = await import("../web/fx.js");
    setFx = fx.setFx;
    fxShown = fx.fxShown;
  });

  test("new users start with theme effects off", async () => {
    store.clear();
    const before = pending.size;
    const theme = await import("../web/theme.js?fresh=1");
    assert.equal(theme.shrinkDataUrl ? "exported" : "", "exported");
    assert.equal(pending.size, before);
    assert.equal(document.documentElement.dataset.revampFx, undefined);
    const src = readFileSync(new URL("../web/theme.js", import.meta.url), "utf8");
    assert.match(src, /const DEFAULTS = \{[^}]*fx:\s*"off"/);
  });

  test("a saved effect still runs for someone who already chose one", async () => {
    store.set("revamp.theme.v1", JSON.stringify({ fx: "embers", fxLevel: 1 }));
    await import("../web/theme.js?saved=1");
    assert.equal(document.documentElement.dataset.revampFx, "embers");
    assert.ok(pending.size > 0);
  });

  test("fx loop stops when the document is hidden and resumes when shown", async () => {
    setFx("off");
    pending.clear();
    setFx("embers", 1);
    assert.equal(flush(1), 1);
    assert.ok(pending.size > 0);
    document.hidden = true;
    document.dispatch("visibilitychange", {});
    assert.equal(pending.size, 0);
    assert.equal(flush(2), 0);
    document.hidden = false;
    document.dispatch("visibilitychange", {});
    assert.ok(pending.size > 0);
    assert.ok(flush(1) >= 1);
  });

  test("fx loop stops when the canvas is off-screen or zero-size and resumes when it is back", () => {
    setFx("off");
    setFx("embers", 1);
    flush(1);
    IO.latest.cb([{ isIntersecting: false, boundingClientRect: { width: 0, height: 0 } }]);
    assert.equal(pending.size, 0);
    assert.equal(flush(2), 0);
    IO.latest.cb([{ isIntersecting: true, boundingClientRect: { width: 400, height: 300 } }]);
    assert.ok(pending.size > 0);
  });

  test("fx loop stops in winamp-only mode and when the host parks the frame", () => {
    setFx("off");
    document.body.classList.remove("mode-winamp");
    setFx("embers", 1);
    flush(1);
    dispatchWindow("message", { data: { type: "revamp:mode", mode: "winamp" } });
    assert.equal(fxShown(), false);
    assert.equal(pending.size, 0);
    dispatchWindow("message", { data: { type: "revamp:mode", mode: "full" } });
    assert.equal(fxShown(), true);
    assert.ok(pending.size > 0);
    dispatchWindow("message", { data: { type: "revamp:visible", visible: false } });
    assert.equal(fxShown(), false);
    assert.equal(pending.size, 0);
    dispatchWindow("message", { data: { type: "revamp:visible", visible: true } });
    assert.equal(fxShown(), true);
    document.body.classList.add("mode-winamp");
    assert.equal(fxShown(), false);
    document.body.classList.remove("mode-winamp");
    // modeOverride is still "full" from the host message, so the class is not the only signal.
    dispatchWindow("message", { data: { type: "revamp:mode", mode: "winamp" } });
    assert.equal(fxShown(), false);
    setFx("off");
    assert.equal(pending.size, 0);
  });

  test("background data urls shrink under 1400000 characters", async () => {
    const theme = await import("../web/theme.js?fresh=1");
    const url = theme.shrinkDataUrl((edge, quality) => "x".repeat(Math.floor(edge * quality * 2000)));
    assert.equal(typeof url, "string");
    assert.ok(url.length < 1_400_000);
    assert.equal(theme.shrinkDataUrl(() => "x".repeat(2_000_000)), null);
  });

  test("an image that cannot fit raises a toast instead of being dropped", async () => {
    const theme = await import("../web/theme.js?fresh=1");
    store.delete("revamp.theme.bg.v1");
    assert.equal(theme.takeBackgroundUrl("x".repeat(1_400_000)), false);
    assert.equal(store.has("revamp.theme.bg.v1"), false);
    const texts = [];
    const walk = (el) => { if (el?.textContent) texts.push(el.textContent); for (const c of el?.children || []) walk(c); };
    walk(document.body);
    assert.ok(texts.some((t) => /too large/i.test(t)));
    assert.equal(theme.takeBackgroundUrl("data:image/jpeg;base64,abc"), true);
    assert.equal(store.get("revamp.theme.bg.v1"), "data:image/jpeg;base64,abc");
  });
});
