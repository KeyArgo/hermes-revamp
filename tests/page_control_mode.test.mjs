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
  assert.match(control, /setView\("library"\)/);
  assert.doesNotMatch(control, /initPlayer\(/);
  assert.match(plugin, /controlFrame\.src = `\$\{url\}\/\?embed=1&control=1`/);
});
