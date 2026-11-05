import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// User reports: moving the player (pop out to desktop, back into the Frame) played the first
// moments of the song from 0:00 and then jumped to where it was. Later review rounds found
// many timing cases around that move (pause, skip, seek, move again, slow media). They are
// all handled by one rule, tested here: a handed-over `place` that the track reaches
// silently whenever it plays (see reachPlace in web/player.js).
const src = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");

function fnSource(name) {
  let a = src.indexOf(`async function ${name}(`);
  if (a === -1) a = src.indexOf(`function ${name}(`);
  assert.notEqual(a, -1, name);
  let depth = 0;
  for (let i = src.indexOf("{", a); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(a, i + 1);
  }
  throw new Error("unbalanced");
}
// An object method ("    async playIndex(i) {"), up to its matching brace.
function methodSource(sig) {
  const a = src.indexOf(sig);
  assert.notEqual(a, -1, sig);
  let depth = 0;
  // The signature ends with its body's "{" (parameters may hold braces of their own).
  for (let i = a + sig.length - 1; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(a, i + 1);
  }
  throw new Error("unbalanced");
}
const line = (re) => { const m = src.match(re); assert.ok(m, String(re)); return m[0]; };

// A fake Webamp: play() starts the audio 30 ms later at 0.3 s; a seek lands 30 ms later.
function fakePlayer({ volume = 60, loseFirstSeek = false, neverLoads = false, track = "t1", slowStatus = false } = {}) {
  const st = { volume, timeElapsed: 0, status: "STOPPED", track, log: [], seeks: 0 };
  const webamp = {
    setVolume(v) { st.volume = v; st.log.push(["vol", v]); },
    play() {
      st.log.push(["play"]);
      if (st.track == null) st.track = "t1";
      if (slowStatus) setTimeout(() => { st.status = "PLAYING"; }, 20); else st.status = "PLAYING";
      if (!neverLoads && !(st.timeElapsed > 0)) setTimeout(() => { if (!(st.timeElapsed > 0)) st.timeElapsed = 0.3; }, 30);
    },
    pause() { st.status = "PAUSED"; st.log.push(["pause"]); },
    seekToTime(t) {
      st.log.push(["seek", t]);
      if (loseFirstSeek && st.seeks++ === 0) return; // audio not ready: the seek is lost
      setTimeout(() => { st.timeElapsed = t; }, 30);
    },
  };
  const run = new Function("webamp", "media", "rawVolume", "storeState",
    `let silentVol = null; let place = null; let reaching = false; let silentSeq = 0;
     const seekIntent = () => ({ version: undefined });
     const seekInternally = t => webamp.seekToTime(t);
     const SILENT_START_WAIT_MS = 2000; // shortened here: the real wait is checked below
     ${line(/  const curTrack = [^\n]*\n/)}
     ${fnSource("placeHere")}
     ${fnSource("endSilence")}
     ${fnSource("startTowardPlace")}
     ${line(/  function currentVolume\(\) \{[^\n]*\n/)}
     ${fnSource("setOutputVolume")}
     ${fnSource("reachPlace")}
     return {
       reach: reachPlace, currentVolume, setOutputVolume, placeHere, startTowardPlace, endSilence,
       setPlace: (p) => { place = p; }, place: () => place, reaching: () => reaching,
     };`);
  const api = run(webamp, () => ({ timeElapsed: st.timeElapsed, status: st.status }), () => st.volume,
    () => ({ playlist: { currentTrack: st.track } }));
  return { st, webamp, api };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const later = (ms, fn) => setTimeout(fn, ms);

test("a handed-over track reaches its place with the sound off, then gets its volume back", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  await api.reach();
  const kinds = st.log.map(([k, v]) => (k === "vol" ? `vol${v}` : k));
  assert.ok(kinds.indexOf("vol0") >= 0 && kinds.indexOf("vol0") < kinds.indexOf("seek"));
  assert.deepEqual(st.log.at(-1), ["vol", 60]);
  await sleep(40);
  assert.equal(st.timeElapsed, 95);
  assert.equal(api.place(), null);
});

test("a pause during it keeps the place; the next play reaches it", async () => {
  const { st, webamp, api } = fakePlayer({ neverLoads: true });
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  later(20, () => webamp.pause());
  await api.reach();
  assert.deepEqual(api.place(), { track: "t1", pos: 95 }, "the place must survive the pause");
  assert.equal(st.volume, 60);
  st.timeElapsed = 0; // the audio arrives on the next play
  webamp.play();
  later(30, () => { st.timeElapsed = 0.3; });
  await api.reach();
  await sleep(40);
  assert.equal(st.timeElapsed, 95);
  assert.equal(api.place(), null);
});

test("a seek the person makes wins over the place", async () => {
  const { st, webamp, api } = fakePlayer({ loseFirstSeek: true });
  api.setPlace({ track: "t1", pos: 180 });
  webamp.play();
  later(200, () => { st.timeElapsed = 30; });
  await api.reach();
  assert.equal(st.timeElapsed, 30);
  assert.equal(api.place(), null);
});

test("another track playing forgets the place and is never seeked", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  later(10, () => { st.track = "t2"; });
  await api.reach();
  await sleep(40);
  assert.notEqual(st.timeElapsed, 95);
  await api.reach(); // the next status change for t2
  assert.equal(api.place(), null);
  assert.equal(st.volume, 60);
});

test("a seek lost because the audio wasn't ready is repeated", async () => {
  const { st, webamp, api } = fakePlayer({ loseFirstSeek: true });
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  await api.reach();
  await sleep(40);
  assert.equal(st.timeElapsed, 95);
});

test("an approval dip during it keeps the output silent, then lands at the dip level", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  later(10, () => api.setOutputVolume(15));
  await api.reach();
  const beforeSeek = st.log.slice(0, st.log.findIndex(([k]) => k === "seek"));
  assert.ok(beforeSeek.filter(([k]) => k === "vol").every(([, v]) => v === 0), JSON.stringify(st.log));
  assert.equal(st.volume, 15);
});

test("while silent, the rest of the player sees the person's volume, not 0", async () => {
  const { webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  let seen = null;
  later(10, () => { seen = api.currentVolume(); });
  await api.reach();
  assert.equal(seen, 60);
});

test("a volume change during it is what the rest of the player sees", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.play();
  let seen = null;
  later(10, () => { st.volume = 40; seen = api.currentVolume(); });
  await api.reach();
  assert.equal(seen, 40);
  assert.equal(st.volume, 40);
});

test("nothing selected at claim time: the track play() picks is the one", async () => {
  const { st, webamp, api } = fakePlayer({ track: null });
  api.setPlace({ track: null, pos: 95 });
  webamp.play();
  await api.reach();
  await sleep(40);
  assert.equal(st.timeElapsed, 95);
});

test("a place that can never be reached is given up, so the track isn't stuck reporting it", async () => {
  const { webamp, api, st } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.seekToTime = (t) => { st.log.push(["seek", t]); }; // every seek lost
  webamp.play();
  await api.reach();
  assert.equal(api.place(), null);
  assert.equal(st.volume, 60);
});

test("an attempt that ends while the track plays again hands the silent stretch straight on", () => {
  const r = fnSource("reachPlace");
  const hand = r.indexOf('if (place && media().status === "PLAYING") void reachPlace();');
  assert.ok(hand >= 0 && hand < r.indexOf("if (!reaching) {"), "the volume must not come back before the next attempt");
});

// ---- how it is wired in --------------------------------------------------------------

function claimBlock() {
  const a = src.indexOf('apiPost("/api/handoff/claim"');
  return src.slice(a, src.indexOf("}).catch(() => {});", a));
}

test("a claimed handoff keeps a place (not for streams) and starts unless paused or skipped", () => {
  const c = claimBlock();
  assert.match(c, /if \(!isStream\) place = \{ track: claimTrack, pos:/);
  assert.match(c, /startPending = true;/);
  assert.match(c, /if \(!startPending\) return;/);
  assert.match(c, /if \(claimTrack != null && curTrack\(\) !== claimTrack\) \{ place = null; return; \}/);
  assert.doesNotMatch(c, /seekToTime/, "seeking belongs to reachPlace only");
});

test("pause, stop and toggle cancel a start that hasn't happened yet", () => {
  for (const name of ["stop() {", "pause() {"]) {
    const a = src.indexOf(`    ${name}`);
    assert.match(src.slice(a, src.indexOf("\n", a)), /startPending = false;/, name);
  }
  const t = src.indexOf("    toggle() {");
  const tog = src.slice(t, src.indexOf("    },", t));
  // A pending start counts as playing only while nothing has really started.
  assert.match(tog, /if \(startPending && st !== "PLAYING"\) \{ startPending = false; return; \}/);
  assert.ok(tog.indexOf('if (st === "PLAYING") { try { webamp.pause(); }') > tog.indexOf("startPending = false;\n"));
});

test("the player reports the place and 'playing' until the handoff has started and arrived", () => {
  const info = fnSource("currentInfo");
  assert.match(info, /status: startPending && m\.status !== "PLAYING" \? "PLAYING"/);
  assert.match(info, /position: placeHere\(\) \? placeHere\(\)\.pos :/);
  assert.match(info, /volume: currentVolume\(\),/);
});

test("while a place is pending a book saves that place (not 0:01), and its own resume stands down", () => {
  assert.match(fnSource("saveBookPlace"), /if \(silentVol != null && !placeHere\(\)\) return;/);
  const mr = fnSource("maybeResume");
  assert.ok(mr.indexOf("if (placeHere()) {") > mr.indexOf("resumedKey = key;"));
  // ...and again right before its seek: a handoff can arrive while the resume request loads.
  const seek = mr.indexOf("applySeek(pos, rk, gen, acted);");
  assert.ok(mr.lastIndexOf("if (placeHere() || gen !== handoffGen || acted !== actSeq - transportSeq) return;", seek) > mr.indexOf("await apiGet("));
  assert.ok(mr.indexOf("const gen = handoffGen;") < mr.indexOf("await apiGet("));
  assert.match(claimBlock(), /handoffGen\+\+;/);
});

test("every status change gives reachPlace a turn", () => {
  assert.match(src, /setTimeout\(\(\) => \{ placeHere\(\); void reachPlace\(\); endSilence\(\); saveBookPlace\(true\); maybeResume\(\); \}, 0\)/);
});

test("the silent 0 is never saved as the person's volume; commands and fades go through setOutputVolume", () => {
  const persist = fnSource("persistNativeSettings");
  assert.match(persist, /!duck\.active && silentVol == null && Number\.isFinite\(st\?\.media\?\.volume\)/);
  const a = src.indexOf("    setVolume(v) {");
  assert.match(src.slice(a, src.indexOf("\n", a)), /setOutputVolume\(clamp\(v\)\)/);
  assert.match(fnSource("animateVolume"), /setOutputVolume\(v\)/);
});

test("a track with a place is muted BEFORE it starts, so not even 0:00's first moment is heard", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  api.startTowardPlace(() => webamp.play());
  const iMute = st.log.findIndex(([k, v]) => k === "vol" && v === 0);
  const iPlay = st.log.findIndex(([k]) => k === "play");
  assert.ok(iMute >= 0 && iMute < iPlay, JSON.stringify(st.log));
  await sleep(2400);
  assert.equal(st.timeElapsed, 95);
  assert.equal(st.volume, 60);
});

test("a track already playing is not started again (that would restart it at 0:00)", async () => {
  const { st, webamp, api } = fakePlayer();
  st.status = "PLAYING"; st.timeElapsed = 0.5;
  api.setPlace({ track: "t1", pos: 95 });
  api.startTowardPlace(() => { if (st.status !== "PLAYING") webamp.play(); });
  assert.ok(!st.log.some(([k]) => k === "play"));
  await sleep(2400);
  assert.equal(st.timeElapsed, 95);
});

test("without a place nothing is muted", () => {
  const { st, webamp, api } = fakePlayer();
  api.startTowardPlace(() => webamp.play());
  assert.ok(!st.log.some(([k, v]) => k === "vol" && v === 0));
});

test("a seek that throws ends the attempt instead of retrying forever", async () => {
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  webamp.seekToTime = () => { throw new Error("broken"); };
  webamp.play();
  await api.reach();
  assert.equal(api.place(), null);
  assert.equal(st.volume, 60);
});

test("moved well away from the place while paused: the person's position wins", () => {
  const { st, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  st.status = "PAUSED"; st.timeElapsed = 30; // they seeked while paused
  assert.equal(api.placeHere(), null);
  assert.equal(api.place(), null);
});

test("anything the person does before the handoff answer arrives wins; a seek replaces the place", () => {
  const c = claimBlock();
  assert.match(src, /const claimSeq = actSeq;[\s\S]{0,600}apiPost\("\/api\/handoff\/claim"/);
  const drop = c.indexOf("if (actSeq !== claimSeq) return;");
  assert.ok(drop >= 0 && drop < c.indexOf("place = {") && drop < c.indexOf("startPending = true;"));
  for (const name of ["stop() {", "pause() {", "play() {"]) {
    const a = src.indexOf(`    ${name}`);
    assert.match(src.slice(a, src.indexOf("\n", a)), /actSeq\+\+/, name);
  }
  assert.match(methodSource("    seek(sec) {"), /actSeq\+\+/);
  const t = src.indexOf("    toggle() {");
  assert.match(src.slice(t, src.indexOf("    },", t)), /actSeq\+\+;/);
  assert.match(methodSource("    seek(sec) {"), /place = null;/);
  assert.match(c, /startTowardPlace\(\(\) => \{ if \(media\(\)\.status !== "PLAYING"\) webamp\.play\(\); \}\)/);
});

test("nothing selected yet and the status lags: the volume stays down until the reach starts", async () => {
  const { st, webamp, api } = fakePlayer({ track: null, slowStatus: true });
  api.setPlace({ track: null, pos: 95 });
  api.startTowardPlace(() => webamp.play());
  assert.equal(st.volume, 0, "restored before PLAYING: the start would be heard");
  assert.equal(api.place().track, "t1");
});

test("any track change, queue change or relative seek before the answer drops the handoff", () => {
  const c = claimBlock();
  assert.match(src, /const trackAtClaim = curTrack\(\);/);
  assert.match(c, /if \(trackAtClaim != null && curTrack\(\) !== trackAtClaim\) return;/);
  for (const name of ["    next() {", "    prev() {"]) assert.match(methodSource(name), /actSeq\+\+/, name);
  assert.match(methodSource("    async playIndex(i) {"), /actSeq\+\+;/);
  assert.match(methodSource('    async loadTracks(tracks, { mode = "replace", play = true } = {}) {'), /if \(play \|\| mode === "replace"\) \{ actSeq\+\+; place = null; startPending = false; endSilence\(\); \}/);
  const sb = methodSource("    seekBy(delta) {");
  assert.ok(sb.indexOf("place = null;") < sb.indexOf("seekInternally(to)") && sb.includes("actSeq++;"));
});

test("picking a song (even the same one), next or previous forgets a pending place", () => {
  assert.match(methodSource("    async playIndex(i) {"), /place = null;/);
  for (const name of ["    next() {", "    prev() {"]) assert.match(methodSource(name), /place = null;/, name);
});

test("next, previous or a seek in the ~300 ms before the start takes the start over (no silence)", () => {
  for (const name of ["    next() {", "    prev() {"]) {
    const m = methodSource(name);
    assert.match(m, /const started = takeOverStart\(\);/, name);
    assert.match(m, /if \(started && media\(\)\.status !== "PLAYING"\) webamp\.play\(\);/, name);
  }
  // A seek this early can be lost: the person's spot becomes the place, reached silently.
  for (const name of ["    seek(sec) {", "    seekBy(delta) {"]) {
    assert.match(methodSource(name), /if \(takeOverStart\(\)\) \{ place = \{ track: curTrack\(\), pos: [^}]+\}; startTowardPlace\(/, name);
  }
  assert.match(fnSource("takeOverStart"), /startPending = false;/);
});

test("an older 2 s restore timer cannot end a newer silent stretch", async () => {
  const { st, webamp, api } = fakePlayer({ slowStatus: true, neverLoads: true });
  api.setPlace({ track: "t1", pos: 95 });
  // First start: status lags, a 2 s restore timer is armed; then a pause.
  api.startTowardPlace(() => webamp.play());
  await sleep(50);
  webamp.pause();
  await api.reach(); // not playing: returns, place kept
  // Second start shortly after, still not PLAYING when the first timer fires.
  await sleep(1500);
  st.status = "PAUSED";
  api.startTowardPlace(() => { st.log.push(["play"]); setTimeout(() => { st.status = "PLAYING"; }, 1200); });
  await sleep(700); // the first timer (t≈2.0 s) fires here
  assert.equal(st.volume, 0, "the older timer switched the sound back on mid-start");
});

test("nothing selected when the start comes: still muted before play() picks the track", () => {
  // Nothing was selected at claim time (place.track null); by the start a track is selected.
  const { st, webamp, api } = fakePlayer({ track: "t1" });
  api.setPlace({ track: null, pos: 95 });
  api.startTowardPlace(() => webamp.play());
  const iMute = st.log.findIndex(([k, v]) => k === "vol" && v === 0);
  const iPlay = st.log.findIndex(([k]) => k === "play");
  assert.ok(iMute >= 0 && iMute < iPlay, JSON.stringify(st.log));
});

test("a book's own resume waits for the handoff answer before it seeks", () => {
  // Answered first, it moved the track to the older save and the place then looked like a seek.
  assert.match(src, /const claimDone = apiPost\("\/api\/handoff\/claim"/);
  const mr = fnSource("maybeResume");
  const wait = mr.indexOf("await claimDone.catch(() => {});");
  assert.ok(wait > mr.indexOf("await apiGet(") && wait < mr.indexOf("applySeek(pos, rk, gen, acted);"));
  assert.ok(mr.indexOf("const gen = handoffGen;") < mr.indexOf("await apiGet("), "gen must be taken before the wait");
});

test("picking a song during the pending start takes the start over (no second play)", () => {
  const m = methodSource("    async playIndex(i) {");
  assert.ok(m.indexOf("takeOverStart();") >= 0 && m.indexOf("takeOverStart();") < m.indexOf("webamp.play()"));
});

test("a muted start waits as long as a reach does (15 s) before the volume comes back", () => {
  assert.match(src, /const SILENT_START_WAIT_MS = 15000;/);
  assert.match(fnSource("startTowardPlace"), /setTimeout\(restore, SILENT_START_WAIT_MS\)/);
  assert.match(fnSource("reachPlace"), /i < 150 && going\(\)/); // 150 x 100 ms = the same 15 s
});

test("skipping away from a muted, still-starting track gives the volume back at once", async () => {
  // Round 25: the new track played silently until the 15 s fallback timer.
  const { st, webamp, api } = fakePlayer({ slowStatus: true, neverLoads: true });
  api.setPlace({ track: "t1", pos: 95 });
  api.startTowardPlace(() => webamp.play());
  assert.equal(st.volume, 0);
  api.setPlace(null); // next() / playIndex() / seek() / stop() drop the place...
  api.endSilence();   // ...and end the silence
  assert.equal(st.volume, 60);
});

test("another track starting (from anywhere) ends the silence through reachPlace", async () => {
  const { st, webamp, api } = fakePlayer({ slowStatus: true, neverLoads: true });
  api.setPlace({ track: "t1", pos: 95 });
  api.startTowardPlace(() => webamp.play());
  st.track = "t2"; st.status = "PLAYING"; // Webamp's own next, say
  await api.reach();
  assert.equal(api.place(), null);
  assert.equal(st.volume, 60);
});

test("every way of dropping a place ends the silence", () => {
  for (const sig of ["    next() {", "    prev() {", "    async playIndex(i) {", "    seek(sec) {", "    seekBy(delta) {"]) {
    assert.match(methodSource(sig), /endSilence\(\);/, sig);
  }
  const stop = src.indexOf("    stop() {");
  assert.match(src.slice(stop, src.indexOf("\n", stop)), /place = null; endSilence\(\);/);
  assert.match(methodSource('    async loadTracks(tracks, { mode = "replace", play = true } = {}) {'), /startPending = false; endSilence\(\); \}/);
  assert.match(src, /setTimeout\(\(\) => \{ placeHere\(\); void reachPlace\(\); endSilence\(\); saveBookPlace\(true\); maybeResume\(\); \}, 0\)/);
  assert.match(fnSource("reachPlace"), /place = null; endSilence\(\); return; \} \/\/ another track is playing/);
});

test("selecting another track, even without playing it, forgets the old track's place", () => {
  const { st, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 182 });
  st.status = "PAUSED";
  st.track = "t2"; // Webamp's own playlist click while paused
  api.placeHere();  // the store change runs this
  st.track = "t1";  // much later they start t1 fresh
  assert.equal(api.placeHere(), null);
  assert.equal(api.place(), null);
});

test("a REVamp seek during the retry wait is not overwritten by the old place", async () => {
  // Every seek is lost until the person's own seek: the retry must not seek again after it.
  const { st, webamp, api } = fakePlayer();
  api.setPlace({ track: "t1", pos: 95 });
  const realSeek = webamp.seekToTime;
  let personSeeked = false;
  webamp.seekToTime = (t) => { st.log.push(["seek", t]); if (personSeeked) realSeek(t); };
  webamp.play();
  later(580, () => { personSeeked = true; api.setPlace(null); st.timeElapsed = 2; }); // seek(2) during the wait before retry 10
  await api.reach();
  const after = st.log.slice(st.log.findIndex(([k]) => k === "seek")).filter(([k, v]) => k === "seek" && v === 95);
  assert.ok(st.log.filter(([k, v]) => k === "seek" && v === 95).length >= 1);
  await sleep(60);
  assert.equal(st.timeElapsed, 2, "the old place's retry seek overwrote the person's seek");
  void after;
});
