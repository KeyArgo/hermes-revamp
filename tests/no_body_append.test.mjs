// REVamp's desktop half must never append to the host page's document.body:
// everything it draws lives in nodes Hermes gives the plugin (its registered
// panes / slots / routes). The hidden dock parks in the status bar's holder;
// the pop-out is a Hermes floating pane. Reads of document.body (size, etc.)
// are fine — writes are not.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

test("desktop/plugin.js contains no append/insert onto document.body", () => {
  for (const call of [
    "document.body.append",
    "document.body.appendChild",
    "document.body.insertBefore",
    "document.body.prepend",
  ]) {
    assert.ok(!src.includes(call), `found ${call} — REVamp must live inside Hermes's own nodes, not the page body`);
  }
  // Whitespace-tolerant sweep, so reformatting cannot smuggle one back in.
  assert.doesNotMatch(
    src,
    /document\.body\s*\.\s*(append|appendChild|insertBefore|prepend|insertAdjacentElement|insertAdjacentHTML|replaceChildren)\b/,
    "no write of any kind onto document.body"
  );
});

test("the hidden dock parks in the status bar's holder, never the body", () => {
  assert.match(src, /let barDockHolder = null/, "the bar's holder is the dock's home");
  assert.match(src, /if \(barDockHolder && barDockHolder\.isConnected\) moveInto\(barDockHolder, dock\)/);
  assert.match(src, /else dock\.remove\(\)/, "no holder yet: the iframes stay detached, never on the body");
  // RevampBar renders the zero-size, overflow-hidden, aria-hidden holder.
  const bar = src.slice(src.indexOf("function RevampBar()"), src.indexOf("function PaneHeaderButtons()"));
  assert.match(bar, /ref: dockRef/);
  assert.match(bar, /'aria-hidden': true, style: \{ position: 'absolute', width: 0, height: 0, overflow: 'hidden'/);
});

test("the pop-out is a Hermes floating pane, not a raw div on the page", () => {
  const pane = src.slice(src.indexOf("function floatingPlayerPane()"), src.indexOf("// The floating pane exists only"));
  assert.match(pane, /area: PANES_AREA/);
  assert.match(pane, /placement: 'floating'/);
  assert.match(pane, /lifecycleKeepAlive: true/, "the iframe stays mounted while the card is collapsed");
  assert.match(src, /function FloatPane\(\)/, "the pane body is a registered render, not a body append");
  assert.match(src, /engine\?\.showFloat\(el\)/);
});
