// fx.js — optional animated backdrops for the REVamp panel: embers/sparks, fire, aurora,
// stars and rain. One fixed canvas behind the UI, drawn at half resolution and capped at
// ~30fps; it stops while the tab is hidden and is off unless the viewer picks an effect.
// Colours follow the active theme (--accent / --accent-2); fire keeps its own palette.

export const FX_KINDS = [
  { id: "off", name: "Off" },
  { id: "embers", name: "Embers" },
  { id: "fire", name: "Fire" },
  { id: "aurora", name: "Aurora" },
  { id: "stars", name: "Stars" },
  { id: "rain", name: "Rain" },
  { id: "sakura", name: "Sakura" },
];

const SCALE = 0.5;
let canvas = null, ctx = null, raf = 0, last = 0;
let kind = "off", level = 2, W = 1, H = 1, parts = [], sprites = new Map(), auroraGrads = [];
let shoot = null, nextShoot = 3;

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
function ensure() {
  if (canvas) return;
  canvas = document.createElement("canvas");
  canvas.id = "revamp-fx";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  ctx = canvas.getContext("2d");
  addEventListener("resize", () => { if (kind !== "off") size(); });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : kind !== "off" && run()));
}
function size() {
  W = Math.max(1, Math.ceil(innerWidth * SCALE));
  H = Math.max(1, Math.ceil(innerHeight * SCALE));
  canvas.width = W; canvas.height = H;
  EFFECTS[kind]?.seed();
}
function frame(ts) {
  raf = requestAnimationFrame(frame);
  if (ts - last < 32) return;
  const dt = Math.min(0.1, (ts - last) / 1000 || 0.033);
  last = ts;
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, W, H);
  EFFECTS[kind]?.draw(dt, ts / 1000);
}
function run() { if (!raf && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); } }
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
