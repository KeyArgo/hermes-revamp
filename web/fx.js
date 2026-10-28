// fx.js — optional animated backdrops for the REVamp panel: embers/sparks, fire, aurora,
// stars and rain. One fixed canvas behind the UI, drawn at half resolution and capped at
// ~30fps; it stops while the tab is hidden and is off unless the viewer picks an effect.
// Colours follow the active theme (--accent / --accent-2); fire keeps its own palette.

export const FX_KINDS = [
  { id: "off", name: "Off", group: "" },
  { id: "embers", name: "Embers & sparks", group: "Warm" },
  { id: "fire", name: "Fire", group: "Warm" },
  { id: "sakura", name: "Sakura petals", group: "Nature" },
  { id: "snow", name: "Snowfall", group: "Nature" },
  { id: "fireflies", name: "Fireflies", group: "Nature" },
  { id: "rain", name: "Rain", group: "Nature" },
  { id: "storm", name: "Thunderstorm", group: "Nature" },
  { id: "aurora", name: "Aurora", group: "Sky & space" },
  { id: "stars", name: "Stars & shooting star", group: "Sky & space" },
  { id: "warp", name: "Warp speed", group: "Sky & space" },
  { id: "matrix", name: "Matrix code", group: "Tech & water" },
  { id: "bubbles", name: "Bubbles", group: "Tech & water" },
];

const SCALE = 0.5;
let canvas = null, ctx = null, raf = 0, last = 0;
let kind = "off", level = 2, W = 1, H = 1, parts = [], sprites = new Map(), auroraGrads = [];
let shoot = null, nextShoot = 3;
// The loop runs only while the user can see the page. hostVisible is a message from the
// Hermes frame (the iframe's own IntersectionObserver cannot always see a parked parent).
// observedVisible is that observer. modeOverride is the latest revamp:mode from the host.
let hostVisible = true, observedVisible = true, modeOverride = null, io = null, watched = false;

const rnd = (a, b) => a + Math.random() * (b - a);
const hexRgb = (h) => { const n = parseInt(String(h).replace("#", ""), 16) || 0; return [n >> 16, (n >> 8) & 255, n & 255]; };
const themeColor = (name, dflt) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return /^#[0-9a-f]{6}$/i.test(v) ? v : dflt;
};

// A soft round glow, pre-rendered once per colour so frames are cheap drawImage calls.
function glow(rgb, size = 32) {
  const key = `${rgb}|${size}`;
  let c = sprites.get(key);
  if (c) return c;
  c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const r = size / 2, grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, `rgba(${rgb}, 1)`);
  grad.addColorStop(0.35, `rgba(${rgb}, 0.55)`);
  grad.addColorStop(1, `rgba(${rgb}, 0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  sprites.set(key, c);
  return c;
}

function count(perPx, min, max) {
  return Math.max(min, Math.min(max, Math.round(W * H * perPx * level)));
}

// ---------------------------------------------------------------- effects
const EFFECTS = {
  embers: {
    seed() {
      const a = hexRgb(themeColor("--accent", "#4cc2ff")).join(","), b = hexRgb(themeColor("--accent-2", "#b18cff")).join(",");
      this.cols = ["255,170,70", "255,110,40", a, b];
      parts = Array.from({ length: count(0.0007, 10 * level, 120) }, () => this.spawn(true));
    },
    spawn(initial) {
      return { x: rnd(0, W), y: initial ? rnd(0, H) : H + 6, vx: rnd(-6, 6), vy: -rnd(14, 46), r: rnd(2.5, 7), life: rnd(2.5, 6), age: initial ? rnd(0, 3) : 0, ph: rnd(0, 6.28), col: this.cols[(Math.random() * this.cols.length) | 0] };
    },
    draw(dt, t) {
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.age += dt; p.y += p.vy * dt; p.x += (p.vx + Math.sin(t * 1.6 + p.ph) * 10) * dt;
        if (p.age > p.life || p.y < -10) { parts[i] = this.spawn(false); continue; }
        const f = p.age / p.life;
        ctx.globalAlpha = Math.min(1, f * 6) * (1 - f) * 0.9;
        const s = p.r * (1.2 - f * 0.5) * 2;
        ctx.drawImage(glow(p.col), p.x - s / 2, p.y - s / 2, s, s);
      }
    },
  },
  fire: {
    seed() {
      this.stages = ["255,226,120", "255,140,40", "230,60,20", "120,20,10"];
      parts = Array.from({ length: count(0.0016, 24 * level, 260) }, () => this.spawn(true));
      this.sparks = Array.from({ length: 6 * level }, () => this.spark());
    },
    spawn(initial) {
      const size = rnd(14, 30) * (0.6 + level * 0.25);
      return { x: rnd(-10, W + 10), y: H + rnd(0, 10) - (initial ? rnd(0, H * 0.3) : 0), vy: -rnd(26, 62), vx: rnd(-8, 8), r: size, life: rnd(1.2, 2.6), age: initial ? rnd(0, 1.5) : 0, ph: rnd(0, 6.28) };
    },
    spark() { return { x: rnd(0, W), y: H, vx: rnd(-14, 14), vy: -rnd(50, 130), life: rnd(0.8, 1.9), age: rnd(0, 1.2) }; },
    draw(dt, t) {
      // Warm floor glow so the flames have something to burn from.
      const floor = ctx.createLinearGradient(0, H, 0, H * 0.55);
      floor.addColorStop(0, "rgba(255, 90, 20, 0.22)"); floor.addColorStop(1, "rgba(255, 90, 20, 0)");
      ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1; ctx.fillStyle = floor; ctx.fillRect(0, H * 0.55, W, H * 0.45);
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.age += dt; p.y += p.vy * dt; p.x += (p.vx + Math.sin(t * 3 + p.ph) * 12) * dt;
        const f = p.age / p.life;
        if (f >= 1) { parts[i] = this.spawn(false); continue; }
        const stage = this.stages[Math.min(3, (f * 4) | 0)];
        ctx.globalAlpha = 0.55 * (1 - f * 0.6);
        const s = p.r * (1 - f * 0.75) * 2;
        ctx.drawImage(glow(stage, 48), p.x - s / 2, p.y - s / 2, s, s);
      }
      for (let i = 0; i < this.sparks.length; i++) {
        const s = this.sparks[i];
        s.age += dt; s.y += s.vy * dt; s.x += s.vx * dt;
        if (s.age > s.life) { this.sparks[i] = this.spark(); continue; }
        ctx.globalAlpha = 1 - s.age / s.life;
        ctx.drawImage(glow("255,220,140", 16), s.x - 3, s.y - 3, 6, 6);
      }
    },
  },
  aurora: {
    seed() {
      const a = hexRgb(themeColor("--accent", "#4cc2ff")).join(","), b = hexRgb(themeColor("--accent-2", "#b18cff")).join(",");
      const cols = [a, b, "80,255,190"];
      auroraGrads = cols.map((c) => {
        const g = ctx.createLinearGradient(0, 0, 0, H * 0.5);
        g.addColorStop(0, `rgba(${c}, 0)`); g.addColorStop(0.45, `rgba(${c}, 0.55)`); g.addColorStop(1, `rgba(${c}, 0)`);
        return g;
      });
    },
    draw(dt, t) {
      ctx.globalCompositeOperation = "lighter";
      const bands = Math.min(3, 1 + level);
      for (let i = 0; i < bands; i++) {
        ctx.fillStyle = auroraGrads[i];
        ctx.globalAlpha = 0.5 + 0.2 * Math.sin(t * 0.5 + i);
        for (let x = 0; x < W; x += 3) {
          const y = H * (0.12 + 0.12 * i) + Math.sin(x * 0.018 + t * 0.55 + i * 2.1) * H * 0.07 + Math.sin(x * 0.047 - t * 0.3) * H * 0.03;
          ctx.save(); ctx.translate(0, y); ctx.fillRect(x, 0, 3, H * 0.5); ctx.restore();
        }
      }
    },
  },
  stars: {
    seed() {
      parts = Array.from({ length: count(0.0009, 20 * level, 160) }, () => ({ x: rnd(0, W), y: rnd(0, H), r: rnd(0.6, 1.8), ph: rnd(0, 6.28), sp: rnd(0.6, 2.2), vx: rnd(-1.5, -0.2) }));
      shoot = null; nextShoot = rnd(2, 5);
    },
    draw(dt, t) {
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = "#fff";
      for (const p of parts) {
        p.x += p.vx * dt; if (p.x < -2) p.x = W + 2;
        ctx.globalAlpha = 0.25 + 0.65 * (0.5 + 0.5 * Math.sin(t * p.sp + p.ph));
        ctx.fillRect(p.x, p.y, p.r, p.r);
      }
      nextShoot -= dt;
      if (!shoot && nextShoot <= 0) { shoot = { x: rnd(W * 0.3, W), y: rnd(0, H * 0.3), vx: -rnd(160, 260), vy: rnd(70, 120), age: 0 }; nextShoot = rnd(4, 9); }
      if (shoot) {
        shoot.age += dt; shoot.x += shoot.vx * dt; shoot.y += shoot.vy * dt;
        const g = ctx.createLinearGradient(shoot.x, shoot.y, shoot.x - shoot.vx * 0.12, shoot.y - shoot.vy * 0.12);
        g.addColorStop(0, "rgba(255,255,255,0.9)"); g.addColorStop(1, "rgba(255,255,255,0)");
        ctx.globalAlpha = 1; ctx.strokeStyle = g; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(shoot.x - shoot.vx * 0.12, shoot.y - shoot.vy * 0.12); ctx.stroke();
        if (shoot.age > 0.9) shoot = null;
      }
    },
  },
  sakura: {
    seed() {
      this.cols = ["255,183,205", "255,214,226", "255,150,185"];
      parts = Array.from({ length: count(0.0008, 14 * level, 110) }, () => this.petal(true));
    },
    petal(initial) {
      return { x: rnd(-20, W + 20), y: initial ? rnd(0, H) : -10, vy: rnd(14, 34), vx: rnd(-12, -2), r: rnd(2.2, 4.6), rot: rnd(0, 6.28), vr: rnd(-1.6, 1.6), ph: rnd(0, 6.28), col: this.cols[(Math.random() * this.cols.length) | 0] };
    },
    draw(dt, t) {
      ctx.globalCompositeOperation = "source-over";
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.y += p.vy * dt; p.x += (p.vx + Math.sin(t * 1.2 + p.ph) * 14) * dt; p.rot += p.vr * dt;
        if (p.y > H + 10 || p.x < -24) { parts[i] = this.petal(false); continue; }
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.scale(1, 0.55 + 0.45 * Math.abs(Math.sin(t * 1.5 + p.ph)));
        ctx.globalAlpha = 0.75; ctx.fillStyle = `rgb(${p.col})`;
        ctx.beginPath(); ctx.ellipse(0, 0, p.r, p.r * 0.62, 0, 0, 6.2832); ctx.fill();
        ctx.restore();
      }
    },
  },
  snow: {
    seed() { parts = Array.from({ length: count(0.0012, 24 * level, 220) }, () => this.flake(true)); },
    flake(initial) { return { x: rnd(0, W), y: initial ? rnd(0, H) : -6, v: rnd(10, 32), r: rnd(0.8, 2.6), ph: rnd(0, 6.28), sw: rnd(4, 16) }; },
    draw(dt, t) {
      ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = "#fff";
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.y += p.v * dt; p.x += Math.sin(t * 0.8 + p.ph) * p.sw * dt;
        if (p.y > H + 6) { parts[i] = this.flake(false); continue; }
        ctx.globalAlpha = 0.35 + p.r / 5;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.2832); ctx.fill();
      }
    },
  },
  fireflies: {
    seed() { parts = Array.from({ length: count(0.0003, 8 * level, 60) }, () => ({ x: rnd(0, W), y: rnd(0, H), a: rnd(0, 6.28), v: rnd(3, 10), ph: rnd(0, 6.28), sp: rnd(0.6, 1.6), r: rnd(3, 6.5) })); },
    draw(dt, t) {
      ctx.globalCompositeOperation = "lighter";
      for (const p of parts) {
        p.a += rnd(-1.2, 1.2) * dt; p.x += Math.cos(p.a) * p.v * dt; p.y += Math.sin(p.a) * p.v * dt;
        if (p.x < -10) p.x = W + 10; if (p.x > W + 10) p.x = -10; if (p.y < -10) p.y = H + 10; if (p.y > H + 10) p.y = -10;
        ctx.globalAlpha = Math.max(0, Math.sin(t * p.sp + p.ph)) ** 2;
        const s = p.r * 2.4; ctx.drawImage(glow("200,255,120", 32), p.x - s / 2, p.y - s / 2, s, s);
      }
    },
  },
  bubbles: {
    seed() {
      this.col = hexRgb(themeColor("--accent", "#4cc2ff")).join(",");
      parts = Array.from({ length: count(0.0005, 10 * level, 80) }, () => this.bub(true));
    },
    bub(initial) { return { x: rnd(0, W), y: initial ? rnd(0, H) : H + 14, r: rnd(3, 12), v: rnd(10, 30), ph: rnd(0, 6.28) }; },
    draw(dt, t) {
      ctx.globalCompositeOperation = "lighter"; ctx.lineWidth = 1;
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.y -= p.v * dt; p.x += Math.sin(t * 1.1 + p.ph) * 8 * dt;
        if (p.y < -16) { parts[i] = this.bub(false); continue; }
        ctx.globalAlpha = 0.5; ctx.strokeStyle = `rgba(${this.col}, 0.8)`;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.2832); ctx.stroke();
        ctx.globalAlpha = 0.5; ctx.fillStyle = "#fff"; ctx.fillRect(p.x - p.r * 0.45, p.y - p.r * 0.5, Math.max(1, p.r * 0.25), Math.max(1, p.r * 0.25));
      }
    },
  },
  warp: {
    seed() { parts = Array.from({ length: count(0.0009, 30 * level, 260) }, () => this.star(true)); },
    star(initial) { return { x: rnd(-1, 1), y: rnd(-1, 1), z: initial ? rnd(0.05, 1) : 1 }; },
    draw(dt) {
      ctx.globalCompositeOperation = "lighter"; ctx.lineWidth = 1;
      const cx = W / 2, cy = H / 2, k = Math.max(W, H);
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i], z0 = p.z; p.z -= dt * 0.45;
        if (p.z <= 0.02) { parts[i] = this.star(false); continue; }
        const x0 = cx + (p.x / z0) * k * 0.16, y0 = cy + (p.y / z0) * k * 0.16;
        const x1 = cx + (p.x / p.z) * k * 0.16, y1 = cy + (p.y / p.z) * k * 0.16;
        if (x1 < -20 || x1 > W + 20 || y1 < -20 || y1 > H + 20) { parts[i] = this.star(false); continue; }
        ctx.globalAlpha = Math.min(1, (1 - p.z) * 1.4); ctx.strokeStyle = "#cfe6ff";
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      }
    },
  },
  storm: {
    seed() { EFFECTS.rain.seed(); this.flash = 0; this.bolt = null; this.next = rnd(2, 5); },
    draw(dt, t) {
      EFFECTS.rain.draw(dt, t);
      this.next -= dt;
      if (this.next <= 0) {
        this.flash = 1; this.next = rnd(4, 9);
        const pts = [[rnd(W * 0.2, W * 0.8), 0]];
        while (pts[pts.length - 1][1] < H * 0.7) { const l = pts[pts.length - 1]; pts.push([l[0] + rnd(-18, 18), l[1] + rnd(14, 34)]); }
        this.bolt = pts;
      }
      if (this.flash > 0) {
        ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = this.flash * 0.28; ctx.fillStyle = "#cfe0ff"; ctx.fillRect(0, 0, W, H);
        if (this.bolt && this.flash > 0.5) {
          ctx.globalAlpha = this.flash; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.6; ctx.beginPath();
          this.bolt.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke();
        }
        this.flash -= dt * 2.2;
      }
    },
  },
  matrix: {
    seed() {
      this.col = hexRgb(themeColor("--accent", "#4cc2ff"));
      this.chars = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎ0123456789";
      const cols = Math.max(6, Math.floor(W / 9));
      parts = Array.from({ length: cols }, (_, i) => ({ x: i * 9 + 4, y: rnd(-H, 0), v: rnd(30, 90) * (0.7 + level * 0.2), len: Math.floor(rnd(8, 18)) }));
    },
    draw(dt) {
      ctx.globalCompositeOperation = "lighter"; ctx.font = "9px monospace"; ctx.textAlign = "center";
      const [r, g, b] = this.col;
      for (const p of parts) {
        p.y += p.v * dt;
        if (p.y - p.len * 10 > H) { p.y = rnd(-60, 0); p.v = rnd(30, 90) * (0.7 + level * 0.2); }
        for (let k = 0; k < p.len; k++) {
          const y = p.y - k * 10; if (y < -10 || y > H + 10) continue;
          ctx.globalAlpha = k === 0 ? 0.95 : (1 - k / p.len) * 0.5;
          ctx.fillStyle = k === 0 ? "#fff" : `rgb(${r},${g},${b})`;
          ctx.fillText(this.chars[(Math.floor(y / 10) * 7 + p.x) % this.chars.length | 0], p.x, y);
        }
      }
    },
  },
  rain: {
    seed() {
      this.col = hexRgb(themeColor("--accent", "#4cc2ff")).join(",");
      parts = Array.from({ length: count(0.0011, 20 * level, 200) }, () => this.drop(true));
    },
    drop(initial) { return { x: rnd(-20, W + 20), y: initial ? rnd(0, H) : -10, v: rnd(160, 300), len: rnd(5, 12) }; },
    draw(dt) {
      ctx.globalCompositeOperation = "lighter";
      ctx.strokeStyle = `rgba(${this.col}, 0.5)`; ctx.lineWidth = 1; ctx.globalAlpha = 0.6;
      ctx.beginPath();
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        p.y += p.v * dt; p.x -= p.v * 0.18 * dt;
        if (p.y > H + 10) { parts[i] = this.drop(false); continue; }
        ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + p.len * 0.18, p.y - p.len);
      }
      ctx.stroke();
    },
  },
};

// ---------------------------------------------------------------- engine
function winampOnly() {
  // The body class is the page's actual mode (set on boot and on revamp:mode).
  // The host message covers the moment before that class lands.
  if (document.body?.classList.contains("mode-winamp")) return true;
  if (modeOverride) return modeOverride === "winamp";
  try {
    const q = new URLSearchParams(location.search);
    if (q.get("mode") === "winamp" || q.has("float")) return true;
  } catch { /* no location */ }
  return false;
}
// True only while the user can see this page: the tab is showing, the canvas has
// a box on screen (not parked, zero-size, or display:none), the host has not
// said the iframe is hidden, and this is not Winamp-only mode (the backdrop
// is not on screen there).
export function fxShown() {
  return !document.hidden && hostVisible && observedVisible && !winampOnly();
}
function sync() {
  if (kind === "off" || !fxShown()) stop();
  else run();
}
function onHostMessage(e) {
  if (window.parent !== window && e.source !== window.parent) return;
  const d = e.data;
  if (!d || typeof d !== "object") return;
  if (d.type === "revamp:visible") { hostVisible = d.visible !== false; sync(); }
  if (d.type === "revamp:mode" && (d.mode === "winamp" || d.mode === "full")) { modeOverride = d.mode; sync(); }
}
function watchCanvas() {
  if (watched) return;
  watched = true;
  addEventListener("resize", () => { if (kind !== "off") size(); });
  document.addEventListener("visibilitychange", sync);
  addEventListener("message", onHostMessage);
  if (document.body && typeof MutationObserver === "function") {
    const mo = new MutationObserver(sync);
    mo.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }
  if (typeof IntersectionObserver === "function") {
    io = new IntersectionObserver((entries) => {
      const hit = entries[entries.length - 1];
      const box = hit?.boundingClientRect || { width: 0, height: 0 };
      observedVisible = !!(hit && hit.isIntersecting && box.width > 0 && box.height > 0);
      sync();
    });
  }
}
function ensure() {
  if (canvas) return;
  canvas = document.createElement("canvas");
  canvas.id = "revamp-fx";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  ctx = canvas.getContext("2d");
  watchCanvas();
  io?.observe(canvas);
}
function size() {
  W = Math.max(1, Math.ceil(innerWidth * SCALE));
  H = Math.max(1, Math.ceil(innerHeight * SCALE));
  canvas.width = W; canvas.height = H;
  EFFECTS[kind]?.seed();
}
function frame(ts) {
  if (!fxShown() || kind === "off") { stop(); return; }
  raf = requestAnimationFrame(frame);
  if (ts - last < 32) return;
  const dt = Math.min(0.1, (ts - last) / 1000 || 0.033);
  last = ts;
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, W, H);
  EFFECTS[kind]?.draw(dt, ts / 1000);
}
function run() { if (!raf && fxShown() && kind !== "off") { last = 0; raf = requestAnimationFrame(frame); } }
function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

export function setFx(next, intensity = 2) {
  const want = EFFECTS[next] ? next : "off";
  level = Math.min(3, Math.max(1, Number(intensity) || 2));
  if (want === "off") {
    kind = "off"; stop();
    if (canvas) canvas.style.display = "none";
    delete document.documentElement.dataset.revampFx;
    return;
  }
  ensure();
  kind = want; sprites = new Map();
  canvas.style.display = "";
  document.documentElement.dataset.revampFx = want;
  size();
  run();
}
