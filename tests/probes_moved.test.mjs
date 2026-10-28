// Manual browser probes live under tools/probes, not next to the automated tests.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const NAMES = [
  "probe_embed.mjs",
  "probe_eq_gap.mjs",
  "probe_eq_menu.mjs",
  "probe_focus.mjs",
  "probe_focus_behaviour.mjs",
  "probe_layout.mjs",
  "evidence_eq.mjs",
];

test("probe scripts moved to tools/probes", () => {
  for (const name of NAMES) {
    const moved = new URL(`../tools/probes/${name}`, import.meta.url);
    const stale = new URL(`./${name}`, import.meta.url);
    assert.equal(existsSync(moved), true, name);
    assert.equal(existsSync(stale), false, `${name} still in tests/`);
    const text = readFileSync(moved, "utf8");
    assert.match(text, /new URL\("\.\.\/\.\."/);
  }
});
