// "Find Music Studio in Plugins" and "Find Aura in Plugins" on the library page
// (the control frame) must reach the host. The control frame's messages used to
// return before the revamp:open-plugin branch, so the buttons did nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

function between(source, start, end) {
  const a = source.indexOf(start);
  assert.notEqual(a, -1, start);
  const b = source.indexOf(end, a);
  assert.notEqual(b, -1, end);
  return source.slice(a, b);
}

test("the control frame's open-plugin message is handled before it returns", () => {
  const block = between(plugin, "if (e.source === controlFrame.contentWindow) {",
    "if (e.source !== frame.contentWindow) return");
  const open = block.indexOf("revamp:open-plugin");
  assert.notEqual(open, -1, "the control frame handles revamp:open-plugin");
  // The allowlist and the source check still gate it.
  assert.match(block, /SIBLING_PLUGIN_IDS\.includes\(e\.data\.plugin\)/);
  assert.match(block, /host\.navigate\(`\/capabilities\?tab=plugins&plugin=/);
  // It sits inside the control-frame branch, before its return (nothing below it
  // sees the control frame's messages).
  const ret = block.lastIndexOf("return");
  assert.ok(open < ret, "handled before the branch returns");
});
