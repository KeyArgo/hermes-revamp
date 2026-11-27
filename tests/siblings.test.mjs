// The sibling-plugin hints: show/hide rules, dismissal persistence, the single
// host door (postMessage -> desktop -> host.navigate), and the songs-folder action.
import assert from "node:assert/strict";
import test from "node:test";

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.className = "";
    this.attrs = {};
    this.textContent = "";
    this.disabled = false;
    this._ls = [];
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  appendChild(k) { if (k && typeof k === "object") { k.parent = this; this.children.push(k); } return k; }
  append(...kids) { for (const k of kids) this.appendChild(k); }
  replaceChildren(...kids) { this.children = []; for (const k of kids) this.appendChild(k); }
  addEventListener(type, fn) { this._ls.push({ type, fn }); }
  click() { for (const l of this._ls) if (l.type === "click") l.fn(); }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  querySelector(sel) {
    const cls = sel.startsWith(".") ? sel.slice(1) : null;
    const walk = (n) => {
      for (const c of n.children || []) {
        if (cls ? String(c.className || "").split(/\s+/).includes(cls) : false) return c;
        const hit = walk(c);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this);
  }
}

function installDom({ embedded = true } = {}) {
  const posted = [];
  const document = { createElement: (tag) => new El(tag) };
  globalThis.document = document;
  const parent = embedded ? { postMessage: (msg) => posted.push(msg) } : null;
  globalThis.window = { parent };
  return { posted, document, parent };
}

const rest = (routes) => async (url, method, body) => {
  const key = `${method || "GET"} ${String(url).split("?")[0]}`;
  if (routes[key]) return routes[key](body);
  throw new Error(`unexpected ${key}`);
};

test("the only route out is the public host route, built in one place", async () => {
  const { pluginRoute, AURA_ID, STUDIO_ID } = await import("../web/views/siblings.js");
  assert.equal(pluginRoute("aura"), "/capabilities?tab=plugins&plugin=aura");
  assert.equal(pluginRoute(STUDIO_ID), "/capabilities?tab=plugins&plugin=hermes-music-studio");
  assert.equal(AURA_ID, "aura");
});

test("dismissal is persisted through saved settings and hides the card", async () => {
  const { mountHint, isDismissed, dismissalAfter } = await import("../web/views/siblings.js");
  installDom({ embedded: false });
  assert.ok(!isDismissed({}, "aura"));
  const next = dismissalAfter({ siblingHintsDismissed: { aura: 1 } }, "aura", 5000);
    assert.equal(next.aura, 5); // seconds, not ms
    assert.equal(dismissalAfter(null, "aura", 1000).aura, 1);

  const saved = [];
    const pApi = { getSettings: () => ({}), setSavedSettings: (p) => saved.push(p) };
  const holder = new El("div");
  const card = mountHint({ holder, key: "aura", name: "Aura", line: "L", pApi });
  assert.ok(card);
  const bye = holder.querySelector(".sib-dismiss");
  assert.ok(bye);
  bye.click();
  assert.equal(saved.length, 1);
  assert.equal(Number.isInteger(saved[0].siblingHintsDismissed.aura), true, "the dismissal is the map itself (unix seconds)");
  assert.equal(holder.children.length, 0, "card leaves with the dismissal");

  const pApi2 = {
    getSettings: () => ({ siblingHintsDismissed: { aura: 1 } }),
    setSavedSettings: () => {},
  };
  assert.equal(mountHint({ holder: new El("div"), key: "aura", name: "Aura", line: "L", pApi: pApi2 }), null);
});

test("a dismissed hint still answers an explicit ask (force), once per holder", async () => {
  const { mountHint } = await import("../web/views/siblings.js");
  installDom({ embedded: false });
  const pApi = {
    getSettings: () => ({ siblingHintsDismissed: { "hermes-music-studio": 1 } }),
    setSavedSettings: () => {},
  };
  const holder = new El("div");
  assert.equal(mountHint({ holder, key: "hermes-music-studio", name: "M", line: "L", pApi }), null);
  const card = mountHint({ holder, key: "hermes-music-studio", name: "M", line: "L", pApi, force: true });
  assert.ok(card);
  assert.equal(mountHint({ holder, key: "hermes-music-studio", name: "M", line: "L", pApi, force: true }), null, "never stacked");
});

test("the card's only host action is the message the desktop half listens for", async () => {
  const { buildCard, askHostForPlugin, fallbackHint } = await import("../web/views/siblings.js");
  const io = installDom({ embedded: true });
  assert.equal(askHostForPlugin("aura"), true);
  assert.deepEqual(io.posted, [{ type: "revamp:open-plugin", plugin: "aura" }]);

  const card = buildCard({ key: "aura", name: "Aura", line: "L" });
  const open = card.querySelector(".sib-open");
  assert.ok(open, "embedded: the Find button exists");
  const before = io.posted.length;
  open.click();
  assert.equal(io.posted.length, before + 1);
  assert.deepEqual(io.posted[before], { type: "revamp:open-plugin", plugin: "aura" });

  const fb = card.querySelector(".sib-fallback");
  assert.equal(fb.textContent, fallbackHint("Aura"));
  assert.equal(fb.textContent, "Search for Aura in Capabilities → Plugins");
  assert.ok(io.document.createElement, "card built from REVamp's own document only");
});

test("standalone (no host around): no Find button, the fallback text remains", async () => {
  const { buildCard } = await import("../web/views/siblings.js");
  installDom({ embedded: false });
    const card = buildCard({ key: "hermes-music-studio", name: "Music Studio", line: "L" });
  assert.equal(card.querySelector(".sib-open"), null);
  assert.equal(card.querySelector(".sib-fallback").textContent,
    "Search for Music Studio in Capabilities → Plugins");
});

test("the songs-folder action uses the existing library-folders setting, once", async () => {
  const { studioFolderButton, foldersWith } = await import("../web/views/siblings.js");
  assert.deepEqual(foldersWith([{ path: "/a", kind: "music" }, { path: "/b", kind: "books" }], "/a"),
    [{ path: "/a", kind: "music" }, { path: "/b", kind: "books" }]);
  assert.deepEqual(foldersWith([], "/s"),
    [{ path: "/s", kind: "music" }]);

  const puts = [];
  const api = rest({
    "GET /api/siblings": () => ({ studio_songs_dir: "/studio" }),
    "GET /api/library/folders": () => ({ folders: [{ path: "/a", kind: "music" }] }),
    "PUT /api/library/folders": (body) => { puts.push(body); return { ok: true }; },
  });
  const b = await studioFolderButton(api, () => {});
  assert.ok(b, "offered when the folder exists and is not in the library yet");
  b.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(puts, [{ folders: [{ path: "/a", kind: "music" }, { path: "/studio", kind: "music" }]}]);
  assert.equal(b.disabled, true);

  const none = await studioFolderButton(rest({ "GET /api/siblings": () => ({ studio_songs_dir: "" }) }), () => {});
  assert.equal(none, null, "no songs dir, no offer");
  const have = await studioFolderButton(rest({
    "GET /api/siblings": () => ({ studio_songs_dir: "/a" }),
    "GET /api/library/folders": () => ({ folders: [{ path: "/a", kind: "music" }] }),
  }), () => {});
  assert.equal(have, null, "already in the library, no offer");
  const err = await studioFolderButton(async () => { throw new Error("down"); }, () => {});
  assert.equal(err, null, "a dead server hides the action, never breaks the view");
});

test("the songs-folder button is never offered when the current folder list cannot be read", async () => {
  // The PUT replaces the whole list, so a button built from an unknown list would drop every folder.
  const { studioFolderButton } = await import("../web/views/siblings.js");
  const calls = [];
  const api = async (path, method) => {
    calls.push(`${method || "GET"} ${path}`);
    if (path === "/api/siblings") return { studio_songs_dir: "/songs" };
    throw new Error("server busy");
  };
  assert.equal(await studioFolderButton(api, () => {}), null);
  assert.ok(!calls.some((c) => c.startsWith("PUT")), calls.join(", "));
});

test("Aura copy promises nothing drawn behind Hermes (that needs a host slot that does not exist yet)", async () => {
  const { AURA_LINE, fallbackHint } = await import("../web/views/siblings.js");
  assert.doesNotMatch(AURA_LINE, /backdrop|behind|ambient/i);
  assert.equal(fallbackHint("Aura"), "Search for Aura in Capabilities → Plugins");
});
