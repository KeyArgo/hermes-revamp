// engine.js — Focus: a steady drone generated live with the Web Audio API.
//
// Self-contained on purpose (no Webamp, no Revamp imports) so another page can
// reuse it unchanged. No audio files: everything is oscillators and noise, so it
// works offline.
//
// The sound (ported from the offline numpy prototype the user listened to):
//   - partials at 1x, 1.5x, 2x, 3x, 4x of D2 (73.42 Hz), each a sine with a
//     little 2nd/3rd harmonic, slowly wandering in level and pitch (40–180 s
//     periods) and a hair wider on the right for stereo width;
//   - a brown-noise bed (plus a touch of white when bright);
//   - a one-pole low-pass whose cutoff follows "warmth";
//   - a sine-shaped beta-range amplitude pulse:
//       gain = 1 - depth * (0.5 - 0.5 * cos(2π · rate · t))
//     smooth, so it never clicks.
// No melody, no percussion, no sudden changes. Every parameter change glides
// (setTargetAtTime / linear crossfades); nothing jumps.

export const FOCUS_DEFAULTS = Object.freeze({
  warmth: 0.6, // 0 bright .. 1 dark
  noise: 0.35, // brown-noise level 0..1
  depth: 0.35, // pulse strength 0 (off) .. 0.9
  rate: 16, // pulse rate, Hz (12..20)
  volume: 1, // master 0..1 (a host may follow its player volume here)
  timer: 0, // minutes; 0 = no timer
});
export const FOCUS_LIMITS = Object.freeze({
  warmth: [0, 1], noise: [0, 1], depth: [0, 0.9], rate: [12, 20], volume: [0, 1], timer: [0, 24 * 60],
});
export const FOCUS_TIMERS = Object.freeze([0, 25, 50, 90]);

const ROOT = 73.42; // D2
const PARTIALS = [[1, 1.0], [1.5, 0.55], [2, 0.5], [3, 0.22], [4, null]]; // null: level follows brightness
const HARMONICS = [[1, 1], [2, 0.25], [3, 0.08]]; // harmonic k, level (k>1 scaled by brightness)
const STEREO_DETUNE = 1.0007;
const DRIFT_HZ = 0.0625; // 0.25 Hz detune * the 0.25 amplitude of each slow sine
const BROWN_CORNER_HZ = 14.04; // the prototype's 0.998 leaky integrator at 44.1 kHz
const BROWN_STD = 0.1985; // prototype brown noise std after its peak normalisation
const WHITE_STD = 1 / 5.87; // ditto for white
const TARGET_RMS = 0.1; // -20 dBFS: quiet and steady
const FADE_S = 6;

const clampNum = (v, [lo, hi], dflt) => {
  v = Number(v);
  return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
};

/** Normalise a (partial) parameter object: unknown keys dropped, values clamped. */
export function cleanParams(p = {}, base = FOCUS_DEFAULTS) {
  const out = { ...base };
  for (const k of Object.keys(FOCUS_DEFAULTS)) {
    if (p[k] !== undefined) out[k] = clampNum(p[k], FOCUS_LIMITS[k], base[k]);
  }
  return out;
}

const cutoffHz = (warmth) => 400 + 1600 * (1 - warmth);
const partialLevel = (a, bright) => (a == null ? 0.12 * bright + 0.03 : a);

/**
 * Expected RMS (per channel) of the graph before make-up gain, so loudness stays
 * near -20 dBFS whatever the settings — the prototype normalised every render.
 */
export function estimateRms(params, sampleRate = 48000) {
  const { warmth, noise, depth } = cleanParams(params);
  const bright = 1 - warmth;
  const fc = cutoffHz(warmth);
  const H = (f) => 1 / (1 + (f / fc) ** 2); // one-pole power response
  let drone = 0;
  for (const [mul, a] of PARTIALS) {
    const lvl = partialLevel(a, bright);
    const env2 = 0.65 * lvl * lvl; // E[(0.8 + 0.1 s1 + 0.1 s2)^2]
    for (const [k, h] of HARMONICS) {
      const amp = k === 1 ? h : h * bright;
      drone += env2 * 0.5 * amp * amp * H(k * mul * ROOT);
    }
  }
  drone /= 2.5 * 2.5;
  const enbw = Math.min(1, (Math.PI / 2) * fc / (sampleRate / 2));
  const noisePow = noise * noise * (0.64 * BROWN_STD ** 2 + (0.2 * bright * WHITE_STD) ** 2 * enbw);
  const am2 = (1 - depth / 2) ** 2 + (depth / 2) ** 2 / 2;
  return Math.sqrt((drone + noisePow) * am2);
}
const makeupFor = (p, sr) => TARGET_RMS / Math.max(1e-4, estimateRms(p, sr));

// Small seeded PRNG so offline renders can be reproducible.
function rng(seed) {
  let s = (seed >>> 0) || 1;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Glide an AudioParam to v. Holding first keeps a glide already under way from
// jumping, and keeps the automation list from growing over long sessions.
function glide(param, v, ctx, seconds = 0.4) {
  const now = ctx.currentTime;
  if (param.cancelAndHoldAtTime) param.cancelAndHoldAtTime(now);
  else { param.cancelScheduledValues(now); param.setValueAtTime(param.value, now); }
  param.setTargetAtTime(v, now, Math.max(0.01, seconds / 3));
}

// A linear ramp starts at the previous event; with none it would start at time 0
// and land mid-way (an audible step). So always anchor it at now.
function rampLinear(param, v, ctx, seconds) {
  const now = ctx.currentTime;
  if (param.cancelAndHoldAtTime) param.cancelAndHoldAtTime(now);
  else param.cancelScheduledValues(now);
  param.setValueAtTime(param.value, now);
  param.linearRampToValueAtTime(v, now + Math.max(0.01, seconds));
}

/**
 * Build one drone graph into `ctx`, ending at `out`. Returns handles to start,
 * re-tune, fade and tear it down. Works on AudioContext and OfflineAudioContext.
 */
export function buildFocusGraph(ctx, params, out, { seed, fadeIn = 0 } = {}) {
  let p = cleanParams(params);
  const rand = rng(seed ?? Math.floor(Math.random() * 2 ** 31));
  const sr = ctx.sampleRate;
  const sources = [];
  const gain = (v) => { const g = ctx.createGain(); g.gain.value = v; return g; };
  const sineWave = (phase) => ctx.createPeriodicWave(
    new Float32Array([0, Math.sin(phase)]), new Float32Array([0, Math.cos(phase)]), { disableNormalization: true });
  // A slow sine (period lo..hi seconds, random phase) scaled by `depth`, added into `target`.
  const slowSine = (lo, hi, depth, target) => {
    const o = ctx.createOscillator();
    o.setPeriodicWave(sineWave(rand() * 2 * Math.PI));
    o.frequency.value = 1 / (lo + rand() * (hi - lo));
    o.connect(gain(depth)).connect(target);
    sources.push(o);
  };

  let bright = 1 - p.warmth;
  const pre = gain(1); // drone + noise, into the low-pass
  const droneBus = gain(1 / 2.5);
  droneBus.connect(pre);
  const harmonicGains = []; // [GainNode, level] scaled by brightness
  let topLevel = null; // the 4x partial: louder when bright

  for (const [mul, a] of PARTIALS) {
    const merger = ctx.createChannelMerger(2);
    const env = gain(0.8); // 0.8 ± 0.1 ± 0.1: slow level wander
    const level = gain(partialLevel(a, bright));
    if (a == null) topLevel = level;
    merger.connect(env).connect(level).connect(droneBus);
    slowSine(40, 150, 0.1, env.gain);
    slowSine(40, 150, 0.1, env.gain);
    for (let ch = 0; ch < 2; ch++) {
      const f = ROOT * mul * (ch ? STEREO_DETUNE : 1);
      const drift = gain(1); // sums two slow sines: ±0.125 Hz pitch wander
      slowSine(60, 180, DRIFT_HZ, drift);
      slowSine(60, 180, DRIFT_HZ, drift);
      for (const [k, h] of HARMONICS) {
        const o = ctx.createOscillator();
        o.frequency.value = f * k;
        drift.connect(gain(k)).connect(o.frequency); // harmonics wander with their fundamental
        sources.push(o);
        if (k === 1) { o.connect(merger, 0, ch); continue; }
        const hg = gain(h * bright);
        harmonicGains.push([hg, h]);
        o.connect(hg).connect(merger, 0, ch);
      }
    }
  }

  // Noise: looped white buffers (different lengths per side, so no audible
  // repeat) through a leaky integrator for brown, plus a little white.
  const white = ctx.createChannelMerger(2);
  [17.3, 19.1].forEach((secs, ch) => {
    const n = Math.floor(secs * sr);
    const buf = ctx.createBuffer(1, n, sr);
    const d = buf.getChannelData(0);
    let mean = 0;
    for (let i = 0; i < n; i += 2) { // Box–Muller, two at a time
      const u = Math.max(1e-12, rand()), v = rand();
      const r = Math.sqrt(-2 * Math.log(u));
      d[i] = r * Math.cos(2 * Math.PI * v);
      if (i + 1 < n) d[i + 1] = r * Math.sin(2 * Math.PI * v);
    }
    for (let i = 0; i < n; i++) mean += d[i];
    mean /= n;
    for (let i = 0; i < n; i++) d[i] -= mean; // no DC: the integrator would turn it into an offset
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    src.connect(white, 0, ch);
    sources.push(src);
  });
  const leak = Math.exp((-2 * Math.PI * BROWN_CORNER_HZ) / sr);
  const brown = ctx.createIIRFilter([1], [1, -leak]);
  const brownGain = gain(0.8 * BROWN_STD * Math.sqrt(1 - leak * leak));
  const whiteGain = gain(0.2 * bright * WHITE_STD);
  const noiseLevel = gain(p.noise);
  white.connect(brown).connect(brownGain).connect(noiseLevel);
  white.connect(whiteGain).connect(noiseLevel);
  noiseLevel.connect(pre);

  // One-pole low-pass. IIR coefficients are fixed, so a warmth change crossfades
  // to a new filter (complementary linear ramps keep the level constant).
  const am = gain(1 - p.depth / 2);
  let filters = []; // [{iir, g, fc}]
  const addFilter = (fc, seconds) => {
    const a = Math.exp((-2 * Math.PI * fc) / sr);
    const iir = ctx.createIIRFilter([1 - a], [1, -a]);
    const g = gain(seconds ? 0 : 1);
    pre.connect(iir).connect(g).connect(am);
    const f = { iir, g, fc };
    if (seconds) {
      for (const old of filters) {
        rampLinear(old.g.gain, 0, ctx, seconds);
        old.dying = true;
      }
      rampLinear(g.gain, 1, ctx, seconds);
      const doomed = filters;
      setTimeout(() => {
        for (const old of doomed) {
          if (!old.dying) continue;
          try { pre.disconnect(old.iir); old.iir.disconnect(); old.g.disconnect(); } catch { /* gone */ }
        }
        filters = filters.filter((x) => !doomed.includes(x));
      }, seconds * 1000 + 200);
    }
    filters.push(f);
  };
  addFilter(cutoffHz(p.warmth), 0);

  // Pulse: 1 - d/2 + (d/2)·cos(2π·rate·t)
  const pulse = ctx.createOscillator();
  pulse.frequency.value = p.rate;
  const pulseDepth = gain(p.depth / 2);
  pulse.connect(pulseDepth).connect(am.gain);
  sources.push(pulse);

  const makeup = gain(makeupFor(p, sr));
  const fade = gain(fadeIn > 0 ? 0 : 1);
  const vol = gain(p.volume);
  // Safety: identity below 0.8, soft knee above. Never engages at normal levels.
  const clip = ctx.createWaveShaper();
  const curve = new Float32Array(4097);
  for (let i = 0; i < curve.length; i++) {
    const x = (i / (curve.length - 1)) * 2 - 1, ax = Math.abs(x);
    curve[i] = ax <= 0.8 ? x : Math.sign(x) * (0.8 + 0.2 * Math.tanh((ax - 0.8) / 0.2));
  }
  clip.curve = curve;
  am.connect(makeup).connect(fade).connect(vol).connect(clip).connect(out);

  let started = false;
  const graph = {
    get params() { return { ...p }; },
    output: clip,
    fadeGain: fade,
    start(when = ctx.currentTime) {
      if (started) return;
      started = true;
      for (const s of sources) s.start(when);
      if (fadeIn > 0) {
        fade.gain.setValueAtTime(0, when);
        fade.gain.linearRampToValueAtTime(1, when + fadeIn);
      }
    },
    /** Glide to new parameters over `seconds`. */
    set(patch, seconds = 0.4) {
      const next = cleanParams(patch, p);
      if (next.warmth !== p.warmth) {
        bright = 1 - next.warmth;
        for (const [hg, h] of harmonicGains) glide(hg.gain, h * bright, ctx, seconds);
        if (topLevel) glide(topLevel.gain, partialLevel(null, bright), ctx, seconds);
        glide(whiteGain.gain, 0.2 * bright * WHITE_STD, ctx, seconds);
        const fc = cutoffHz(next.warmth);
        if (Math.abs(fc - filters[filters.length - 1].fc) > 0.5) addFilter(fc, Math.max(0.3, seconds));
      }
      if (next.noise !== p.noise) glide(noiseLevel.gain, next.noise, ctx, seconds);
      if (next.depth !== p.depth) {
        glide(am.gain, 1 - next.depth / 2, ctx, seconds);
        glide(pulseDepth.gain, next.depth / 2, ctx, seconds);
      }
      if (next.rate !== p.rate) glide(pulse.frequency, next.rate, ctx, seconds);
      if (next.volume !== p.volume) glide(vol.gain, next.volume, ctx, Math.min(seconds, 0.2));
      if (next.warmth !== p.warmth || next.noise !== p.noise || next.depth !== p.depth) {
        glide(makeup.gain, makeupFor(next, sr), ctx, Math.max(seconds, 0.6));
      }
      p = next;
    },
    fadeTo(v, seconds) { rampLinear(fade.gain, v, ctx, seconds); },
    /** Stop every source (after any fade) and drop the nodes. */
    teardown() {
      for (const s of sources) { try { s.stop(); } catch { /* not started */ } try { s.disconnect(); } catch { /* ok */ } }
      try { clip.disconnect(); } catch { /* ok */ }
    },
  };
  return graph;
}

/**
 * The live engine: one AudioContext, one graph at a time, fades, pause/resume
 * and an optional timer that fades out at the end.
 *
 *   const e = new FocusEngine({ params });
 *   e.on((status) => …);           // state changes, param changes, timer ticks
 *   await e.start(); e.set({ depth: 0.2 }); e.setTimer(25); await e.pause(); …
 */
export class FocusEngine {
  constructor({ params, context = null, fadeSeconds = FADE_S, pauseFadeSeconds = 1.5 } = {}) {
    this.params = cleanParams(params);
    this.fadeSeconds = fadeSeconds;
    this.pauseFadeSeconds = pauseFadeSeconds;
    this.state = "stopped"; // stopped | playing | paused
    this.ctx = context;
    this.graph = null;
    this.analyser = null;
    this.elapsed = 0; // seconds actually played this session
    this.timerTotal = 0; // seconds; 0 = none
    this.timerLeft = 0;
    this.endedBy = null; // "timer" after a timer ran out
    this._listeners = new Set();
    this._tick = null;
    this._lastTick = 0;
    this._seq = 0; // bumps on every start/stop/pause/resume, so stale fades do nothing
    this._unlock = null;
  }

  on(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  _emit() { const s = this.status(); for (const fn of this._listeners) { try { fn(s); } catch { /* listener bug */ } } }

  status() {
    return {
      state: this.state,
      params: { ...this.params },
      elapsed: Math.floor(this.elapsed),
      timerTotal: this.timerTotal,
      timerLeft: Math.ceil(this.timerLeft),
      contextState: this.ctx?.state ?? "none",
      endedBy: this.endedBy,
    };
  }

  _context() {
    if (!this.ctx) {
      const AC = globalThis.AudioContext || globalThis.webkitAudioContext;
      this.ctx = new AC({ latencyHint: "playback" });
    }
    if (!this.analyser) {
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 2048;
      this.analyser.connect(this.ctx.destination);
    }
    return this.ctx;
  }

  // Autoplay rules can leave a context suspended without a click: resume on the next one.
  async _resumeContext() {
    const ctx = this.ctx;
    try { await ctx.resume(); } catch { /* blocked */ }
    if (ctx.state !== "running" && !this._unlock && globalThis.addEventListener) {
      this._unlock = () => {
        if (this.state === "playing") ctx.resume().then(() => this._emit()).catch(() => {});
        globalThis.removeEventListener("pointerdown", this._unlock, true);
        globalThis.removeEventListener("keydown", this._unlock, true);
        this._unlock = null;
      };
      globalThis.addEventListener("pointerdown", this._unlock, true);
      globalThis.addEventListener("keydown", this._unlock, true);
    }
  }

  async start({ fade = this.fadeSeconds } = {}) {
    if (this.state === "playing") return this.status();
    if (this.state === "paused") return this.resume();
    const seq = ++this._seq;
    const ctx = this._context();
    await this._resumeContext();
    if (seq !== this._seq) return this.status();
    this._dropGraph(0.05); // anything still fading out from a quick stop/start
    this.graph = buildFocusGraph(ctx, this.params, this.analyser, { fadeIn: fade });
    this.graph.start(ctx.currentTime + 0.05);
    this.state = "playing";
    this.elapsed = 0;
    this.endedBy = null;
    if (this.params.timer) this.setTimerSeconds(this.params.timer * 60, { keepParam: true });
    this._startTicking();
    this._emit();
    return this.status();
  }

  async stop({ fade = this.fadeSeconds, reason = null } = {}) {
    if (this.state === "stopped") return this.status();
    const seq = ++this._seq;
    const wasPaused = this.state === "paused";
    this.state = "stopped";
    this.endedBy = reason;
    this._stopTicking();
    this.timerLeft = this.timerTotal;
    this._emit();
    this._dropGraph(wasPaused ? 0 : fade);
    // Idle: give the audio thread back once the fade is done.
    setTimeout(() => {
      if (seq === this._seq && this.state === "stopped") this.ctx?.suspend().catch(() => {});
    }, (wasPaused ? 0 : fade) * 1000 + 300);
    return this.status();
  }

  async pause({ fade = this.pauseFadeSeconds } = {}) {
    if (this.state !== "playing") return this.status();
    const seq = ++this._seq;
    this.state = "paused";
    this._stopTicking();
    this.graph?.fadeTo(0, fade);
    this._emit();
    setTimeout(() => {
      if (seq === this._seq && this.state === "paused") this.ctx?.suspend().catch(() => {});
    }, fade * 1000 + 100);
    return this.status();
  }

  async resume({ fade = this.pauseFadeSeconds } = {}) {
    if (this.state !== "paused") return this.state === "stopped" ? this.start() : this.status();
    const seq = ++this._seq;
    await this._resumeContext();
    if (seq !== this._seq) return this.status();
    this.state = "playing";
    this.graph?.fadeTo(1, fade);
    this._startTicking();
    this._emit();
    return this.status();
  }

  toggle() { return this.state === "playing" ? this.pause() : this.state === "paused" ? this.resume() : this.start(); }

  /** Change parameters; audible changes glide over `seconds`. */
  set(patch = {}, { seconds = 0.4 } = {}) {
    const next = cleanParams(patch, this.params);
    const timerChanged = next.timer !== this.params.timer;
    this.params = next;
    this.graph?.set(next, seconds);
    if (timerChanged) this.setTimer(next.timer);
    else this._emit();
    return this.status();
  }

  /** Minutes (0 = off). While playing, the countdown starts now. */
  setTimer(minutes) {
    this.params = cleanParams({ timer: minutes }, this.params);
    return this.setTimerSeconds(this.params.timer * 60, { keepParam: true });
  }

  /** Seconds — the fine-grained form (tests use it to shorten a session). */
  setTimerSeconds(seconds, { keepParam = false } = {}) {
    seconds = Math.max(0, Number(seconds) || 0);
    if (!keepParam) this.params = { ...this.params, timer: seconds / 60 };
    this.timerTotal = seconds;
    this.timerLeft = seconds;
    this._emit();
    return this.status();
  }

  _startTicking() {
    this._stopTicking();
    this._lastTick = performance.now();
    this._tick = setInterval(() => this._onTick(), 250);
  }
  _stopTicking() { clearInterval(this._tick); this._tick = null; }
  _onTick() {
    const now = performance.now();
    const dt = (now - this._lastTick) / 1000; // wall clock: throttled timers still count right
    this._lastTick = now;
    if (this.state !== "playing") return;
    const before = Math.floor(this.elapsed);
    this.elapsed += dt;
    if (this.timerTotal > 0) {
      this.timerLeft = Math.max(0, this.timerLeft - dt);
      if (this.timerLeft <= 0) { this.stop({ reason: "timer" }); return; }
    }
    if (Math.floor(this.elapsed) !== before) this._emit();
  }

  _dropGraph(fade) {
    const g = this.graph;
    this.graph = null;
    if (!g) return;
    if (fade > 0) g.fadeTo(0, fade);
    setTimeout(() => g.teardown(), fade * 1000 + 200);
  }

  /** Render `seconds` of the drone offline (no fade-in) — for tests and previews. */
  static async renderOffline({ params, seconds = 8, sampleRate = 22050, seed = 1, fadeIn = 0 } = {}) {
    const OAC = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
    const ctx = new OAC(2, Math.ceil(seconds * sampleRate), sampleRate);
    const g = buildFocusGraph(ctx, params, ctx.destination, { seed, fadeIn });
    g.start(0);
    return ctx.startRendering();
  }
}
