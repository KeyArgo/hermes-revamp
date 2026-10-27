// Shell chrome must survive a short window: the left rail scrolls (and drops its
// now-playing card) instead of being clipped, and the bottom ticker keeps a floor
// and a ceiling instead of being squeezed to a sliver by the grid.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../web/app.css", import.meta.url), "utf8");

function rule(selector) {
  const i = css.indexOf(`${selector} {`);
  assert.notEqual(i, -1, `missing rule: ${selector}`);
  const j = css.indexOf("}", i);
  // Comments name the old declarations they replaced; keep them out of the match.
  return css.slice(i, j).replace(/\/\*[\s\S]*?\*\//g, "");
}

test("the sidebar scrolls rather than clipping its nav", () => {
  const body = rule(".sidebar");
  assert.match(body, /overflow-y:\s*auto/);
  assert.match(body, /overflow-x:\s*hidden/);
});

test("a short window drops the now-playing card instead of pushing the nav out", () => {
  const i = css.indexOf("@media (max-height: 620px)");
  assert.notEqual(i, -1, "missing the short-window query");
  assert.match(css.slice(i, css.indexOf("}", css.indexOf("{", i))), /\.nowplaying-card\s*\{\s*display:\s*none/);
  // It has to come after the card's own display:flex, or it loses the cascade.
  assert.ok(css.indexOf("@media (max-height: 620px)") > css.indexOf(".nowplaying-card { flex-wrap: wrap; }"));
});

test("the bottom ticker has a floor and a ceiling, and scrolls past the ceiling", () => {
  const body = rule(".ticker");
  assert.match(body, /min-height:/);
  assert.match(body, /max-height:/);
  assert.match(body, /overflow-y:\s*auto/);
  // `overflow: hidden` is what zeroed the grid item's automatic minimum size.
  assert.doesNotMatch(body, /overflow:\s*hidden/);
});
