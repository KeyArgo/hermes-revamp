// Hunt 3: Escape on a station's menu gave focus back to the first row whose name merely
// contained this station's name ("Smooth Jazz FM" for "Jazz FM"), so Enter then played
// the wrong station. The menu must return focus to the row it was opened from.
import assert from "node:assert/strict";
import test from "node:test";
import { El, install, settle } from "./library_fake_dom.mjs";

const station = (id, name) => ({ id, name, url: `https://example.test/${id}`, tags: [], favicon: "", source: "radiobrowser" });

test("Escape on a station menu returns focus to that station's row", async () => {
  const document = install();
  globalThis.revampPlayerApi = { loadTracks: async () => {} };
  const api = async (url) => {
    const u = String(url);
    if (u === "/api/radio/favorites") return [];
    if (u.startsWith("/api/radio/popular")) return [station("a", "Smooth Jazz FM"), station("b", "Jazz FM")];
    return [];
  };
  api.authedUrl = (p) => p;
  const { render } = await import("../web/views/radio.js");
  const root = new El("div");
  document.body.appendChild(root);
  await render(root, api);
  await settle();
  const rows = document.querySelectorAll(".radio-row");
  assert.deepEqual(rows.map((r) => r.querySelector(".name").textContent), ["Smooth Jazz FM", "Jazz FM"]);
  rows[1].focus();
  rows[1].fire("keydown", { key: "F10", shiftKey: true });
  assert.ok(document.querySelector(".track-menu"), "the station menu opened");
  const esc = { key: "Escape", preventDefault() {} };
  for (const l of document._ls.filter((r) => r.type === "keydown")) l.fn(esc);
  assert.equal(document.querySelector(".track-menu"), null, "the menu closed");
  assert.equal(document.activeElement === rows[0] ? "Smooth Jazz FM" : document.activeElement === rows[1] ? "Jazz FM" : "elsewhere", "Jazz FM");
});
