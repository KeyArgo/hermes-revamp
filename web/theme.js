// theme.js — colour schemes, background gradients and an accent colour for the REVamp
// panel. Everything rides on the CSS tokens in app.css (:root), so views never change.
// The choice is a per-viewer convenience kept in localStorage; "Auto" leaves the
// stylesheet untouched (including its light/dark media query).

const KEY = "revamp.theme.v1";
import { setFx, FX_KINDS } from "./fx.js";
const BG_KEY = "revamp.theme.bg.v1";

const THEMES = [
  { id: "auto", name: "Auto", swatch: ["#0b0e14", "#4cc2ff"], vars: null },
  { id: "midnight", name: "Midnight", swatch: ["#0b0e14", "#4cc2ff"], vars: {
    bg: "#0b0e14", "bg-2": "#10141d", panel: "rgba(22,27,39,0.88)", "panel-solid": "#161b27",
    "glass-brd": "rgba(255,255,255,0.09)", txt: "#dbe2f0", "txt-dim": "#8a94aa",
    accent: "#4cc2ff", "accent-2": "#b18cff", stage: "#070910" } },
  { id: "aurora", name: "Aurora", swatch: ["#08121a", "#2ee6b6"], vars: {
    bg: "#08121a", "bg-2": "#0d1b26", panel: "rgba(14,32,42,0.86)", "panel-solid": "#0f2230",
    "glass-brd": "rgba(120,255,220,0.12)", txt: "#d9f1ec", "txt-dim": "#82a8a6",
    accent: "#2ee6b6", "accent-2": "#6c8cff", stage: "#050d13" } },
  { id: "sunset", name: "Sunset", swatch: ["#150d12", "#ff8a5c"], vars: {
    bg: "#150d12", "bg-2": "#1d1219", panel: "rgba(38,21,30,0.86)", "panel-solid": "#26151e",
    "glass-brd": "rgba(255,160,130,0.13)", txt: "#f4e3dc", "txt-dim": "#b0948f",
    accent: "#ff8a5c", "accent-2": "#ff5c93", stage: "#0e080c" } },
  { id: "forest", name: "Forest", swatch: ["#0a130e", "#7ddc7a"], vars: {
    bg: "#0a130e", "bg-2": "#101c15", panel: "rgba(19,34,26,0.86)", "panel-solid": "#13221a",
    "glass-brd": "rgba(160,255,160,0.11)", txt: "#e0f0e2", "txt-dim": "#8aa592",
    accent: "#7ddc7a", "accent-2": "#c8e66a", stage: "#060d09" } },
  { id: "violet", name: "Violet", swatch: ["#100c1c", "#b18cff"], vars: {
    bg: "#100c1c", "bg-2": "#171128", panel: "rgba(31,23,50,0.86)", "panel-solid": "#1f1732",
    "glass-brd": "rgba(200,170,255,0.13)", txt: "#e6e0f7", "txt-dim": "#9a8fb8",
    accent: "#b18cff", "accent-2": "#ff7ad9", stage: "#090613" } },
  { id: "paper", name: "Paper", swatch: ["#eef1f7", "#0a86c8"], vars: {
    bg: "#eef1f7", "bg-2": "#e7ebf2", panel: "rgba(255,255,255,0.9)", "panel-solid": "#ffffff",
    "glass-brd": "rgba(20,30,50,0.12)", txt: "#1d2536", "txt-dim": "#5b6478",
    accent: "#0a86c8", "accent-2": "#7a5cff", stage: "#dfe4ee" } },
];

// Gradients: "theme" follows the scheme's own accents (the original soft glow);
// the others are fixed colour pairs; "custom" uses the two pickers.
const GRADIENTS = [
  { id: "theme", name: "Glow", a: null, b: null },
  { id: "aurora", name: "Aurora", a: "#2ee6b6", b: "#6c8cff" },
  { id: "sunset", name: "Sunset", a: "#ff8a5c", b: "#ff5c93" },
  { id: "ocean", name: "Ocean", a: "#4cc2ff", b: "#2b5cff" },
  { id: "grape", name: "Grape", a: "#b18cff", b: "#ff7ad9" },
  { id: "ember", name: "Ember", a: "#ff5c5c", b: "#ffb25c" },
  { id: "custom", name: "Custom", a: null, b: null },
  { id: "none", name: "None", a: null, b: null },
];

// Built-in scenes (original artwork, web/img/bg/*.svg).
const SCENES = [
  { id: "none", name: "None" },
  { id: "rooftop", name: "Moonlit rooftop", file: "rooftop.svg" },
  { id: "shrine", name: "Sakura shrine", file: "shrine.svg" },
  { id: "neon", name: "Neon city", file: "neon.svg" },
  { id: "lanterns", name: "Lantern night", file: "lanterns.svg" },
];
const sceneOf = (id) => SCENES.find((x) => x.id === id);

// Looks: one tap sets scheme + gradient + scene + effect together.
const LOOKS = [
  { id: "default", name: "Default", theme: "auto", grad: "theme", scene: "none", fx: "off", fxLevel: 2, thumb: "linear-gradient(135deg,#0b0e14,#1d3a52)" },
  { id: "moonlit", name: "Moonlit girl", theme: "violet", grad: "grape", scene: "rooftop", fx: "stars", fxLevel: 2 },
  { id: "sakura", name: "Sakura shrine", theme: "sunset", grad: "sunset", scene: "shrine", fx: "sakura", fxLevel: 2 },
  { id: "neon", name: "Neon city", theme: "violet", grad: "grape", scene: "neon", fx: "rain", fxLevel: 2 },
  { id: "lantern", name: "Lantern night", theme: "aurora", grad: "aurora", scene: "lanterns", fx: "embers", fxLevel: 1 },
  { id: "fire", name: "Campfire", theme: "sunset", grad: "ember", scene: "none", fx: "fire", fxLevel: 2, thumb: "linear-gradient(180deg,#150d12,#7a2a12 70%,#ff8a2a)" },
  { id: "aurora", name: "Aurora", theme: "aurora", grad: "aurora", scene: "none", fx: "aurora", fxLevel: 2, thumb: "linear-gradient(135deg,#08121a,#1c6a62 55%,#6c8cff)" },
  { id: "paper", name: "Paper", theme: "paper", grad: "theme", scene: "none", fx: "off", fxLevel: 2, thumb: "linear-gradient(135deg,#eef1f7,#c9d6ea)" },
];

const DEFAULTS = { theme: "auto", grad: "theme", accent: "", from: "#4cc2ff", to: "#b18cff", fx: "off", fxLevel: 2, bgStrength: 0.8, scene: "none", look: "default" };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
let bgData = null, explicitFx = false, bgNote = "", customOpen = false;
try { bgData = localStorage.getItem(BG_KEY) || null; } catch { /* no storage */ }
const root = document.documentElement;
let state = load();

function load() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { ...DEFAULTS }; }
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private window: still applies for this view */ } }
const hex = (v, d) => (/^#[0-9a-f]{6}$/i.test(v || "") ? v : d);
function rgba(h, a) {
  const n = parseInt(h.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
const themeOf = (id) => THEMES.find((t) => t.id === id) || THEMES[0];
const gradOf = (id) => GRADIENTS.find((g) => g.id === id) || GRADIENTS[0];

function gradientCss(accent, accent2) {
  const g = gradOf(state.grad);
  if (g.id === "none") return "none";
  if (g.id === "theme") {
    return `radial-gradient(1200px 600px at 80% -10%, ${rgba(accent, 0.1)}, transparent 60%), radial-gradient(900px 500px at 10% 110%, ${rgba(accent2, 0.08)}, transparent 60%)`;
  }
  const a = g.id === "custom" ? hex(state.from, "#4cc2ff") : g.a;
  const b = g.id === "custom" ? hex(state.to, "#b18cff") : g.b;
  return `linear-gradient(135deg, ${rgba(a, 0.2)} 0%, transparent 42%), radial-gradient(1000px 560px at 88% -8%, ${rgba(a, 0.3)}, transparent 62%), radial-gradient(900px 520px at 4% 112%, ${rgba(b, 0.28)}, transparent 62%)`;
}

export function applyTheme() {
  const t = themeOf(state.theme);
  const style = root.style;
  for (const k of ["bg", "bg-2", "panel", "panel-solid", "glass-brd", "txt", "txt-dim", "accent", "accent-2", "stage"]) style.removeProperty(`--${k}`);
  if (t.vars) for (const [k, v] of Object.entries(t.vars)) style.setProperty(`--${k}`, v);
  const accent = hex(state.accent, "");
  if (accent) style.setProperty("--accent", accent);
  // Resolve what the glow should follow: the scheme's accents, or the stylesheet's defaults.
  const cs = getComputedStyle(root);
  const a1 = hex(cs.getPropertyValue("--accent").trim(), "#4cc2ff");
  const a2 = hex(cs.getPropertyValue("--accent-2").trim(), "#b18cff");
  style.setProperty("--revamp-grad", gradientCss(a1, a2));
  root.dataset.revampTheme = t.id;
  const scene = sceneOf(state.scene);
  const src = state.scene === "custom" && bgData ? `url("${bgData}")` : scene?.file ? `url("/web/img/bg/${scene.file}")` : "";
  if (src) {
    style.setProperty("--revamp-bg-img", src);
    style.setProperty("--revamp-bg-op", String(Math.min(1, Math.max(0.1, Number(state.bgStrength) || 0.8))));
    root.dataset.revampBg = "1";
  } else {
    style.removeProperty("--revamp-bg-img"); style.removeProperty("--revamp-bg-op"); delete root.dataset.revampBg;
  }
  // Motion is opt-in; honour reduced-motion unless the viewer picked an effect this session.
  setFx(REDUCED.matches && !explicitFx ? "off" : state.fx, state.fxLevel);
}

// Pick any picture (anime art, a wallpaper): shrunk to <=1280px JPEG so it fits in localStorage.
function pickImage(file, done) {
  if (!file || !/^image\//.test(file.type)) return;
  const url = URL.createObjectURL(file), img = new Image();
  img.onload = () => {
    const k = Math.min(1, 1280 / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(img.width * k)); c.height = Math.max(1, Math.round(img.height * k));
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    URL.revokeObjectURL(url);
    bgData = c.toDataURL("image/jpeg", 0.82); bgNote = ""; state.scene = "custom"; state.look = "";
    try { localStorage.setItem(BG_KEY, bgData); } catch { bgNote = "Too big to remember after a reload."; }
    done();
  };
  img.onerror = () => { URL.revokeObjectURL(url); bgNote = "Could not read that image."; done(); };
  img.src = url;
}

// ---------------------------------------------------------------- picker UI
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v;
    else n.setAttribute(k, v);
  }
  n.append(...kids);
  return n;
}

export function mountThemeButton(head, before) {
  if (!head || document.getElementById("theme-btn")) return;
  const NS = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(NS, "svg");
  icon.setAttribute("viewBox", "0 0 16 16"); icon.setAttribute("width", "14"); icon.setAttribute("height", "14"); icon.setAttribute("aria-hidden", "true");
  const ring = document.createElementNS(NS, "circle");
  for (const [k, v] of Object.entries({ cx: "8", cy: "8", r: "6.25", fill: "none", stroke: "currentColor", "stroke-width": "1.5" })) ring.setAttribute(k, v);
  const half = document.createElementNS(NS, "path");
  half.setAttribute("d", "M8 1.75a6.25 6.25 0 0 1 0 12.5z"); half.setAttribute("fill", "currentColor");
  icon.append(ring, half);
  const btn = el("button", { class: "btn sm theme-btn", id: "theme-btn", type: "button", "aria-label": "Theme and colours", "aria-expanded": "false", title: "Theme and colours" }, icon, el("span", {}, "Theme"));
  const pop = el("div", { class: "theme-pop", id: "theme-pop", role: "dialog", "aria-label": "Theme and colours", hidden: "" });
  head.insertBefore(btn, before || null);
  document.body.append(pop);

  const render = () => {
    pop.replaceChildren();
    const schemes = el("div", { class: "theme-grid" });
    for (const t of THEMES) {
      const b = el("button", { class: "theme-chip", type: "button", "aria-pressed": String(state.theme === t.id), title: t.name },
        el("span", { class: "theme-dots", style: `background:linear-gradient(135deg, ${t.swatch[0]} 50%, ${t.swatch[1]} 50%)` }), t.name);
      b.addEventListener("click", () => { state.theme = t.id; touch(); });
      schemes.append(b);
    }
    const grads = el("div", { class: "theme-grid theme-grads" });
    for (const g of GRADIENTS) {
      const a = g.id === "custom" ? hex(state.from, "#4cc2ff") : g.a || "#4cc2ff";
      const c = g.id === "custom" ? hex(state.to, "#b18cff") : g.b || "#b18cff";
      const bg = g.id === "none" ? "repeating-linear-gradient(45deg,#555 0 3px,#333 3px 6px)" : `linear-gradient(135deg, ${a}, ${c})`;
      const b = el("button", { class: "theme-chip", type: "button", "aria-pressed": String(state.grad === g.id), title: g.name },
        el("span", { class: "theme-dots", style: `background:${bg}` }), g.name);
      b.addEventListener("click", () => { state.grad = g.id; touch(); });
      grads.append(b);
    }
    const color = (label, key, dflt) => {
      const i = el("input", { type: "color", value: hex(state[key], dflt), "aria-label": label });
      i.addEventListener("input", () => { state[key] = i.value; state.look = ""; if (key !== "accent") state.grad = "custom"; applyTheme(); save(); });
      i.addEventListener("change", render);
      return el("label", { class: "theme-color" }, i, label);
    };
    const customRow = el("div", { class: "theme-row" }, color("From", "from", "#4cc2ff"), color("To", "to", "#b18cff"));
    const accentRow = el("div", { class: "theme-row" }, color("Accent", "accent", "#4cc2ff"));
    const reset = el("button", { class: "btn sm", type: "button" }, "Reset");
    reset.addEventListener("click", () => { state = { ...DEFAULTS }; bgData = null; bgNote = ""; explicitFx = false; try { localStorage.removeItem(BG_KEY); } catch { /* ignore */ } commit(); });
    accentRow.append(reset);
    // One scrolling row (not a native dropdown, not a wall of chips): swipe or scroll sideways.
    const fxGrid = el("div", { class: "theme-strip", role: "group", "aria-label": "Animated effect" });
    for (const f of FX_KINDS) {
      const b = el("button", { class: "theme-chip", type: "button", "aria-pressed": String(state.fx === f.id) }, f.name);
      b.addEventListener("click", () => { state.fx = f.id; explicitFx = true; touch(); });
      fxGrid.append(b);
    }
    requestAnimationFrame(() => {
      const on = fxGrid.querySelector('[aria-pressed="true"]');
      if (on) fxGrid.scrollLeft = Math.max(0, on.offsetLeft - (fxGrid.clientWidth - on.offsetWidth) / 2);
    });
    const level = el("input", { type: "range", min: "1", max: "3", step: "1", value: String(state.fxLevel), "aria-label": "Effect intensity" });
    level.addEventListener("input", () => { state.fxLevel = Number(level.value); explicitFx = true; applyTheme(); save(); });
    const fxRow = el("div", { class: "theme-row" }, el("label", { class: "theme-color" }, "Intensity", level));
    const file = el("input", { type: "file", accept: "image/*", hidden: "" });
    file.addEventListener("change", () => pickImage(file.files[0], () => { applyTheme(); render(); }));
    const choose = el("button", { class: "btn sm", type: "button" }, bgData ? "Change your image…" : "Use your own image…");
    choose.addEventListener("click", () => file.click());
    const imgRow = el("div", { class: "theme-row" }, choose, file);
    if (bgData) {
      const rm = el("button", { class: "btn sm", type: "button" }, "Remove");
      rm.addEventListener("click", () => { bgData = null; bgNote = ""; if (state.scene === "custom") state.scene = "none"; state.look = ""; try { localStorage.removeItem(BG_KEY); } catch { /* ignore */ } applyTheme(); render(); });
      const str = el("input", { type: "range", min: "0.1", max: "1", step: "0.05", value: String(state.bgStrength), "aria-label": "Image strength" });
      str.addEventListener("input", () => { state.bgStrength = Number(str.value); applyTheme(); save(); });
      imgRow.append(rm, el("label", { class: "theme-color", style: "flex:1 1 100%" }, "Strength", str));
    }
    if (bgNote) imgRow.append(el("span", { class: "theme-color" }, bgNote));
    // Scene chips (built-in artwork + your own image).
    const scenes = el("div", { class: "theme-strip" });
    for (const sc of SCENES) {
      const b = el("button", { class: "theme-chip", type: "button", "aria-pressed": String(state.scene === sc.id) }, sc.name);
      b.addEventListener("click", () => { state.scene = sc.id; touch(); });
      scenes.append(b);
    }
    // Looks: big, visual, one tap.
    const looks = el("div", { class: "look-grid" });
    for (const lk of LOOKS) {
      const sc = sceneOf(lk.scene);
      const thumb = sc?.file ? `url(/web/img/bg/${sc.file}) center / cover` : lk.thumb;
      const b = el("button", { class: "look-card", type: "button", "aria-pressed": String(state.look === lk.id), title: lk.name },
        el("span", { class: "look-thumb", style: `background:${thumb}` }), el("span", { class: "look-name" }, lk.name));
      b.addEventListener("click", () => {
        state = { ...state, theme: lk.theme, grad: lk.grad, scene: lk.scene, fx: lk.fx, fxLevel: lk.fxLevel, accent: "", look: lk.id };
        explicitFx = true; commit();
      });
      looks.append(b);
    }
    // Customize opens as a second column to the LEFT of the picker (stacked below on narrow panes).
    const toggle = el("button", { class: "btn sm theme-customize", type: "button", "aria-expanded": String(customOpen) }, customOpen ? "Customize ‹" : "‹ Customize");
    toggle.addEventListener("click", () => { customOpen = !customOpen; render(); });
    const mainCol = el("div", { class: "theme-col" },
      el("div", { class: "theme-h" }, "Pick a look"), looks,
      el("div", { class: "theme-h" }, "Animation"), fxGrid, fxRow, toggle);
    pop.append(mainCol);
    requestAnimationFrame(() => {
      const on = looks.querySelector('[aria-pressed="true"]');
      if (on) looks.scrollLeft = Math.max(0, on.offsetLeft - (looks.clientWidth - on.offsetWidth) / 2);
    });
    if (customOpen) {
      pop.append(el("div", { class: "theme-col theme-side" },
        el("div", { class: "theme-h" }, "Colour scheme"), schemes,
        el("div", { class: "theme-h" }, "Scene"), scenes, imgRow,
        el("div", { class: "theme-h" }, "Gradient"), grads, customRow,
        el("div", { class: "theme-h" }, "Accent"), accentRow));
    }
  };
  const commit = () => { applyTheme(); save(); render(); };
  const touch = () => { state.look = ""; commit(); };
  const open = (on) => {
    pop.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
    if (on) { render(); pop.querySelector("button")?.focus({ preventScroll: true }); }
  };
  btn.addEventListener("themechange", () => { if (!pop.hidden) render(); });
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(pop.hidden); });
  document.addEventListener("click", (e) => { if (!pop.hidden && !e.composedPath().includes(pop) && e.target !== btn) open(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { open(false); btn.focus(); } });
}

// The control page and the player page are separate iframes on one origin, and Hermes keeps
// both alive: a change made in one arrives here as a storage event, so every surface
// (including the player after "Bring the player here") shows the same theme.
addEventListener("storage", (e) => {
  if (e.key !== null && e.key !== KEY && e.key !== BG_KEY) return;
  state = load();
  try { bgData = localStorage.getItem(BG_KEY) || null; } catch { bgData = null; }
  applyTheme();
  const pop = document.getElementById("theme-pop");
  if (pop && !pop.hidden) document.getElementById("theme-btn")?.dispatchEvent(new Event("themechange"));
});

applyTheme();
