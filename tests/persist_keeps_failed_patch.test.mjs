// A state save that fails (the server restarting, a dropped connection) used to throw the
// pending patch away: a setting, skin or EQ change looked saved and came back on reload.
// The failed patch must ride along with the next save, and an empty patch sends nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
const start = src.indexOf("  let persistState = {};");
const end = src.indexOf("\n", src.indexOf("  const stagePersist ="));
assert.ok(start > 0 && end > start, "the save block is where it was");
const block = src.slice(start, end);

function load(apiPut) {
  // persist() is the debounced save; here it does nothing, the test flushes by hand.
  return new Function("apiPut", "persist", `${block}\nreturn { persistNow, stagePersist };`)(apiPut, () => {});
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test("a failed save keeps its patch for the next save", async () => {
  const sent = [];
  let fail = true;
  const { persistNow, stagePersist } = load((url, body) => {
    sent.push(JSON.parse(JSON.stringify(body)));
    return fail ? Promise.reject(new Error("server down")) : Promise.resolve({});
  });
  stagePersist({ settings: { duck_chime: false } });
  persistNow();
  await tick();
  fail = false;
  stagePersist({ volume: 40 });
  persistNow();
  await tick();
  assert.equal(sent.length, 2);
  assert.deepEqual(sent[1], { settings: { duck_chime: false }, volume: 40 });
});

test("a newer change wins over the same key from a failed save", async () => {
  const sent = [];
  let fail = true;
  const { persistNow, stagePersist } = load((url, body) => {
    sent.push(JSON.parse(JSON.stringify(body)));
    return fail ? Promise.reject(new Error("server down")) : Promise.resolve({});
  });
  stagePersist({ skin: "old" });
  persistNow();
  stagePersist({ skin: "new" }); // changed again before the failure came back
  await tick();
  fail = false;
  persistNow();
  await tick();
  assert.equal(sent.at(-1).skin, "new");
});

test("nothing to save sends nothing", async () => {
  const sent = [];
  const { persistNow } = load((url, body) => { sent.push(body); return Promise.resolve({}); });
  persistNow();
  await tick();
  assert.equal(sent.length, 0);
});

test("a patch the server refused (400) is not sent again with later saves", async () => {
  const sent = [];
  let refuse = true;
  const { persistNow, stagePersist } = load((url, body) => {
    sent.push(JSON.parse(JSON.stringify(body)));
    return refuse ? Promise.reject(Object.assign(new Error("API /api/state: invalid"), { status: 400 })) : Promise.resolve({});
  });
  stagePersist({ volume: "loud" });
  persistNow();
  await tick();
  refuse = false;
  stagePersist({ volume: 40 });
  persistNow();
  await tick();
  assert.deepEqual(sent.at(-1), { volume: 40 });
  stagePersist({ skin: "x" });
  persistNow();
  await tick();
  assert.deepEqual(sent.at(-1), { skin: "x" });
});
