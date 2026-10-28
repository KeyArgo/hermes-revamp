// The EQ window is optional: closing it must close the gap it leaves, and its
// presets menu must stay inside the stack. Both are source contracts here
// because the behaviour itself needs a browser (see tools/probes/probe_eq_gap.mjs).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../web/app.css", import.meta.url), "utf8");

function between(src, start, end) {
  const a = src.indexOf(start);
  assert.notEqual(a, -1, `missing: ${start}`);
  const b = src.indexOf(end, a);
  assert.notEqual(b, -1, `missing: ${end}`);
  return src.slice(a, b);
}

test("placeClassicStack measures the stack in every home, not just the desktop one", () => {
  const body = between(player, "function placeClassicStack()", "function ensureClassicOpen()");
  // The old hardcoded y=232 stayed when the EQ closed.
  assert.match(body, /getElementById\("main-window"\)/);
  assert.match(body, /getElementById\("equalizer-window"\)/);
  assert.doesNotMatch(body, /visualizerPlacement === "desktop"\) \{/);
});

test("a closed EQ re-places the stack instead of leaving the gap", () => {
  assert.match(player, /genWindows\.equalizer\?\.open/);
  const body = between(player, "function eqOpen()", "// Live streams report an infinite duration");
  assert.match(body, /placeClassicStack\(\)/);
});

test("the presets menu is pulled back inside the stage", () => {
  const body = between(app, "function fitContextMenus()", "function watchContextMenus()");
  assert.match(body, /marginLeft/);
  assert.match(body, /FLIP_CLASS/);
  // The submenu is wider than the menu row: flipping has to consider it.
  assert.match(body, /submenuBox\(menu\)/);
  assert.match(app, /webamp-context-menu/);
});

test("a flipped menu opens its submenus to the left", () => {
  assert.match(css, /\.context-menu\.revamp-flip ul\s*\{[^}]*right:\s*100%/);
  assert.match(css, /\.context-menu\.revamp-flip ul\s*\{[^}]*left:\s*auto/);
});
