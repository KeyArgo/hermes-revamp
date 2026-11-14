// Shuffle is a dealt deck (web/shuffle.js), not Webamp 2.3.1's random pick on every Next, which
// heard 6 of 12 songs in 12 Nexts and sent Previous somewhere random (measured 2026-11-14).
import assert from "node:assert/strict";
import test from "node:test";

import { createDeck, shuffled } from "../web/shuffle.js";

const ids = (n) => Array.from({ length: n }, (_, i) => i);

// A small seeded generator, so failures reproduce.
function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test("every song plays once before any song repeats", () => {
  for (let seed = 1; seed <= 50; seed++) {
    const q = ids(12);
    const deck = createDeck(seeded(seed));
    deck.start(q, 0);
    const heard = [0];
    for (let i = 0; i < 11; i++) heard.push(deck.next(q));
    assert.equal(new Set(heard).size, 12, `seed ${seed}: ${heard}`);
    assert.equal(deck.next(q), null, "the end of a pass stops with Repeat off");
  }
});

test("the order is actually shuffled, and evenly (Fisher-Yates)", () => {
  const random = seeded(7);
  const counts = Array.from({ length: 4 }, () => Array(4).fill(0));
  const runs = 24000;
  for (let r = 0; r < runs; r++) shuffled([0, 1, 2, 3], random).forEach((v, pos) => counts[v][pos]++);
  for (const row of counts) for (const c of row) assert.ok(Math.abs(c - runs / 4) < runs * 0.02, `uneven: ${JSON.stringify(counts)}`);
  const deck = createDeck(seeded(3));
  deck.start(ids(20), 0);
  const order = deck.order;
  assert.notDeepEqual(order, ids(20), "20 songs came out in queue order");
});

test("Previous walks back through what was heard, Next comes forward again", () => {
  const q = ids(10);
  const deck = createDeck(seeded(11));
  deck.start(q, 4);
  const heard = [4, deck.next(q), deck.next(q), deck.next(q)];
  assert.equal(deck.prev(q), heard[2]);
  assert.equal(deck.prev(q), heard[1]);
  assert.equal(deck.prev(q), heard[0]);
  assert.equal(deck.prev(q), heard[0], "Previous on the first song stays there");
  assert.equal(deck.next(q), heard[1]);
  assert.equal(deck.next(q), heard[2]);
});

test("with Repeat a new pass is dealt, never starting with the song that just ended", () => {
  for (let seed = 1; seed <= 40; seed++) {
    const q = ids(6);
    const deck = createDeck(seeded(seed));
    deck.start(q, 2);
    let last = 2;
    for (let i = 0; i < 5; i++) last = deck.next(q, { repeat: true });
    const first = deck.next(q, { repeat: true });
    assert.notEqual(first, last, `seed ${seed}`);
    const pass = [first];
    for (let i = 0; i < 5; i++) pass.push(deck.next(q, { repeat: true }));
    assert.equal(new Set(pass).size, 6, "the second pass also plays every song once");
  }
});

test("Previous keeps walking back across passes (Repeat, or Next after the last card)", () => {
  const q = ids(5);
  const deck = createDeck(seeded(21));
  deck.start(q, 0);
  const heard = [0];
  for (let i = 0; i < 4; i++) heard.push(deck.next(q));
  const firstOfNewPass = deck.next(q, { repeat: true });
  assert.notEqual(firstOfNewPass, null);
  assert.equal(deck.prev(q), heard[4], "Previous from a new pass goes back into the last one");
  assert.equal(deck.prev(q), heard[3]);
  assert.equal(deck.next(q), heard[4]);
  assert.equal(deck.next(q), firstOfNewPass);
});

test("picking a song already heard plays it again without losing the rest of the pass", () => {
  const q = ids(6);
  const deck = createDeck(seeded(13));
  deck.start(q, 0);
  const a = deck.next(q);
  deck.jump(q, 0); // back to the first song by hand
  const rest = [];
  for (let id; (id = deck.next(q)) != null;) rest.push(id);
  assert.equal(rest.length, 4, `the 4 songs not yet heard still come: ${rest}`);
  assert.ok(!rest.includes(a) && !rest.includes(0));
});

test("a picked song plays now and the deck carries on without it later", () => {
  const q = ids(8);
  const deck = createDeck(seeded(5));
  deck.start(q, 0);
  const upcoming = deck.order.slice(1);
  const pick = upcoming[upcoming.length - 1]; // the song the deck would have dealt last
  deck.jump(q, pick);
  const rest = [];
  for (let i = 0; i < 6; i++) rest.push(deck.next(q));
  assert.equal(new Set([0, pick, ...rest]).size, 8, "nothing repeats and nothing is skipped");
  assert.equal(deck.next(q), null);
});

test("the deck follows the queue: removed songs drop out, added songs are still to come", () => {
  const deck = createDeck(seeded(9));
  let q = ids(6);
  deck.start(q, 0);
  const first = deck.next(q);
  q = q.filter((id) => id !== deck.order[2]).concat([100, 101]); // one removed, two added
  const rest = [];
  for (let id; (id = deck.next(q)) != null;) rest.push(id);
  const heard = [0, first, ...rest];
  assert.equal(heard.length, q.length, `${heard}`);
  assert.deepEqual(new Set(heard), new Set(q));
});

test("tiny queues", () => {
  const one = createDeck(seeded(1));
  one.start([7], 7);
  assert.equal(one.next([7]), null);
  assert.equal(one.next([7], { repeat: true }), 7, "a one-song queue on repeat replays it");
  const empty = createDeck(seeded(1));
  empty.start([], null);
  assert.equal(empty.next([], { repeat: true }), null);
  assert.equal(empty.prev([]), null);
});
