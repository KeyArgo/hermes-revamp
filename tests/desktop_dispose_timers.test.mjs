// The control frame's "reload after a refused key" timer could fire up to 60 s after the
// plugin was disabled or reloaded and reload a library frame the new instance does not own.
// remove() (the ctx.onDispose handler) must cancel it, like reload() already does.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const start = src.indexOf("  function remove() {");
const end = src.indexOf("  ctx.onDispose(remove)");
assert.ok(start > 0 && end > start, "remove() is where it was");
const removeSrc = src.slice(start, end);

test("disposing the engine cancels a pending control-frame reload", () => {
  const timers = new Map();
  let next = 1;
  const setTimeout = (fn, ms) => { const id = next++; timers.set(id, fn); return id; };
  const clearTimeout = (id) => { timers.delete(id); };
  const reloads = [];
  const run = new Function("setTimeout", "clearTimeout", "reloadControl", `
    let bridge = null, bridgeApi = {}, paneWatch = null, paneArea = null, paneHost = null, paneClip = null;
    let scaleTimer = 0, refitPane = null, floatHost = null;
    const dock = { remove() {} }, overlay = { root: { remove() {} } };
    const dropPending = () => {}, syncFloatingPane = () => {};
    let controlRetry = setTimeout(reloadControl, 60000); // armed by a second refused key
    ${removeSrc}
    remove();
    return controlRetry;`);
  const left = run(setTimeout, clearTimeout, () => reloads.push("reload"));
  for (const fn of timers.values()) fn(); // a minute later
  assert.deepEqual(reloads, [], "no reload after dispose");
  assert.equal(left, 0);
});
