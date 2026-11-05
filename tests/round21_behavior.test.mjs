import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Execute the actual player closures with a deterministic audio clock and fake timers.
function harness() {
  const source = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const start = source.indexOf('  function applySeek(');
  const end = source.indexOf('  function seekWhenStarted(', start);
  assert.ok(start >= 0 && end > start, 'player seek implementation found');
  let ms = 1000, id = 0;
  const timers = new Map(), seeks = [];
  const state = { clock: 1, resuming: 'book', onSeek: () => {} };
  const context = {
    Date: { now: () => ms },
    setInterval: (fn, delay) => { const n = ++id; timers.set(n, { fn, delay, next: ms + delay }); return n; },
    clearInterval: n => timers.delete(n),
    media: () => ({ timeElapsed: state.clock }),
    currentInfo: () => ({ item: 'book' }), resumeKeyOf: x => x,
    handoffGen: 0, actSeq: 0, transportSeq: 0, placeHere: () => false,
    seekInternally: pos => { seeks.push(pos); state.onSeek(pos, seeks.length); },
    toast: () => {}, fmt: String, resumingId: state.resuming,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  return { state, seeks, context, advance(duration) {
    const until = ms + duration;
    while (true) {
      const next = Math.min(...[...timers.values()].map(t => t.next), Infinity);
      if (next > until) break;
      ms = next;
      for (const [key, timer] of [...timers]) if (timer.next === ms) {
        timer.next += timer.delay; timer.fn();
      }
    }
    ms = until;
  }};
}

test('a native seek during a dropped resume seek wins over retries', () => {
  const h = harness();
  h.context.applySeek(50, 'book', 0, 0);
  assert.deepEqual(h.seeks, [50], 'first resume seek was attempted and dropped');
  h.advance(400);
  h.state.clock = 20;
  h.context.actSeq++; // Webamp dispatches its native seek action
  h.advance(6000);
  assert.deepEqual(h.seeks, [50], 'native seek must not be overwritten');
  assert.equal(h.context.resumingId, null);
});

test('a native seek to zero during a dropped resume seek wins over retries', () => {
  const h = harness();
  h.context.applySeek(50, 'book', 0, 0);
  assert.deepEqual(h.seeks, [50], 'first resume seek was attempted and dropped');
  h.advance(400);
  h.state.clock = 0;
  h.context.actSeq++; // native seek to zero is an action, not buffering
  h.advance(6000);
  assert.deepEqual(h.seeks, [50], 'seek to zero must not be overwritten');
  assert.equal(h.context.resumingId, null);
});

test('pausing during a dropped seek keeps the retry active', () => {
  const h = harness();
  h.context.applySeek(50, 'book', 0, 0);
  h.advance(2200);
  assert.deepEqual(h.seeks, [50, 50], 'frozen clock is not a native seek');
});

// HTML seeking aborts an earlier seek when a later seek starts; it does not allow the
// earlier request to assign the clock after the newer seek has completed.
test('a later native seek aborts a pending resume seek and prevents retries', () => {
  const h = harness();
  let pending = 0;
  h.state.onSeek = pos => {
    const generation = ++pending;
    const id = h.context.setInterval(() => {
      h.context.clearInterval(id);
      if (generation === pending) h.state.clock = pos;
    }, 1000);
  };
  h.context.applySeek(50, 'book', 0, 0);
  h.advance(400);
  pending++; // the media element aborts the older seek
  h.state.clock = 20;
  h.context.actSeq++;
  h.advance(6000);
  assert.equal(h.state.clock, 20);
  assert.deepEqual(h.seeks, [50]);
});

test('an asynchronous seek landing well short is retried', () => {
  const h = harness();
  h.context.applySeek(50, 'book', 0, 0);
  assert.deepEqual(h.seeks, [50]);
  h.advance(100);
  h.state.clock = 40; // media element lands ten seconds short, after seekToTime returned
  h.advance(2200);
  assert.deepEqual(h.seeks, [50, 50], 'short landing must not be mistaken for user input');
});

test('bounded failed retries release the save guard without claiming success', () => {
  const h = harness();
  h.context.applySeek(50, 'book', 0, 0);
  h.advance(8000);
  assert.deepEqual(h.seeks, [50, 50, 50]);
  assert.equal(h.context.resumingId, null);
});

function resumeHarness() {
  const source = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const start = source.indexOf('  async function maybeResume()');
  const end = source.indexOf('  // Once <audio>', start);
  const state = { track: 1, clock: 8, status: 'PLAYING', seekTrack: null, seekVersion: 0, place: false, calls: [] };
  let resolveBook;
  const context = {
    activeBackend: 'webamp', seenTrack: 1, seenTid: '1', prevTid: null, playedTid: null,
    resumedKey: null, resumingId: null, heldClock: null, heldSeekVersion: null, bookStart: null, handoffGen: 0, actSeq: 0, transportSeq: 0,
    seekIntent: track => ({ track, version: state.seekTrack === track ? state.seekVersion : 0 }),
    currentInfo: () => ({ item: '1', status: state.status, duration: 200 }),
    storeState: () => ({ playlist: { currentTrack: state.track } }),
    media: () => ({ timeElapsed: state.clock }), resumeKeyOf: () => 'book', localTid: () => '1',
    placeHere: () => state.place, movementWatch: () => () => false,
    setInterval: () => 1, clearInterval: () => {},
    apiGet: url => url.includes('/book?') ? new Promise(resolve => { resolveBook = resolve; }) : Promise.resolve({ position: 50 }),
    claimDone: Promise.resolve(), waitFirstAudio: async () => true,
    applySeek: pos => state.calls.push(pos), seekWhenStarted: () => {},
    webampItems: () => [], toast: () => {}, fmt: String, api: {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  return { state, context, resolve: () => resolveBook({}), async hold() {
    const task = context.maybeResume(); state.status = 'PAUSED'; this.resolve(); await task;
  }, async play() {
    state.status = 'PLAYING'; const task = context.maybeResume();
    if (resolveBook && state.calls.length === 0) resolveBook({});
    await task;
  }, seek(track, clock) { state.seekTrack = track; state.seekVersion++; state.clock = clock; } };
}

test('pause at eight seconds then Play resumes the saved position', async () => {
  const h = resumeHarness(); await h.hold(); await h.play();
  assert.deepEqual(h.state.calls, [50]);
});

test('a seek to zero while paused wins over Play', async () => {
  const h = resumeHarness(); await h.hold(); h.seek(1, 0); await h.play();
  assert.deepEqual(h.state.calls, []);
  assert.equal(h.context.resumingId, null);
});

test('a small seek while paused wins over Play', async () => {
  const h = resumeHarness(); await h.hold(); h.seek(1, 9); await h.play();
  assert.deepEqual(h.state.calls, []);
  assert.equal(h.context.resumingId, null);
});

test('seek intent from another track does not cancel a held resume', async () => {
  const h = resumeHarness(); await h.hold(); h.seek(2, 8); await h.play();
  assert.deepEqual(h.state.calls, [50]);
});

test('a seek during the paused lookup is retained as intent on Play', async () => {
  const h = resumeHarness();
  const task = h.context.maybeResume();
  h.state.status = 'PAUSED'; h.seek(1, 0); h.resolve(); await task;
  await h.play();
  assert.deepEqual(h.state.calls, []);
});

test('a native seek to zero clears the pending handoff for that track', () => {
  const source = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const start = source.indexOf('  function placeHere()');
  const end = source.indexOf('  // Start (or resume)', start);
  const state = { track: 1, clock: 0, version: 1, seekTrack: 1 };
  const context = { place: { track: 1, pos: 50, seekVersion: 0 }, reaching: false,
    curTrack: () => state.track, media: () => ({ timeElapsed: state.clock }),
    seekIntent: track => ({ track, version: state.seekTrack === track ? state.version : 0 }) };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  assert.equal(context.placeHere(), null);
});


test('pause and play during pending lookup preserve the saved place', async () => {
  const h = resumeHarness();
  const task = h.context.maybeResume();
  h.state.status = 'PAUSED'; h.context.actSeq++; h.context.transportSeq++;
  h.state.status = 'PLAYING'; h.context.actSeq++; h.context.transportSeq++;
  h.resolve(); await task;
  assert.deepEqual(h.state.calls, [50]);
});

test('pause before first audio keeps the saved place held for play', async () => {
  const h = resumeHarness();
  h.context.waitFirstAudio = async () => { h.state.status = 'PAUSED'; return false; };
  h.context.seekWhenStarted = () => { throw new Error('must hold instead of starting stale watcher'); };
  const task = h.context.maybeResume(); h.resolve(); await task;
  assert.equal(h.context.heldClock, 8);
  assert.equal(h.context.resumedKey, null);
});

test('native seek during handoff reach cancels its pending seek', async () => {
  const source = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const start = source.indexOf('  async function reachPlace()');
  const end = source.indexOf('  const claimSeq', start);
  let resolveWait; let version = 0; const seeks = [];
  const state = { clock: 1 };
  let waits = 0;
  const context = { reaching: false, place: {track: 1, pos: 50, seekVersion: 0}, silentSeq: 0,
    silentVol: null, curTrack: () => 1, seekIntent: () => ({version}),
    media: () => ({status: 'PLAYING', timeElapsed: state.clock}),
    setTimeout: fn => { if (++waits === 1) resolveWait = fn; else queueMicrotask(fn); }, currentVolume: () => 10, rawVolume: () => 0,
    webamp: {setVolume() {}}, seekInternally: p => seeks.push(p), endSilence() {},
  };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  const task = context.reachPlace();
  version++; state.clock = 1; resolveWait(); await task;
  assert.deepEqual(seeks, [50]);
  assert.equal(context.place, null);
});

test('explicit zero seek is saved as zero', async () => {
  const source = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const start = source.indexOf('  function saveBookPlace(');
  const end = source.indexOf('  // The file played before', start);
  const sent = [];
  const context = {activeBackend: 'webamp', currentInfo: () => ({item: 'book', position: 0, duration: 200}),
    resumeKeyOf: x => x, resumingId: null, silentVol: null, placeHere: () => false,
    seekIntent: () => ({version: 1}), zeroSeekTrack: 'track', curTrack: () => 'track', bookSaved: {id: null, at: 0, pos: -1}, BOOK_SAVE_MS: 10000,
    saveChain: Promise.resolve(), apiPost: async (url, body) => sent.push(body.position)};
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  context.saveBookPlace(true); await context.saveChain;
  assert.deepEqual(sent, [0]);
});
