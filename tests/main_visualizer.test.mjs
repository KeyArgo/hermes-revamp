import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  MAIN_VIS_DEFAULTS,
  MAIN_VIS_FALLOFF,
  MAIN_VIS_PEAK_FALLOFF,
  mainVisFalloff,
  mainVisModePatch,
  mainVisPeakFalloff,
  mainVisPrefs,
} from "../web/main-vis.mjs";

const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
const bundle = readFileSync(new URL("../web/vendor/webamp.butterchurn-bundle.min.mjs", import.meta.url), "utf8");

test("calm defaults are thick bands, no peaks, and the slowest Webamp falloff", () => {
  assert.deepEqual(mainVisPrefs({}), MAIN_VIS_DEFAULTS);
  assert.deepEqual(mainVisPrefs(undefined), MAIN_VIS_DEFAULTS);
  assert.equal(MAIN_VIS_DEFAULTS.bandwidth, "wide");
  assert.equal(MAIN_VIS_DEFAULTS.peaks, false);
  assert.equal(MAIN_VIS_DEFAULTS.coloring, "fire");
  assert.equal(MAIN_VIS_DEFAULTS.saFalloff, "slower");
  assert.equal(MAIN_VIS_DEFAULTS.mode, "BAR");
  assert.equal(mainVisFalloff(MAIN_VIS_DEFAULTS), 3);
  assert.equal(mainVisPeakFalloff(MAIN_VIS_DEFAULTS), MAIN_VIS_PEAK_FALLOFF.slow);
  assert.equal(MAIN_VIS_FALLOFF.moderate, 12);
});

test("a saved choice overrides one field and leaves the others calm", () => {
  const prefs = mainVisPrefs({
    duck_on_approval: false,
    mainVisualizer: { mode: "OSCILLOSCOPE", peaks: true, saFalloff: "nope", bandwidth: "thin" },
  });
  assert.deepEqual(prefs, {
    mode: "OSCILLOSCOPE",
    renderer: "revamp",
    bandwidth: "thin",
    coloring: "fire",
    peaks: true,
    saFalloff: "slower",
    saPeakFalloff: "slow",
  });
});

test("remembering a mode merges into the analyzer object and does not touch other settings", () => {
  const settings = {
    duck_on_approval: false,
    duck_level: 0.5,
    ticker: true,
    mainVisualizer: { mode: "BAR", peaks: true, saFalloff: "fast", note: "keep" },
  };
  assert.equal(mainVisModePatch(settings, "BAR"), null);
  assert.equal(mainVisModePatch(settings, "sideways"), null);
  const patch = mainVisModePatch(settings, "NONE");
  assert.deepEqual(patch, {
    mainVisualizer: { mode: "NONE", peaks: true, saFalloff: "fast", note: "keep" },
  });
  const merged = { ...settings, ...patch };
  assert.equal(merged.duck_on_approval, false);
  assert.equal(merged.duck_level, 0.5);
  assert.equal(merged.ticker, true);
  assert.equal(merged.mainVisualizer.peaks, true);
  assert.equal(merged.mainVisualizer.note, "keep");
});

test("the player applies those prefs and persists a click by merging settings", () => {
  assert.match(player, /from "\.\/main-vis\.js"/);
  assert.match(player, /alignMainVisMode\(webamp, saved\.settings\)/);
  assert.match(player, /const patch = mainVisModePatch\(saved\.settings, MAIN_VIS_MODES\[style\] \|\| "BAR"\)/);
  assert.match(player, /if \(patch\) api\.setSavedSettings\?\.\(patch\)/);
  assert.match(player, /api\.setSavedSettings = \(s\) => \{ saved\.settings = \{ \.\.\.saved\.settings, \.\.\.s \}; stagePersist\(\{ settings: saved\.settings \}\); \}/);
  assert.doesNotMatch(player, /settings:\s*\{\s*mainVisualizer/);
  assert.match(player, /classList\.toggle\("revamp-idle", status !== "PLAYING"\)/);
  assert.match(player, /ctx\.clearRect\(0, 0, canvas\.width, canvas\.height\)/);
});

test("the vendored analyzer follows the user's mode and starts calm", () => {
  // MilkDrop still asks whether its own window is the selected visualizer.
  assert.match(bundle, /const a=zq\(sy\)/);
  // The small analyzer must not: that selector turns it off while MilkDrop is open.
  // Tf reads the display slice; handed the whole store it returned no mode and the
  // analyzer drew nothing at all (regression), so it must be given state.display.
  assert.match(bundle, /const t=zq\(ay\),n=zq\(e=>Tf\(e\.display\)\)/);
  assert.doesNotMatch(bundle, /n=zq\(Tf\)/);
  assert.doesNotMatch(bundle, /n=zq\(sy\)/);
  assert.match(bundle, /bandwidth:"wide",coloring:"normal",peaks:!1,saFalloff:"slower",saPeakFalloff:"slow"/);
  assert.doesNotMatch(bundle, /peaks:!0,saFalloff:"moderate"/);
});

test("bar coloring is a saved choice limited to Webamp's three paints", () => {
  assert.equal(mainVisPrefs({ mainVisualizer: { coloring: "line" } }).coloring, "line");
  assert.equal(mainVisPrefs({ mainVisualizer: { coloring: "normal" } }).coloring, "normal");
  assert.equal(mainVisPrefs({ mainVisualizer: { coloring: "rainbow" } }).coloring, "fire");
  // Webamp binds its paint in the constructor, so the player must rebind it.
  assert.match(player, /inst\.paintBar = paint\.bind\(inst\)/);
});

test("under software WebGL the analyzer draws on a frame budget", () => {
  // Every analyzer frame made Hermes repaint its whole window on the CPU (several cores
  // while playing). The vendored loop skips drawing until the budget allows a frame.
  assert.match(bundle, /const f=window\.__revampVisFps;if\(f&&x-l<1e3\/f-2\)return;l=x,e\.drawImage\(d,0,0\),g\.paintFrame\(\)/);
  assert.match(player, /if \(softwareGL\) window\.__revampVisFps = SOFTWARE_ANALYZER_FPS/);
});
