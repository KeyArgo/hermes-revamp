// Hunt 3: after create, rename, delete or import, the Playlists view refreshed twice at
// once (its own refresh plus the server's "playlists" event). Both runs cleared the list
// and then both appended a full set, so every playlist showed twice.
import assert from "node:assert/strict";
import test from "node:test";
import { El, install, settle } from "./library_fake_dom.mjs";

const winListeners = [];

async function renderPlaylists() {
  const document = install();
  document.contains = (n) => document.body.contains(n);
  globalThis.addEventListener = (type, fn) => winListeners.push({ type, fn });
  globalThis.revampPlayerApi = { loadTracks: async () => {}, getSettings: () => ({}) };
  const tick = () => new Promise((r) => setImmediate(r));
  const api = async (url) => {
    await tick(); // every answer takes a moment, so two refreshes overlap
    if (url === "/api/playlists") return [{ id: "p1", name: "Road trip", count: 2, duration: 300 }];
    if (url === "/api/likes") return { tracks: [] };
    return {};
  };
  api.authedUrl = (p) => p;
  const { render } = await import("../web/views/playlists.js");
  const root = new El("div");
  document.body.appendChild(root);
  await render(root, api);
  await settle();
  return { document, root };
}

const names = (root) => root.querySelectorAll(".card-row .name").map((n) => n.textContent);

test("two overlapping refreshes leave one Liked row and one row per playlist", async () => {
  const { root } = await renderPlaylists();
  assert.deepEqual(names(root), ["♥ Liked", "Road trip"]);
  const changed = winListeners.filter((l) => l.type === "revamp:playlists-changed");
  assert.ok(changed.length, "the view listens for playlist changes");
  for (const l of changed) l.fn();
  for (const l of changed) l.fn();
  await settle(); await settle();
  assert.deepEqual(names(root), ["♥ Liked", "Road trip"]);
});
