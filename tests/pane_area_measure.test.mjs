// The docked pane must be measured and observed on REVamp's own holder, which fills
// the space the pane gave it — never on a host container through el.parentElement.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

test("nothing reads or observes a parent element", () => {
  assert.ok(!src.includes("el.parentElement"), "the holder's parent is not read");
  assert.ok(!src.includes("paneHost?.parentElement"), "the clip's parent is not read");
  assert.doesNotMatch(src, /\.observe\([^)]*parentElement/, "nothing observes a parent element");
});

test("the pane scale is measured on REVamp's own holder", () => {
  assert.match(src, /const area = paneArea/);
  assert.match(src, /paneWatch\.observe\(el\)/);
});

test("the holder fills the space it is given and the clip box is REVamp's own", () => {
  const pane = src.slice(src.indexOf("function ClassicPaneSpike()"), src.indexOf("function FloatPane()"));
  assert.match(pane, /width: '100%', height: '100%', flex: 'none'/);
  assert.match(src, /paneClip\.style\.cssText = 'position:relative;overflow:hidden;flex:none;margin:6px auto 0'/);
  assert.match(src, /moveInto\(paneHost, frame\)/);
});
