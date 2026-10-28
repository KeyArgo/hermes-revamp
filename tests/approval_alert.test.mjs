import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
const store = readFileSync(new URL("../revamp_core/store.py", import.meta.url), "utf8");

function between(src, start, end) {
  const from = src.indexOf(start);
  assert.notEqual(from, -1, `missing: ${start}`);
  const to = src.indexOf(end, from);
  assert.notEqual(to, -1, `missing: ${end}`);
  return src.slice(from, to);
}

test("an actionable approval makes one short, automatically restored alert", () => {
  const body = between(player, "function duckTo(", "function unduck(");
  assert.match(body, /playApprovalChime\(\)/);
  assert.match(body, /setTimeout\(unduck, dipDuration\(\)\)/);
  assert.doesNotMatch(body, /120000/);
});

test("approval alert is silent while paused and suppresses duplicate or old events", () => {
  const body = between(player, "function duckTo(", "// ---- commands from SSE");
  assert.match(body, /!== "PLAYING"\) return/);
  assert.match(body, /seenApprovalIds/);
  assert.match(body, /approvalIsFresh/);
  assert.match(player, /if \(bulk\) \{[^}]*without alert side effects/s);
});

test("approval alert defaults and settings keep independent setting keys", () => {
  assert.match(store, /"duck_on_approval": False/);
  assert.match(store, /"duck_chime": True/);
  assert.match(store, /"duck_dip_seconds": 2/);
  assert.match(player, /cmd\.duck_chime/);
  assert.match(player, /cmd\.duck_dip_seconds/);
});
