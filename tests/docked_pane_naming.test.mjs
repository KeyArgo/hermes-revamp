// The docked player's constant used to be a research spike; the diagnostics used
// to read a host global for its version. Both are now named/behaved for what ships.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

test("the docked pane constant is named for what it is", () => {
  assert.match(src, /const DOCKED_PANE = true/);
  assert.ok(!src.includes("PANE_SPIKE"), "the spike name is gone");
  assert.ok(!src.includes("Research spike"), "the stale spike comment is gone");
});

test("the diagnostics report 'unknown' instead of reading a host global", () => {
  assert.ok(!src.includes("__HERMES_APP_VERSION__"));
  assert.match(src, /const hermesVersion = typeof host\?\.appVersion === 'string' \? host\.appVersion : 'unknown'/);
});
