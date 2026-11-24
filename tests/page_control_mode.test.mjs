import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");

function between(source, start, end) {
  const a = source.indexOf(start);
  assert.notEqual(a, -1, start);
  const b = source.indexOf(end, a);
  assert.notEqual(b, -1, end);
  return source.slice(a, b);
}

test("page control mode keeps Frame, Pop into Hermes, and desktop players in place", () => {
  const body = between(plugin, "function place() {", "function sendControlPlace()");
  assert.match(body, /const controlPage = pageHost && \(parked \|\| !\$pageBorrow\.get\(\)\)/);
  assert.match(body, /const loc = placementLocation\(\)/);
  assert.match(body, /loc === 'docked' && paneHost/);
  assert.match(body, /loc === 'floating'/);
  assert.match(body, /if \(parked\)/);
});

test("only the explicit control-shell button borrows the live player", () => {
  assert.match(plugin, /e\.data\.type === 'revamp:bring-player-here'/);
  assert.match(plugin, /\$pageBorrow\.set\(true\)/);
  assert.match(app, /button\.textContent = "Bring the player here"/);
  assert.match(app, /type: "revamp:bring-player-here"/);
});

test("Full screen remains an intentional borrow even from Frame or Pop-out", () => {
  const items = between(plugin, "function placeItems(", "function LocationRow(");
  assert.match(items, /pageBorrowRequested = true; \$open\?\.set\?\.\(false\); host\.navigate\(ROUTE\)/);
  const engine = between(plugin, "showPage(el) {", "hidePage(el)");
  assert.match(engine, /pageBorrowRequested \|\| placementLocation\(\) === 'mini'/);
});

test("control page is a full shell that never starts Webamp audio", () => {
  const boot = between(app, "async function boot()", "boot().catch");
  const control = between(boot, "if (CONTROL) {", "applyLayoutClasses();\n  if (matchMedia");
  assert.match(control, /buildNav\(\)/);
  assert.match(control, /showStartView\(\)/);
  assert.doesNotMatch(control, /initPlayer\(/);
  assert.match(plugin, /controlFrame\.src = `\$\{url\}\/\?embed=1&control=1`/);
});

test("reload refreshes the library control frame as well as the player", () => {
  const reload = plugin.slice(plugin.indexOf("    reload() {"), plugin.indexOf("\n", plugin.indexOf("    reload() {")));
  assert.match(reload, /frame\.src = /);
  assert.match(reload, /controlFrame\.src = `\$\{url\}\/\?embed=1&control=1`/);
});

test("a reload waits for the new page's handshake, not the old one", () => {
  // Recovery used to report success at once: the first page's ready stayed latched.
  for (const fn of ["reload() {", "park() {", "unpark() {"]) {
    const line = plugin.slice(plugin.indexOf(fn), plugin.indexOf("\n", plugin.indexOf(fn)));
    assert.match(line, /sawRevamp = false/, fn);
  }
  // A timed-out wait is removed, so a later handshake cannot resolve a stale promise.
  const ready = between(plugin, "whenReady(ms) {", "readyWaiters.push(waiter)");
  assert.match(ready, /readyWaiters\.splice\(i, 1\)/);
});

test("the bar waveform slows and rests in the background under software rendering", () => {
  // Under SwiftShader each waveform frame repainted Hermes' window on the CPU.
  const wave = between(plugin, "function Waveform(", "const updateMotion");
  assert.match(wave, /fps: hostRendersInSoftware\(\) \? SOFTWARE_WAVE_FPS : WAVE_FPS/);
  assert.match(wave, /pauseWhenUnfocused: hostRendersInSoftware\(\)/);
  assert.match(plugin, /const SOFTWARE_WAVE_FPS = 6/);
});

// Hunt 3: in the library control frame (the player sits in Frame, Pop-out or Desktop),
// Settings showed defaults and silently dropped every change, and "Not now" on a hint was
// never saved: the stand-in player api had no getSettings / setSavedSettings.
function fnBody(source, sig) {
  const a = source.indexOf(sig);
  assert.notEqual(a, -1, sig);
  let depth = 0;
  for (let i = source.indexOf("{", a + sig.length - 1); i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(a, i + 1);
  }
  throw new Error("unbalanced");
}

function controlApi(state) {
  const posts = [];
  const win = {};
  const install = new Function("window", "apiPost", "apiGet",
    `${fnBody(app, "function installControlPlayerApi() {")}\ninstallControlPlayerApi();`);
  install(win, async (path, body) => { posts.push([path, body]); return { ok: true }; },
    async (path) => { assert.equal(path, "/api/state"); return state; });
  return { pApi: win.revampPlayerApi, posts };
}

test("the control stand-in reads the live player's saved settings and sends changes to it", async () => {
  const { pApi, posts } = controlApi({ settings: { duck_on_approval: true, ticker: false } });
  assert.equal(typeof pApi.getSettings, "function");
  await pApi.refreshSettings();
  assert.deepEqual(pApi.getSettings(), { duck_on_approval: true, ticker: false });
  await pApi.setSavedSettings({ duck_on_approval: false });
  assert.deepEqual(posts, [["/api/command", { action: "settings", duck_on_approval: false }]]);
  assert.equal(pApi.getSettings().duck_on_approval, false, "the next render shows the change");
  // Boot waits for the saved settings before the first view renders.
  const boot = between(app, "async function boot()", "boot().catch");
  const control = between(boot, "if (CONTROL) {", "showStartView();");
  assert.match(control, /await window\.revampPlayerApi\.refreshSettings\(\)/);
});

test("the live player accepts the ticker and hint dismissals sent from the control frame", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const block = between(player, 'case "settings": {', "        default: break;");
  const saved = [];
  const run = new Function("cmd", "api", "applyMilkdropChoice", "hasWebGL",
    `switch (cmd.action) { ${block} }`);
  const api = { setSavedSettings: (p) => saved.push(p) };
  run({ action: "settings", ticker: false }, api, () => {}, () => true);
  run({ action: "settings", siblingHintsDismissed: { "hermes-music-studio": 1700000000 } }, api, () => {}, () => true);
  run({ action: "settings", siblingHintsDismissed: { aura: "soon" } }, api, () => {}, () => true);
  run({ action: "settings", siblingHintsDismissed: "x" }, api, () => {}, () => true);
  assert.deepEqual(saved, [{ ticker: false }, { siblingHintsDismissed: { "hermes-music-studio": 1700000000 } }]);
});
