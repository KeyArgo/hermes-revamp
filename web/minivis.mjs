// REVamp's own main-window analyzer: drawn over Webamp's 76x16 one at the screen's real
// resolution, with smooth bars, a soft glow and falling peak caps in the skin's colours.
//
// Cheap by design. It draws only while music plays and the page is visible, on a frame
// budget (30 fps on a GPU, 12 under software WebGL, where every frame makes the host
// repaint its window on the CPU), with no shadowBlur or filters. Webamp's own analyzer
// stops drawing while this one is attached (its loop honours window.__revampVisFps).

const BARS = 19;            // 76 px / 4: Winamp's band count
const MIN_HZ = 50;
const MAX_HZ = 16000;
const ATTACK = 0.55;        // share of the gap a bar closes per frame going up
const RELEASE = 0.12;       // ...and coming down: fluid, not jittery
const PEAK_HOLD_MS = 420;
const PEAK_GRAVITY = 1.6;   // bar-heights per second squared
const NOISE_FLOOR = 0.3;

export const MINIVIS_FPS = 30;
export const MINIVIS_SOFTWARE_FPS = 12;

// Winamp viscolor layout: 2..17 spectrum top to bottom, 23 peak dots.
export function paletteFrom(skinColors) {
  const c = Array.isArray(skinColors) ? skinColors : [];
  const pick = (i, fallback) => (typeof c[i] === "string" && c[i]) || fallback;
  const top = pick(2, "rgb(239,49,16)"), mid = pick(9, "rgb(206,214,41)"), bottom = pick(17, "rgb(24,132,8)");
  // spectrum[0] is the top colour, spectrum[15] the bottom (Winamp order).
  const spectrum = Array.from({ length: 16 }, (_, i) => pick(2 + i, i < 6 ? top : i < 11 ? mid : bottom));
  return { top, mid, bottom, peak: pick(23, "rgb(150,150,150)"), spectrum };
}

// Log-spaced bin edges for BARS bands of an analyser with binCount bins at sampleRate.
export function bandEdges(binCount, sampleRate, bars = BARS) {
  const nyquist = sampleRate / 2;
  const edges = [];
  for (let i = 0; i <= bars; i++) {
    const hz = MIN_HZ * Math.pow(MAX_HZ / MIN_HZ, i / bars);
    edges.push(Math.min(binCount - 1, Math.max(1, Math.round((hz / nyquist) * binCount))));
  }
  for (let i = 1; i < edges.length; i++) if (edges[i] <= edges[i - 1]) edges[i] = Math.min(binCount, edges[i - 1] + 1);
  return edges;
}

// One frame of easing: bars rise quickly, fall slowly; peaks hold, then fall with gravity.
export function step(state, targets, dt, now) {
  for (let i = 0; i < targets.length; i++) {
    const v = state.level[i];
    const t = targets[i];
    state.level[i] = v + (t - v) * (t > v ? ATTACK : RELEASE);
    if (state.level[i] >= state.peak[i]) {
      state.peak[i] = state.level[i]; state.peakAt[i] = now; state.peakV[i] = 0;
    } else if (now - state.peakAt[i] > PEAK_HOLD_MS) {
      state.peakV[i] += PEAK_GRAVITY * dt;
      state.peak[i] = Math.max(state.level[i], state.peak[i] - state.peakV[i] * dt);
    }
  }
}

export function freshState(bars = BARS) {
  return { level: new Float32Array(bars), peak: new Float32Array(bars), peakAt: new Float64Array(bars), peakV: new Float32Array(bars) };
}

// Options (read each frame, so a settings change applies at once):
//   getStyle() -> { coloring: "normal" | "fire" | "line", peaks: boolean }
//     normal: colour by height on screen (Winamp's default), fire: each bar's tip takes
//     the top colour and the palette runs down from there (Winamp's fire), line: one colour per bar.
//   isVisible() -> false while the player is parked offscreen (Mini, hidden in Hermes).
export function createMiniVis({ getAnalyser, getColors, isPlaying, isVisible = () => true, getStyle = () => ({}), software }) {
  const fps = software ? MINIVIS_SOFTWARE_FPS : MINIVIS_FPS;
  const gap = 1000 / fps;
  let canvas = null, ctx = null, target = null, raf = 0, last = 0, lastDraw = 0;
  let edges = null, bins = null, edgeKey = "", gradient = null, gradKey = "";
  const state = freshState();
  const resizer = typeof ResizeObserver === "function" ? new ResizeObserver(() => place()) : null;

  function place() {
    if (!canvas || !target) return;
    const cs = getComputedStyle(target);
    const w = target.offsetWidth, h = target.offsetHeight;
    Object.assign(canvas.style, { position: "absolute", left: cs.left, top: cs.top, width: `${w}px`, height: `${h}px` });
    // Device pixels: the zoom and screen scale Webamp's blocky canvas ignores.
    // At least 3x even on a 1x screen: smooth, anti-aliased edges and sub-pixel bar heights
    // for the price of a 228x48 canvas.
    const scale = Math.max(3, Math.min(4, (window.devicePixelRatio || 1) * (target.getBoundingClientRect().width / (w || 1))));
    const pw = Math.round(w * scale), ph = Math.round(h * scale);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; gradKey = ""; }
  }

  function attach(visCanvas) {
    // Same canvas: nothing to do. This runs on every Webamp DOM change, and place()
    // forces a style and layout read; size changes come through the ResizeObserver.
    if (!visCanvas || (visCanvas === target && canvas?.isConnected)) return;
    target = visCanvas;
    resizer?.disconnect();
    resizer?.observe(target);
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "revamp-minivis";
      canvas.setAttribute("aria-hidden", "true");
      canvas.style.pointerEvents = "none"; // clicks still reach Webamp's analyzer (mode cycling)
      ctx = canvas.getContext("2d");
    }
    target.insertAdjacentElement("afterend", canvas);
    // Opacity, not visibility: a hidden element takes no clicks, and a click on the
    // analyzer must still reach Webamp to cycle spectrum / oscilloscope / off.
    target.style.opacity = "0";
    place();
    wake();
  }

  function detach() {
    stop();
    resizer?.disconnect();
    if (target) target.style.opacity = "";
    canvas?.remove();
    target = null;
  }

  function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

  function wake() {
    if (!raf && canvas && !document.hidden && isVisible()) { last = 0; raf = requestAnimationFrame(frame); }
  }

  function readTargets(analyser) {
    const n = analyser.frequencyBinCount;
    const key = `${n}:${analyser.context.sampleRate}`;
    if (key !== edgeKey) { edges = bandEdges(n, analyser.context.sampleRate); bins = new Uint8Array(n); edgeKey = key; }
    analyser.getByteFrequencyData(bins);
    const out = new Float32Array(BARS);
    for (let b = 0; b < BARS; b++) {
      let peak = 0;
      for (let k = edges[b]; k < edges[b + 1]; k++) if (bins[k] > peak) peak = bins[k];
      // The analyser's byte scale starts at -100 dB, so near-silence still reads about a
      // quarter full: drop that floor, then a gentle curve so loud passages don't pin.
      out[b] = Math.pow(Math.max(0, (peak / 255 - NOISE_FLOOR) / (1 - NOISE_FLOOR)), 1.2);
    }
    return out;
  }

  function paint() {
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const colors = paletteFrom(getColors());
    const style = getStyle() || {};
    const coloring = style.coloring === "fire" || style.coloring === "line" ? style.coloring : "normal";
    const gk = `${H}:${colors.top}:${colors.mid}:${colors.bottom}`;
    if (gk !== gradKey) {
      gradient = ctx.createLinearGradient(0, H, 0, 0);
      gradient.addColorStop(0, colors.bottom); gradient.addColorStop(0.55, colors.mid); gradient.addColorStop(1, colors.top);
      gradKey = gk;
    }
    const slot = W / BARS, bw = Math.max(1, slot * 0.72), r = Math.min(bw / 2, H * 0.06);
    // fire: shift the shared gradient so each bar's tip gets the top colour (no per-bar
    // gradient objects); line: one skin colour picked by the bar's height.
    const fillFor = (h) => coloring === "line" ? colors.spectrum[Math.min(15, Math.max(0, Math.round((1 - h / H) * 15)))] : gradient;
    const shift = (h) => (coloring === "fire" ? -(H - h) : 0);
    const bar = (i, grow, rounded) => {
      const h = state.level[i] * H;
      if (h < 0.5) return;
      const x = i * slot + (slot - bw) / 2 - (bw * grow) / 2;
      const w = bw * (1 + grow);
      ctx.fillStyle = fillFor(h);
      const dy = shift(h);
      ctx.translate(0, -dy);
      ctx.beginPath();
      if (rounded && ctx.roundRect) ctx.roundRect(x, H - h + dy, w, h, [r, r, 0, 0]);
      else ctx.rect(x, H - h + dy - (rounded ? 0 : 1), w, h + (rounded ? 0 : 1));
      ctx.fill();
      ctx.translate(0, dy);
    };
    // Glow: the same bars, wider and faint, behind. No blur filter: that is costly in software.
    ctx.globalAlpha = 0.22;
    for (let i = 0; i < BARS; i++) bar(i, 0.36, false);
    ctx.globalAlpha = 1;
    for (let i = 0; i < BARS; i++) bar(i, 0, true);
    if (style.peaks === false) return;
    ctx.fillStyle = colors.peak;
    const cap = Math.max(1, H * 0.07);
    for (let i = 0; i < BARS; i++) {
      if (state.peak[i] < 0.02) continue;
      const x = i * slot + (slot - bw) / 2;
      ctx.fillRect(x, Math.min(H - cap, H - state.peak[i] * H - cap), bw, cap);
    }
  }

  function frame(ts) {
    raf = 0;
    if (!canvas || !canvas.isConnected || document.hidden || !isVisible()) return; // woken when shown
    const playing = isPlaying();
    const resting = !playing && state.level.every((v) => v < 0.004) && state.peak.every((v) => v < 0.004);
    if (resting) { ctx.clearRect(0, 0, canvas.width, canvas.height); return; } // idle: no loop at all
    raf = requestAnimationFrame(frame);
    if (ts - lastDraw < gap - 2) return;
    const dt = Math.min(0.1, last ? (ts - last) / 1000 : gap / 1000);
    last = ts; lastDraw = ts;
    const analyser = playing ? getAnalyser() : null;
    const targets = analyser ? readTargets(analyser) : new Float32Array(BARS); // paused: bars settle to zero
    step(state, targets, dt, ts);
    paint();
  }

  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : wake()));
  window.addEventListener("resize", place);

  return { attach, detach, wake, place, get canvas() { return canvas; } };
}
