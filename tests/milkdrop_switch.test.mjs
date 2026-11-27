// The MilkDrop on/off switch: one saved key (milkdropOn), surfaced in REVamp
// Settings and as a "Turn off" pill on the visualizer itself. Turning it off
// must persist AND stop the renderer (Webamp unmounts the canvas on close).
// Executes the real player closures against a fake Webamp store and DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../web/app.css", import.meta.url), "utf8");

function between(src, start, end) {
  const a = src.indexOf(start);
  const b = src.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `source region found: ${start}`);
  return src.slice(a, b);
}

// ---- the settings command: persist + really stop/start the renderer --------
function settingsHarness({ gl = true } = {}) {
  const block = between(player, 'case "settings": {', "default: break;")
    .replace('case "settings": {', "")
    .replace(/break;\s*\}\s*$/, "");
  const saved = [], applied = [];
  const api = { setSavedSettings: p => saved.push(p) };
  const applyMilkdropChoice = v => applied.push(v);
  const hasWebGL = () => gl;
  const run = new Function("cmd", "api", "applyMilkdropChoice", "hasWebGL", block + "\nreturn patch;");
  return { saved, applied, send: cmd => run(cmd, api, applyMilkdropChoice, hasWebGL) };
}

test("the switch persists milkdropOn and stops the renderer in the same command", () => {
  const h = settingsHarness();
  const patch = h.send({ milkdropOn: false });
  assert.deepEqual(patch, { milkdropOn: false });
  assert.deepEqual(h.saved, [{ milkdropOn: false }], "persisted through the existing settings store");
  assert.deepEqual(h.applied, [false], "the renderer is told to close, not just remembered");
});

test("turning it on applies only where WebGL exists; off applies everywhere", () => {
  const gpu = settingsHarness({ gl: true });
  gpu.send({ milkdropOn: true });
  assert.deepEqual(gpu.applied, [true]);
  const cpu = settingsHarness({ gl: false });
  cpu.send({ milkdropOn: true });
  assert.deepEqual(cpu.saved, [{ milkdropOn: true }], "the choice is still remembered");
  assert.deepEqual(cpu.applied, [false], "no renderer without graphics acceleration");
  cpu.send({ milkdropOn: false });
  assert.deepEqual(cpu.applied, [false, false]);
});

test("only a boolean counts, and the duck keys keep working beside it", () => {
  const h = settingsHarness();
  h.send({ milkdropOn: "yes" });
  assert.deepEqual(h.saved, [], "a non-boolean is ignored");
  assert.deepEqual(h.applied, []);
  h.send({ duck_level: 0.5, milkdropOn: false });
  assert.deepEqual(h.saved, [{ duck_level: 0.5, milkdropOn: false }], "one merged patch");
  assert.deepEqual(h.applied, [false]);
  h.send({ duck_chime: true });
  assert.deepEqual(h.applied, [false], "a duck-only change never touches the visualizer");
});

// ---- the visualizer closures: toggle + the "Turn off" pill -----------------
function visualizerHarness({ open = true } = {}) {
  const state = { windows: { genWindows: { milkdrop: { open } } } };
  const subscribers = [];
  const webamp = {
    store: {
      getState: () => state,
      dispatch: (a) => {
        if (a.type === "TOGGLE_WINDOW" && a.windowId === "milkdrop") {
          state.windows.genWindows.milkdrop.open = !state.windows.genWindows.milkdrop.open;
        }
        subscribers.forEach(f => f());
      },
      subscribe: f => subscribers.push(f),
    },
  };
  // The window element exists only while the window is open — exactly like
  // Webamp, which unmounts MilkDrop's canvas when its window closes.
  const win = {
    children: [], style: {},
    querySelector(sel) { return this.children.find(c => sel.includes(c.className)) || null; },
    appendChild(c) { this.children.push(c); },
  };
  const document = {
    querySelector(sel) {
      if (sel.includes("canvas")) return state.windows.genWindows.milkdrop.open ? { width: 275, height: 232 } : null;
      return state.windows.genWindows.milkdrop.open ? win : null;
    },
    createElement() {
      return {
        attrs: {}, listeners: {}, style: {},
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener(ev, fn) { this.listeners[ev] = fn; },
      };
    },
    addEventListener() {},
  };
  const toasts = [];
  const slice = between(player, "  api.visualizerOpen = () => milkdropOpen();", "  function suspendMilkdrop()");
  const context = {
    webamp, document, toasts, win,
    api: {},
    visualizerPlacement: "page",
    isMiniVisible: () => true,
    hasWebGL: () => true,
    toast: (text, opts) => toasts.push({ text, ...opts }),
    paintNpExtras: () => {},
    getComputedStyle: () => ({ position: "absolute" }),
    milkdropOpen: () => Boolean(state.windows.genWindows.milkdrop?.open),
    lastMilkdrop: open,
    Element: Object,
  };
  vm.createContext(context);
  vm.runInContext(slice, context);
  return { context, state, win, toasts, subscribers };
}

test("the visualizer wears a labelled Turn off pill that closes it and stops the canvas", () => {
  const h = visualizerHarness({ open: true });
  const btn = h.win.children.find(c => c.className === "revamp-md-off");
  assert.ok(btn, "paintMilkdropOff put the pill on the window at once");
  assert.equal(btn.type, "button");
  assert.equal(btn.textContent, "Turn off");
  assert.equal(btn.attrs["aria-label"], "Turn off MilkDrop visualizer");
  assert.ok(h.context.api.visualizerOpen(), "open before the click");
  assert.ok(h.context.document.querySelector("#milkdrop-window canvas"), "canvas drawing before the click");

  let stopped = false;
  btn.listeners.click({ stopPropagation() { stopped = true; } });
  assert.ok(stopped, "the click never bubbles into fullscreen or drag");
  assert.equal(h.state.windows.genWindows.milkdrop.open, false, "the window really closed");
  assert.equal(h.context.api.visualizerOpen(), false);
  assert.equal(h.context.document.querySelector("#milkdrop-window canvas"), null, "no hidden canvas keeps animating");
});

test("the pill is painted once, and returns when the window is opened again", () => {
  const h = visualizerHarness({ open: true });
  h.context.paintMilkdropOff();
  h.context.paintMilkdropOff();
  assert.equal(h.win.children.filter(c => c.className === "revamp-md-off").length, 1, "no duplicate pills");
  h.context.api.toggleVisualizer(false);
  assert.equal(h.context.document.querySelector("#milkdrop-window"), null, "closed window is unmounted");
  h.context.api.toggleVisualizer(true);
  h.context.paintMilkdropOff(); // the MutationObserver/watcher call on reopen
  assert.equal(h.win.children.filter(c => c.className === "revamp-md-off").length, 1);
});

test("a double-click on the pill never triggers fullscreen", () => {
  assert.match(player, /target\.closest\("\.revamp-md-off"\)\) return;/);
});

// ---- one key, surfaced in Settings -----------------------------------------
test("the switch is the player's own milkdropOn key, not a second setting", () => {
  assert.match(player, /const milkdropSetting = \(\) => "milkdropOn"/);
  // The window watcher still remembers manual opens/closes with the same key.
  assert.match(player, /if \(!milkdropSuspended && !autoMilkdrop\) api\.setSavedSettings\?\.\(\{ \[milkdropSetting\(\)\]: open \}\)/);
  // The automatic toggle from Settings must not be re-saved by the watcher.
  const settingsBlock = between(player, 'case "settings": {', "default: break;");
  assert.ok(settingsBlock.indexOf("api.setSavedSettings(patch)") < settingsBlock.indexOf("applyMilkdropChoice("), "persist first, then apply");
  assert.match(player, /const applyMilkdropChoice[\s\S]*?autoMilkdrop = true/);
});

test("REVamp Settings leads with the MilkDrop switch and its CPU/GPU help line", () => {
  const comp = between(plugin, "function MilkdropSettings() {", "\nfunction RevampSettings()");
  assert.match(comp, /live\?\.settings/, "reads the player's saved settings");
  assert.match(comp, /command\('settings', \{ milkdropOn: want \}\)/, "writes through the existing settings command");
  assert.match(comp, /'aria-pressed': on/);
  assert.match(comp, /'aria-pressed': !on/);
  assert.match(comp, /role: 'group'/);
  assert.match(plugin, /milkdropTitle: 'MilkDrop visualizer'/);
  assert.match(plugin, /milkdropHelp: '[^']*saves CPU\/GPU/);
  const settings = between(plugin, "function RevampSettings() {", "\n// Volume dips");
  const switchAt = settings.indexOf("jsx(MilkdropSettings, {})");
  const locationAt = settings.indexOf("t('location')");
  assert.ok(switchAt >= 0 && switchAt < locationAt, "the switch is at the top of the section");
});

test("the Turn off pill is styled: Aura geometry, visible over the picture, focus ring", () => {
  const rule = between(css, ".revamp-md-off {", "\n.revamp-md-off:hover");
  assert.match(rule, /position: absolute/);
  assert.match(rule, /border-radius: 999px/);
  assert.match(rule, /height: 22px/);
  assert.match(rule, /z-index: 30/);
  assert.match(css, /\.revamp-md-off:focus-visible \{ outline: 2px solid #ffbf8f; outline-offset: 2px; \}/);
  // Bottom-right: Webamp's own close button lives in the title bar, top-right.
  assert.match(rule, /right: 6px; bottom: 6px/);
});
