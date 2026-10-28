// Frame scaling fills the pane. Crisp whole-number scaling stays an explicit opt-in.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const block = src.match(/function frameScale\(fit, crisp\) \{[\s\S]*?\n\}/);
const frameScale = new Function(`${block[0]}\nreturn frameScale;`)();

test("fractional scale fills the frame down to 0.6", () => {
  assert.equal(frameScale(1.25, false), 1.25);
  assert.equal(frameScale(1.2344, false), 1.234);
  assert.equal(frameScale(0.4, false), 0.6);
  assert.equal(frameScale(5, false), 4);
  assert.equal(frameScale(Number.NaN, false), 0.6);
  assert.equal(frameScale(0, false), 0.6);
});

test("crisp whole-number scaling snaps to 1..4", () => {
  assert.equal(frameScale(1.25, true), 1);
  assert.equal(frameScale(2.9, true), 2);
  assert.equal(frameScale(0.4, true), 1);
  assert.equal(frameScale(5, true), 4);
  assert.equal(frameScale(Number.NaN, true), 1);
});

test("scaled iframe stays pixelated and disposal clears the pane watcher", () => {
  assert.match(src, /image-rendering:\s*pixelated/);
  assert.match(src, /imageRendering\s*=\s*['"]pixelated['"]/);
  const remove = src.slice(src.indexOf("function remove()"), src.indexOf("ctx.onDispose(remove)"));
  assert.match(remove, /paneWatch\?\.disconnect\(\)/);
  assert.match(remove, /clearTimeout\(scaleTimer\)/);
  assert.match(remove, /scaleTimer\s*=\s*0/);
});

test("crisp whole-number scaling is off by default and settings merge", () => {
  assert.match(src, /Crisp whole-number scaling/);
  assert.match(src, /\$crispScale = atom\(false\)/);
  assert.match(src, /savedUi\.crispScale === true/);
  assert.match(src, /const next = \{ \.\.\.readUiSettings\(\), \.\.\.patch \}/);
});
