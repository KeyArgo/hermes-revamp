import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Incident 11-02: pages kept a stale key after the server behind them was replaced; every click
// failed with "invalid or missing token" (51 notifications). A reload by itself would cut off the
// music playing in the page (council review), so the page shows ONE reconnect bar instead.
const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");

test("a 401 from the API raises the reconnect notice", () => {
  const api = app.slice(app.indexOf("async function api("), app.indexOf("export const apiGet"));
  assert.match(api, /if \(res\.status === 401 && msg === KEY_REFUSED\) keyRejected\(\);/, "only the server's own key refusal");
  assert.match(app, /const KEY_REFUSED = "invalid or missing token";/);
});

test("the notice appears once, never reloads by itself, and reloads only on a click", () => {
  const src = app.slice(app.indexOf("let keyNoticeShown"), app.indexOf("async function api("));
  const appended = [];
  let reloads = 0;
  const makeEl = () => ({ setAttribute() {}, append(child) { this._button = child; }, addEventListener(t, f) { this.click = f; } });
  const document = { createElement: makeEl, body: { append: (el) => appended.push(el) } };
  const location = { reload: () => { reloads++; } };
  const posted = [];
  const window = { parent: { postMessage: (m) => posted.push(m) } };
  const fn = new Function("document", "location", "window", `${src}; return keyRejected;`)(document, location, window);
  for (let i = 0; i < 51; i++) fn();
  assert.equal(appended.length, 1, "one notice, not one per failed call");
  assert.equal(reloads, 0, "never reloads on its own (that would stop the music)");
  const button = appended[0]._button;
  assert.ok(button && typeof button.click === "function", "the bar has a Reconnect button");
  button.click();
  assert.equal(reloads, 1, "Reconnect reloads, once");
  assert.deepEqual(posted, [{ type: "revamp:key-rejected" }], "the host is told once (the bar may be clipped)");
});

test("MilkDrop: old per-place choices carry over; automatic toggles never overwrite the choice", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /const legacy = LEGACY_MILKDROP\[visualizerPlacement\];\s*if \(legacy && has\(legacy\)\) return saved\.settings\[legacy\] === true;/);
  assert.match(player, /if \(!milkdropSuspended && !autoMilkdrop\) api\.setSavedSettings/);
  assert.equal((player.match(/applyMilkdropChoice\(/g) || []).length, 3, "start-up + new place + resume");
  assert.doesNotMatch(player, /visualizerOpen\(\) !== wantOpen\) api\.toggleVisualizer/);
  // Full screen counts only while the browser really is full screen (a refused request must
  // not freeze the desktop window's layout).
  assert.match(player, /api\.visualizerFullscreen = \(\) => Boolean\(document\.fullscreenElement\);/);
});

test("MilkDrop migration: the shared key wins, then the place's old key, then the page's old key", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /const LEGACY_MILKDROP = \{ page: "milkdropOpen", docked: "milkdropOpenDocked"/);
  const shared = player.indexOf('if (has("milkdropOn")) return saved.settings.milkdropOn === true;');
  const place = player.indexOf("if (legacy && has(legacy)) return saved.settings[legacy] === true;");
  const page = player.indexOf('if (has("milkdropOpen")) return saved.settings.milkdropOpen === true;');
  assert.ok(shared > 0 && shared < place && place < page, "fallback order: shared, place, page");
  assert.match(player, /if \(milkdropSuspended && want\) return;/, "never reopened out of sight by a late placement step");
});

test("a bridge key mismatch only tells the host; the page's one notice is kept for its own key", () => {
  assert.match(app, /if \(!sameKey\(d\.key\)\) return reply\(\{ ok: false, status: 401, error: "bridge key", bridgeKey: true \}\);/);
});
