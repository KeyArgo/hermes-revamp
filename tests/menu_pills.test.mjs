// The status-bar menu's footer (where the player lives, bug report, recovery) is pills,
// like the genre chips above it, not bare text buttons.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const between = (a, b) => plugin.slice(plugin.indexOf(a), plugin.indexOf(b, plugin.indexOf(a)));

test("location and report-bug rows render pills", () => {
  for (const body of [between("function LocationRow()", "function LocationRowWithTip()"),
    between("function ReportBugRow(", "function BarToast()")]) {
    assert.ok(body.includes("jsx(Pill"), body.slice(0, 80));
    assert.ok(!body.includes("variant: 'ghost', size: 'micro'"), "a bare text button is back");
  }
  assert.match(plugin, /className: 'revamp-chip revamp-menu-pill'/);
  assert.match(plugin, /\.revamp-menu-pills\{display:flex/);
});
