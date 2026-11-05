import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Run the actual seek bookkeeping, save function, and Webamp subscription together.
function harness() {
  const src = readFileSync(new URL('../web/player.js', import.meta.url), 'utf8');
  const slice = (a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i); assert.ok(i >= 0 && j > i); return src.slice(i, j); };
  let callback; const timers = [], saved = new Map(), posts = [];
  const state = { track: 'A', pos: 0, status: 'PAUSED' };
  const ctx = {
    curTrack: () => state.track, activeBackend: 'webamp', resumingId: null, silentVol: null,
    currentInfo: () => ({ item: state.track, position: state.pos, duration: 1000 }),
    resumeKeyOf: x => x, placeHere: () => null, reachPlace: async () => {}, endSilence() {},
    bookSaved: { id: null, at: 0, pos: -1 }, BOOK_SAVE_MS: 10000, saveChain: Promise.resolve(),
    apiPost: async (_, body) => { posts.push([body.key, body.position]); saved.set(body.key, body.position); },
    storeState: () => ({ playlist: { currentTrack: state.track, trackOrder: ['A', 'B'] }, media: { status: state.status } }),
    persistNativeSettings() {}, saveQueuePosition() {}, beat() {}, maybeResume() {},
    setTimeout: (f, delay) => { if (delay === 0) timers.push(f); },
    webamp: {store: {subscribe: f => { callback = f; }}}, lastOrderKey: '', lastCurrent: null, lastBeatKey: '',
  };
  vm.createContext(ctx);
  vm.runInContext(slice('  const seekVersions = new Map();', '  const seekInternally =') + '\nglobalThis.noteSeek = noteSeek;', ctx);
  vm.runInContext('let actSeq = 0, internalSeekDepth = 0; const options = {\n' + slice('    __customMiddlewares:', '    initialTracks:') + '\n}; globalThis.dispatch = options.__customMiddlewares[0]()(() => {});', ctx);
  vm.runInContext(slice('  function saveBookPlace(', '  // The file played before'), ctx);
  vm.runInContext(slice('  try {\n    webamp.store.subscribe(() => {', '\n\n  // ---- MilkDrop'), ctx);
  const flush = async () => { while (timers.length) timers.shift()(); await ctx.saveChain; };
  return { state, saved, posts, ctx, callback, flush, async visit(track, pos = 0) { state.track = track; state.pos = pos; callback(); await flush(); } };
}

test('old zero intent cannot erase a newer bookmark on leave and revisit', async () => {
  const h = harness();
  await h.visit('A');
  h.ctx.noteSeek(0); h.ctx.saveBookPlace(true); await h.flush();
  assert.equal(h.saved.get('A'), 0, 'first explicit seek saved');
  h.state.pos = 73; h.ctx.saveBookPlace(true); await h.flush();
  assert.equal(h.saved.get('A'), 73, 'newer place persisted before leaving');
  await h.visit('B');
  await h.visit('A');
  assert.equal(h.saved.get('A'), 73, 'revisit startup zero must not overwrite the saved place');
});

test('fresh explicit zero seek after a track change is persisted', async () => {
  const h = harness();
  await h.visit('A'); h.state.pos = 73; h.ctx.saveBookPlace(true); await h.flush();
  await h.visit('B');
  h.state.track = 'A'; h.state.pos = 0; h.callback();
  h.ctx.noteSeek(0); // user seeks after the synchronous track update but before deferred save
  await h.flush();
  assert.equal(h.saved.get('A'), 0);
});

test('a nonzero seek does not authorize buffering zero', async () => {
  const h = harness(); await h.visit('A', 73); h.ctx.saveBookPlace(true); await h.flush();
  h.ctx.noteSeek(42); h.state.pos = 0; h.ctx.saveBookPlace(true); await h.flush();
  assert.equal(h.saved.get('A'), 73);
});

test('zero authorization cannot erase later progress on the same visit', async () => {
  const h = harness(); await h.visit('A'); h.ctx.noteSeek(0); h.ctx.saveBookPlace(true); await h.flush();
  h.state.pos = 73; h.ctx.saveBookPlace(true); await h.flush();
  h.state.pos = 0; h.ctx.saveBookPlace(true); await h.flush();
  assert.equal(h.saved.get('A'), 73);
});

test('native seek middleware authorizes only the zero percent action', async () => {
  const h = harness(); await h.visit('A', 73); h.ctx.saveBookPlace(true); await h.flush();
  h.ctx.dispatch({type:'SEEK_TO_PERCENT_COMPLETE', percent:25}); h.state.pos = 0;
  h.ctx.saveBookPlace(true); await h.flush(); assert.equal(h.saved.get('A'), 73);
  h.ctx.dispatch({type:'SEEK_TO_PERCENT_COMPLETE', percent:0});
  h.ctx.saveBookPlace(true); await h.flush(); assert.equal(h.saved.get('A'), 0);
});

test('native same-track restart invalidates an unconsumed zero seek', async () => {
  const h = harness(); await h.visit('A', 73); h.ctx.saveBookPlace(true); await h.flush();
  h.ctx.dispatch({type:'SEEK_TO_PERCENT_COMPLETE', percent:0});
  h.ctx.dispatch({type:'PLAY_TRACK', id:'A'}); h.state.pos = 0;
  h.ctx.saveBookPlace(true); await h.flush(); assert.equal(h.saved.get('A'), 73);
});
