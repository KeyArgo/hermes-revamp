// Focus view: the controls are sized like controls. The old markup used inline
// styles (an 18px/12-36px/24px-radius pill) and .setting-row's flex:1 label,
// which squeezed each slider into the right half of its row.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../web/views/focus.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../web/app.css", import.meta.url), "utf8");

function rule(selector) {
  const i = css.indexOf(`${selector} {`);
  assert.notEqual(i, -1, `missing rule: ${selector}`);
  return css.slice(i, css.indexOf("}", i)).replace(/\/\*[\s\S]*?\*\//g, "");
}

test("the focus view carries no inline sizing", () => {
  assert.doesNotMatch(src, /style\.cssText\s*=/);
  assert.doesNotMatch(src, /border-radius:24px/);
});

test("sliders use the focus row, not the generic setting row", () => {
  assert.match(src, /className = "focus-row"/);
  assert.doesNotMatch(src, /setting-row/);
  // Label + live value share a head line; the track owns the width below it.
  assert.match(src, /focus-head/);
  assert.match(src, /focus-track/);
  assert.match(src, /focus-value/);
});

test("the track spans the row and the value reads back to the user", () => {
  assert.match(rule(".focus-track"), /grid-template-columns:\s*auto\s+minmax\(0,\s*1fr\)\s+auto/);
  assert.match(rule('.focus-track input[type="range"]'), /width:\s*100%/);
  assert.match(rule(".focus-start"), /min-height:\s*3[0-9]px/);
  assert.match(rule(".focus-start"), /flex:\s*0 0 auto/);
});

test("every timer option is a chip of equal width", () => {
  const body = rule(".focus-tabs button");
  assert.match(body, /flex:\s*1 1 0/);
  assert.match(body, /border:\s*1px solid var\(--glass-brd\)/);
  assert.match(src, /focus-tabs/);
});

test("advanced is a peer row with a disclosure marker", () => {
  assert.match(src, /className = "focus-adv"/);
  assert.match(css, /\.focus-adv > summary::before\s*\{[^}]*content:/);
});
