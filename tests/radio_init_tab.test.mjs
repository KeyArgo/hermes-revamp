// Hunt 3: Radio's start-up waits for the favorites list and then always loaded Popular
// (or Hermes Radio). A tab clicked during that wait had already loaded its own list,
// which was then replaced by Popular while the tab bar still said "Favorites".
import assert from "node:assert/strict";
import test from "node:test";
import { El, install, settle } from "./library_fake_dom.mjs";

const fav = { id: "f1", name: "My Station", url: "https://example.test/s", tags: [], favicon: "" };

test("a tab picked while Radio is still starting keeps its own list", async () => {
  const document = install();
  globalThis.revampPlayerApi = { loadTracks: async () => {} };
  const log = [];
  let releaseFirst;
  const firstFavs = new Promise((r) => { releaseFirst = r; });
  let favCalls = 0;
  const api = async (url) => {
    const u = String(url);
    log.push(u);
    if (u === "/api/radio/favorites") return ++favCalls === 1 ? firstFavs : [fav];
    if (u.startsWith("/api/radio/popular")) return [{ id: "p1", name: "Popular One", url: "https://example.test/p", source: "radiobrowser" }];
    return [];
  };
  api.authedUrl = (p) => p;
  const { render } = await import("../web/views/radio.js");
  const root = new El("div");
  document.body.appendChild(root);
  const done = render(root, api);
  await settle();
  document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "favorites").fire("click");
  await settle();
  releaseFirst([fav]);
  await done;
  await settle();
  const names = document.querySelectorAll(".radio-row .name").map((n) => n.textContent);
  assert.deepEqual(names, ["My Station"]);
  assert.equal(log.filter((u) => u.startsWith("/api/radio/popular")).length, 0, "Popular was never asked for");
});
