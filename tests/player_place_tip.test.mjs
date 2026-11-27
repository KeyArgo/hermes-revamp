// Dismissing the place-menu tip sends settings { place_menu_tip_seen: true }.
// The player must keep that flag in its saved settings, or a new profile (or this
// one after its own storage is cleared) shows the tip again: the desktop half
// learns the flag from the server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");

function between(src, start, end) {
  const a = src.indexOf(start);
  const b = src.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `source region found: ${start}`);
  return src.slice(a, b);
}

function settingsHarness() {
  const block = between(player, 'case "settings": {', "default: break;")
    .replace('case "settings": {', "")
    .replace(/break;\s*\}\s*$/, "");
  const saved = [], applied = [];
  const api = { setSavedSettings: (p) => saved.push(p) };
  const applyMilkdropChoice = (v) => applied.push(v);
  const hasWebGL = () => true;
  const run = new Function("cmd", "api", "applyMilkdropChoice", "hasWebGL", block + "\nreturn patch;");
  return { saved, applied, send: (cmd) => run(cmd, api, applyMilkdropChoice, hasWebGL) };
}

test("the place-menu tip flag is kept in the player's saved settings", () => {
  const h = settingsHarness();
  const patch = h.send({ place_menu_tip_seen: true });
  assert.deepEqual(patch, { place_menu_tip_seen: true });
  assert.deepEqual(h.saved, [{ place_menu_tip_seen: true }]);
});

test("only a boolean tip flag is kept, and it does not disturb the other keys", () => {
  const h = settingsHarness();
  assert.deepEqual(h.send({ place_menu_tip_seen: "yes" }), {});
  assert.deepEqual(h.send({ place_menu_tip_seen: false, duck_on_approval: false }),
    { place_menu_tip_seen: false, duck_on_approval: false });
});
