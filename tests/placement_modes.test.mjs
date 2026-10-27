import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

function between(start, end) {
  const a = src.indexOf(start);
  assert.notEqual(a, -1, start);
  const b = src.indexOf(end, a);
  assert.notEqual(b, -1, end);
  return src.slice(a, b);
}

test("the location menu names are the five the user asked for", () => {
  for (const [key, label] of [
    ["locMini", "Mini player"],
    ["locDocked", "Frame"],
    ["locFloating", "Pop into Hermes"],
    ["locDesktop", "Pop out to desktop"],
    ["locFull", "Full screen"],
  ]) {
    assert.match(src, new RegExp(`${key}: '${label}'`));
  }
});

test("placeItems offers them in order, with the frame side in the name", () => {
  const body = between("function placeItems(", "function LocationRow(");
  const order = [...body.matchAll(/item\('(\w+)'|extra\('(\w+)'/g)].map((m) => m[1] || m[2]);
  assert.deepEqual(order, ["mini", "docked", "floating", "desktop", "full"]);
  assert.ok(body.includes("item('docked', 'locDocked', ` (${side})`)"));
});

test("placementLocation never rewrites a choice, including docked on a narrow window", () => {
  const body = between("function placementLocation()", "const LOC_BUS");
  assert.match(body, /return loc/);
  assert.doesNotMatch(body, /mini|DOCKED_MIN_WIDTH|innerWidth/);
});

test("choosing the frame reveals the pane instead of leaving it dismissed", () => {
  const body = between("function setPlayerLocation(", "function dockedPlayerPane(");
  assert.match(body, /loc === 'docked'/);
  assert.match(body, /host\.revealPane\?\.\(scopedPaneId\(\$dockedPlayerSide\.get\(\)\)\)/);
});
