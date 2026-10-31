// Hermes shows the player in iframes sandboxed without allow-modals: window.confirm()
// returns false at once there (alert/prompt are ignored), so a guarded action silently
// never runs. Regression: "Clear queue" could not clear. Use confirmDialog() from
// web/views/modal.js instead.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "web");
const files = (d) => readdirSync(d).flatMap((f) => {
  const p = join(d, f);
  if (statSync(p).isDirectory()) return f === "vendor" ? [] : files(p);
  return p.endsWith(".js") ? [p] : [];
});

test("the web player never calls a browser modal dialog", () => {
  const hits = [];
  for (const f of files(WEB)) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      const code = line.replace(/\/\/.*$/, "");
      if (/(^|[^.\w$])(window\.)?(confirm|alert|prompt)\s*\(/.test(code)) hits.push(`${f}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});

test("player chrome is never part of a select-all copy", () => {
  // Copying the whole Hermes screen picked up the track name, transport words and the
  // braille waveform. Player UI opts out of selection; text fields stay selectable.
  const css = readFileSync(join(WEB, "app.css"), "utf8");
  assert.match(css, /body \{ -webkit-user-select: none; user-select: none; \}/);
  assert.match(css, /input, textarea, \[contenteditable="true"\] \{ -webkit-user-select: text; user-select: text; \}/);
  const plugin = readFileSync(join(WEB, "..", "desktop", "plugin.js"), "utf8");
  assert.match(plugin, /\[class\^="revamp-"\],\[class\*=" revamp-"\]\{-webkit-user-select:none;user-select:none\}/);
});
