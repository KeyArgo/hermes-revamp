// The pop-out is a Hermes floating pane. The host's own header drags the card,
// so REVamp's strip must not reach into host markup, replay pointer events or
// add a raw window listener of its own.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

test("the floating strip does not query host markup or dispatch synthetic events", () => {
  assert.ok(!src.includes("[data-floating-pane]"), "no host pane markup is queried");
  assert.ok(!src.includes("new PointerEvent("), "no synthetic pointer event is dispatched");
  assert.ok(!src.includes("window.addEventListener("), "no raw listener on the host window");
});

test("the strip is a plain label row that keeps its buttons", () => {
  const block = src.slice(
    src.indexOf("function buildOverlay(ctx)"),
    src.indexOf("// Only the window holding the 'revamp:engine' lock")
  );
  assert.match(block, /mkBtn\('⤢'/);
  assert.match(block, /mkBtn\('⇲'/);
  assert.match(block, /mkBtn\('✕'/);
  assert.ok(!block.includes("pointerdown"), "no drag handling of its own");
  // The classic title bars still stream drags to the host; they are heard, not acted on.
  assert.match(block, /const dragFromPage = \(\) => \{\}/);
});
