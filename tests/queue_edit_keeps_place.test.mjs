import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Hunt 3: "Add to queue" or "Play next" while a book was resuming made the book start
// from 0:00 and lose its saved place. Appending is a pure queue edit: it plays nothing,
// so it must not count as the person's own move (actSeq) or drop a handed-over place.
const src = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");

function methodSource(sig) {
  const a = src.indexOf(sig);
  assert.notEqual(a, -1, sig);
  let depth = 0;
  for (let i = a + sig.length - 1; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(a, i + 1);
  }
  throw new Error("unbalanced");
}

function harness() {
  const body = methodSource("    async loadTracks(tracks, { mode = \"replace\", play = true } = {}) {").trim();
  const make = new Function("hooks", `
    let actSeq = 0, place = { track: "t1", pos: 4320 }, startPending = true, playedTid = "b1";
    let activeBackend = "webamp", savedQueue = [], replacedAt = 0, nextEntry = 0;
    const backends = new Map([["focus", { load: async (items, o) => hooks.focusLoads.push(o) }]]);
    const normalizeItems = (xs) => xs.map((x) => ({ ...x, playback: x.playback || { backend: "webamp" } }));
    const isWebampItem = (x) => x.playback.backend === "webamp";
    const media = () => ({ status: "PLAYING" });
    const webamp = { setTracksToPlay() {}, appendTracks() {}, play() { hooks.plays++; }, pause() {} };
    const currentInfo = () => ({ index: 0 });
    const webampSettled = () => true, webampItems = () => [{ id: "b1" }];
    const insertAfter = (base, i, xs) => [...base.slice(0, i + 1), ...xs, ...base.slice(i + 1)];
    const storeState = () => ({ playlist: { trackOrder: [0, 1] } }), dispatchStore = () => {};
    const stagePersist = () => {}, updateNowPlayingSoon = () => {}, toWebampTrack = (x) => x;
    const toast = () => {}, endSilence = () => { hooks.silenceEnded++; };
    const api = { ${body} };
    return { api, state: () => ({ actSeq, place, startPending }) };`);
  const hooks = { plays: 0, silenceEnded: 0, focusLoads: [] };
  return { ...make(hooks), hooks };
}

for (const mode of ["append", "next"]) {
  test(`queue edit (${mode}, default play) keeps the book's place and pending resume`, async () => {
    const { api, state, hooks } = harness();
    await api.loadTracks([{ id: "s1", title: "Song" }], { mode });
    assert.equal(state().actSeq, 0, "a queue edit is not the person's own move");
    assert.deepEqual(state().place, { track: "t1", pos: 4320 });
    assert.equal(state().startPending, true);
    assert.equal(hooks.plays, 0);
  });
}

test("replacing the queue still drops the old place", async () => {
  const { api, state } = harness();
  await api.loadTracks([{ id: "s1" }], { mode: "replace" });
  assert.equal(state().actSeq, 1);
  assert.equal(state().place, null);
});

test("a list for another backend that starts playing still drops the old place", async () => {
  const { api, state, hooks } = harness();
  await api.loadTracks([{ id: "f", playback: { backend: "focus" } }], { mode: "append", play: true });
  assert.equal(hooks.focusLoads.length, 1);
  assert.equal(state().actSeq, 1);
  assert.equal(state().place, null);
});
