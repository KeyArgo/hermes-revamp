import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { bandEdges, freshState, paletteFrom, step, MINIVIS_FPS, MINIVIS_SOFTWARE_FPS } from "../web/minivis.mjs";

test("bands are log-spaced, strictly increasing and inside the analyser", () => {
  const e = bandEdges(512, 44100);
  assert.equal(e.length, 20);
  for (let i = 1; i < e.length; i++) assert.ok(e[i] > e[i - 1], `edge ${i}`);
  assert.ok(e[0] >= 1 && e.at(-1) <= 512);
  // log spacing: the low half of the bands covers far fewer bins than the high half
  assert.ok(e[10] - e[0] < e[19] - e[10]);
});

test("bars rise fast and fall slowly", () => {
  const s = freshState(1);
  step(s, Float32Array.of(1), 1 / 30, 0);
  const up = s.level[0];
  assert.ok(up > 0.5, "attack");
  step(s, Float32Array.of(0), 1 / 30, 33);
  assert.ok(s.level[0] > up * 0.8, "release is gentle");
});

test("peak caps hold, then fall under gravity but never below the bar", () => {
  const s = freshState(1);
  for (let t = 0; t < 10; t++) step(s, Float32Array.of(1), 1 / 30, t * 33);
  const top = s.peak[0];
  step(s, Float32Array.of(0), 1 / 30, 400);
  assert.equal(s.peak[0], top, "held");
  let now = 400;
  for (let t = 0; t < 60; t++) { now += 33; step(s, Float32Array.of(0), 1 / 30, now); }
  assert.ok(s.peak[0] < top, "fell");
  assert.ok(s.peak[0] >= s.level[0], "never below the bar");
});

test("skin colours come from the viscolor slots, with Winamp defaults", () => {
  const skin = Array.from({ length: 24 }, (_, i) => `rgb(${i},${i},${i})`);
  const p = paletteFrom(skin);
  assert.deepEqual([p.top, p.mid, p.bottom, p.peak], ["rgb(2,2,2)", "rgb(9,9,9)", "rgb(17,17,17)", "rgb(23,23,23)"]);
  // "line" picks from all 16 spectrum slots, top (2) to bottom (17).
  assert.equal(p.spectrum.length, 16);
  assert.equal(p.spectrum[0], "rgb(2,2,2)");
  assert.equal(p.spectrum[15], "rgb(17,17,17)");
  assert.equal(paletteFrom(null).bottom, "rgb(24,132,8)");
});

test("frame budgets stay light", () => {
  assert.equal(MINIVIS_FPS, 30);
  assert.equal(MINIVIS_SOFTWARE_FPS, 12);
});

test("the analyzer honours the saved style and peak choice", async () => {
  const { readFileSync } = await import("node:fs");
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const vis = readFileSync(new URL("../web/minivis.mjs", import.meta.url), "utf8");
  assert.match(vis, /if \(style\.peaks === false\) return;/);
  assert.match(vis, /coloring === "fire" \|\| style\.coloring === "line"/);
  // Caps are on unless the user explicitly saved peaks: false.
  assert.match(player, /peaks: typeof explicit === "boolean" \? explicit : true/);
  // Hidden by the host or in Mini: the loop stops instead of drawing offscreen.
  assert.match(player, /isVisible: \(\) => visualizerPlacement !== "mini" && hostSaysVisible !== false/);
});

test("re-attaching the same analyzer does not force a layout read", () => {
  // attach() runs on every Webamp DOM change; place() reads computed style and layout.
  const src = readFileSync(new URL("../web/minivis.mjs", import.meta.url), "utf8");
  const attach = src.slice(src.indexOf("function attach("), src.indexOf("function detach("));
  assert.match(attach, /visCanvas === target && canvas\?\.isConnected\)\) return;/);
  assert.match(src, /new ResizeObserver\(\(\) => place\(\)\)/);
});
