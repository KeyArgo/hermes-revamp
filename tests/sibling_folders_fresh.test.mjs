// The Music Studio songs-folder button writes the folder list it reads at click
// time, not the one it saw when it was drawn: the folders PUT replaces the whole
// list, so a folder added (or removed) since then must survive the click.
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
  addEventListener(type, fn) { this._ls.push({ type, fn }); }
  // Returns what the (async) listener returns, so a test can await the click.
  click() { let out; for (const l of this._ls) if (l.type === "click") out = l.fn(); return out; }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
}

function installDom() {
  globalThis.document = { createElement: (tag) => new El(tag) };
  globalThis.window = {};
}

// A folder store whose list the test can change between drawing and clicking.
function makeApi(state) {
  const puts = [];
  const api = async (url, method, body) => {
    const m = method || "GET";
    if (url === "/api/siblings") return { studio_songs_dir: "/studio/songs" };
    if (url === "/api/library/folders" && m === "GET") {
      if (state.getFails) throw new Error("offline");
      return { folders: state.folders.map((f) => ({ ...f })) };
    }
    if (url === "/api/library/folders" && m === "PUT") {
      puts.push(body);
      return {};
    }
    throw new Error(`unexpected ${m} ${url}`);
  };
  api.puts = puts;
  return api;
}

async function drawButton(state) {
  installDom();
  const { studioFolderButton } = await import("../web/views/siblings.js");
  const api = makeApi(state);
  const b = await studioFolderButton(api, () => { state.added = true; });
  assert.ok(b, "the button is offered");
  return { b, api };
}

test("a folder added after the button was drawn is kept in the PUT", async () => {
  const state = { folders: [{ path: "/music", kind: "music" }] };
  const { b, api } = await drawButton(state);
  // The library changes while the card sits on screen (Folders → add a folder).
  state.folders = [{ path: "/music", kind: "music" }, { path: "/new-albums", kind: "music" }];
  await b.click();
  assert.equal(api.puts.length, 1, "one PUT");
  assert.deepEqual(api.puts[0].folders, [
    { path: "/music", kind: "music" },
    { path: "/new-albums", kind: "music" },
    { path: "/studio/songs", kind: "music" },
  ]);
  assert.equal(state.added, true);
});

test("a folder removed after the button was drawn stays removed", async () => {
  const state = { folders: [{ path: "/music", kind: "music" }, { path: "/old", kind: "music" }] };
  const { b, api } = await drawButton(state);
  state.folders = []; // the owner emptied the library
  await b.click();
  assert.deepEqual(api.puts[0].folders, [{ path: "/studio/songs", kind: "music" }]);
});

test("a folder list that cannot be read now writes nothing", async () => {
  const state = { folders: [{ path: "/music", kind: "music" }] };
  const { b, api } = await drawButton(state);
  state.getFails = true;
  await b.click();
  assert.equal(api.puts.length, 0, "no PUT without a current list");
  assert.equal(b.disabled, false, "the button can be tried again");
});
