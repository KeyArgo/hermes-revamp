// app.js — bootstrap: token, API client, shell wiring (nav/panel/np card), SSE, view registry.
import { initPlayer } from "./player.js";
import { contentSize, outerWindowSize } from "./float-layout.js";
import { renderMusic, renderBooks } from "./views/library.js";
import { render as renderPlaylists } from "./views/playlists.js";
import { render as renderRadio } from "./views/radio.js";
import { render as renderSkins } from "./views/skins.js";
import { render as renderAgent } from "./views/agent.js";
import { render as renderFocus } from "./views/focus.js";
import { maybeOfferRestore } from "./views/backups.js";
import { toast } from "./views/modal.js";
import { mountThemeButton } from "./theme.js";
import { labelHostPlace, setHostPlayerPlace } from "./host-place.js";

// ---------------------------------------------------------------- token
const TOKEN =
  document.querySelector('meta[name="revamp-token"]')?.content ||
  new URLSearchParams(location.search).get("t") ||
  "";

// Key for the Hermes plugin's bridge (see "REST bridge" below); read early, boot uses it.
const BRIDGE_KEY = document.querySelector('meta[name="revamp-bridge-key"]')?.content || "";

// ---------------------------------------------------------------- api
async function api(method, path, body) {
  const url = new URL(path, location.origin);
  const opts = { method, headers: { "X-Revamp-Token": TOKEN } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
    if (!res.ok) {
      let msg = `${res.status}`;
      try { msg = (await res.json()).error || msg; } catch { /* keep status */ }
      throw new Error(`API ${path}: ${msg}`);
    }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

export const apiGet = (p) => api("GET", p);
export const apiPost = (p, b) => api("POST", p, b ?? {});
export const apiPut = (p, b) => api("PUT", p, b);
export const apiDelete = (p) => api("DELETE", p);
export const token = TOKEN;

/** The views' calling convention: viewApi(path, method = "GET", body), plus .authedUrl. */
function viewApi(path, method = "GET", body) { return api(method, path, body); }
viewApi.authedUrl = (...a) => authedUrl(...a);
viewApi.token = TOKEN; // for fetches that send it as a header (never in a URL)

/** Authenticated absolute URL for media/streams/SSE/art. */
export function authedUrl(path, extra = {}) {
  const u = new URL(path, location.origin);
  u.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(extra)) u.searchParams.set(k, v);
  return u.toString();
}

// ---------------------------------------------------------------- shell: nav + views
const VIEWS = [
  { id: "hermes-radio", icon: "✦", label: "Hermes Radio", render: renderRadio },
  { id: "library", icon: "♬", label: "Music", render: renderMusic }, // id kept: the file picker opens it
  { id: "books", icon: "📖", label: "Books", render: renderBooks },
  { id: "focus", icon: "◎", label: "Focus", render: renderFocus },
  { id: "radio", icon: "((·))", label: "Radio", render: renderRadio },
  { id: "playlists", icon: "≡", label: "Playlists", render: renderPlaylists },
  { id: "skins", icon: "◧", label: "Skins", render: renderSkins },
  // Hermes activity and the volume dip while Hermes waits for your approval.
  { id: "agent", icon: "⚙", label: "Settings", render: renderAgent },
];

let activeView = "library";
let renderGeneration = 0;
export let player = null; // set by initPlayer; views use getPlayer()

export function getPlayer() { return player; }

export function setView(id) {
  const generation = ++renderGeneration;
  activeView = id;
  const view = VIEWS.find((v) => v.id === id);
  if (!view) return;
  document.getElementById("panel-title").textContent = view.label;
  document.querySelectorAll("#nav button, #tabbar button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === id);
    b.setAttribute("aria-current", b.dataset.view === id ? "page" : "false");
  });
  const body = document.getElementById("panel-body");
  body.innerHTML = "";
  body.setAttribute("aria-busy", "true");
  const viewRoot = document.createElement("div");
  body.appendChild(viewRoot);
  Promise.resolve(view.render(viewRoot, viewApi)).catch((e) => {
    if (generation !== renderGeneration) return;
    viewRoot.innerHTML = "";
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = `Failed to load ${view.label}: ${e.message}`;
    viewRoot.appendChild(div);
  })
    .finally(() => {
      if (generation === renderGeneration) body.setAttribute("aria-busy", "false");
    });
  document.body.classList.remove("panel-open");
}
window.revampSetView = setView; // for webamp filePicker callbacks / debugging

function buildNav() {
  const nav = document.getElementById("nav");
  const tabbar = document.getElementById("tabbar");
  for (const v of VIEWS) {
    const b = document.createElement("button");
    b.dataset.view = v.id;
    b.title = v.label;
    b.setAttribute("aria-label", v.label);
    b.innerHTML = `<span class="icon" aria-hidden="true">${v.icon}</span><span class="label">${v.label}</span>`;
    b.addEventListener("click", () => setView(v.id));
    nav.appendChild(b);
    const t = b.cloneNode(true);
    t.addEventListener("click", () => setView(v.id));
    tabbar.appendChild(t);
  }
  // small-screen panel toggle inside stage header area: reuse nav; the panel close button:
  document.getElementById("panel-close").addEventListener("click", () => document.body.classList.remove("panel-open"));
  document.getElementById("panel-scrim").addEventListener("click", () => document.body.classList.remove("panel-open"));
}

// ---------------------------------------------------------------- now-playing mini card
export function setNowPlaying({ title, artist, art, isStream, time, duration, status }) {
  const elT = document.getElementById("np-title");
  const elA = document.getElementById("np-artist");
  const elTime = document.getElementById("np-time");
  const elLive = document.getElementById("np-live");
  const elArt = document.getElementById("np-art");
  if (title !== undefined) elT.textContent = title || "Nothing playing";
  if (artist !== undefined) elA.textContent = artist || "";
  if (time !== undefined) elTime.textContent = time;
  elLive.hidden = !isStream;
  if (art !== undefined) {
    if (art) { elArt.onerror = () => { elArt.hidden = true; }; elArt.src = art; elArt.hidden = false; }
    else { elArt.removeAttribute("src"); elArt.hidden = true; }
  }
  document.title = title && title !== "Nothing playing" ? `${title} — REVamp` : "REVamp";
}

// ---------------------------------------------------------------- connection pill
function setConn(ok, text) {
  const el = document.getElementById("conn-pill");
  el.classList.toggle("on", !!ok);
  el.textContent = text || (ok ? "connected" : "offline");
}

// ---------------------------------------------------------------- ticker
export function pushTicker(entries) {
  const track = document.getElementById("ticker-track");
  if (!entries?.length) return;
  const frag = document.createDocumentFragment();
  for (const ev of entries.slice(-20)) {
    const s = document.createElement("span");
    s.className = "t-item";
    const icon = ev.kind === "tool_end" ? (ev.ok ? "✓" : "✗") : "⚙";
    const okCls = ev.kind === "tool_end" ? (ev.ok ? "t-ok" : "t-err") : "t-tool";
    const preview = (ev.preview || ev.tool || "").slice(0, 60);
    s.innerHTML = `<span class="${okCls}"></span>`;
    s.querySelector("span").textContent = icon;
    const label = document.createElement("span");
    label.textContent = `${ev.tool || ev.kind}${preview ? ": " + preview : ""}`;
    s.appendChild(label);
    frag.appendChild(s);
  }
  track.querySelector(".muted")?.remove();
  track.appendChild(frag);
  while (track.children.length > 30) track.firstChild.remove();
}

export function setApprovalWaiting(on, reason = "") {
  document.body.classList.toggle("approval-wait", !!on);
  if (on) document.getElementById("approval-banner").textContent = `⏸ ${reason || "Waiting for your approval…"}`;
}
export function setAgentBusy(on) { document.body.classList.toggle("agent-busy", !!on); }

// ---------------------------------------------------------------- SSE + fallback polling
function connectEvents(onCommand, onAgent, onLibrary, onState, onStatus) {
  let es = null;
  let backoff = 1000;
  let pollTimer = null;
  let closed = false;

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const events = await apiGet("/api/agent_events");
        if (Array.isArray(events)) onAgent(events.slice(-20), true);
      } catch { /* server down; keep trying */ }
    }, 5000);
  };
  const stopPolling = () => { clearInterval(pollTimer); pollTimer = null; };

  const connect = () => {
    if (closed) return;
    try { es?.close(); } catch { /* ignore */ }
    es = new EventSource(authedUrl("/api/events"));
    es.onopen = () => { backoff = 1000; setConn(true); stopPolling(); onStatus?.(true); };
    es.onerror = () => {
      setConn(false, "reconnecting…");
      onStatus?.(false);
      startPolling();
      es.close();
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    const handler = (fn) => (e) => { try { fn(JSON.parse(e.data)); } catch { /* bad json */ } };
    es.addEventListener("command", handler(onCommand));
    es.addEventListener("agent", handler((ev) => onAgent([ev], false)));
    es.addEventListener("library", handler(onLibrary));
    es.addEventListener("state", handler(onState));
    // Agent/API playlist writes arrive here too, so an already-open Playlists view reloads.
    es.addEventListener("playlists", handler(() => window.dispatchEvent(new Event("revamp:playlists-changed"))));
    // Likes made anywhere (e.g. the Hermes mini player): player.js keeps the hearts in step.
    es.addEventListener("likes", handler((d) => window.dispatchEvent(new CustomEvent("revamp:likes-sse", { detail: d }))));
    // A backup restore (or import) rewrote the data dir: start fresh so every view re-reads it.
    es.addEventListener("restored", handler(() => { if (!window.revampLocalRestore) location.reload(); }));
  };
  connect();
  return () => { closed = true; es?.close(); stopPolling(); };
}

// ---------------------------------------------------------------- keyboard: / focuses view search
function globalKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) {
      e.preventDefault();
      const inp = document.querySelector('#panel-body .searchbox input, #panel-body input[type="search"]');
      inp?.focus();
    }
  });
}

// ---------------------------------------------------------------- host bridge
// When Hermes Desktop hosts this page it keeps ONE live player and moves it
// between views. It talks to us with postMessage:
//   parent → page  {type: "revamp:mode", mode: "full" | "winamp"}
//   page → parent  {type: "revamp:bbox", x, y, w, h}  (winamp mode: where the classic windows are)
//   page → parent  {type: "revamp:ready"}
// "winamp" mode hides the modern shell so only the classic windows remain.
const pageQuery = new URLSearchParams(location.search);
const hosted = window.parent !== window;
// The OS pop-out (?float=1) is a top-level app window, not Hermes's iframe.
const desktopFloat = pageQuery.has("float");
let mode = pageQuery.get("mode") === "winamp" || desktopFloat ? "winamp" : "full";
let lastBox = "";

function applyLayoutClasses() {
  const forcedEmbed = pageQuery.has("embed");
  document.body.classList.toggle("embed", mode === "full" && (forcedEmbed || innerWidth < 900));
  document.body.classList.toggle("mobile", innerWidth < 520);
  document.body.classList.toggle("mode-winamp", mode === "winamp");
  document.body.classList.toggle("float-desktop", desktopFloat);
  fitClassicStack();
}

// A docked Hermes pane can be narrower than the 275px classic stack. Scale it down
// to the stage instead of letting it overflow (and crop) on both sides.
function fitClassicStack() {
  const stage = document.querySelector(".stage");
  if (!stage || !document.body.classList.contains("embed")) {
    document.documentElement.style.removeProperty("--revamp-fit");
    return;
  }
  const cs = getComputedStyle(stage);
  const room = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const fit = room > 0 ? Math.min(1, room / 275) : 1;
  document.documentElement.style.setProperty("--revamp-fit", fit.toFixed(3));
}

function setMode(next, placement) {
  mode = next === "winamp" ? "winamp" : "full";
  lastBox = "";
  applyLayoutClasses();
  player?.setVisualizerPlacement?.(placement);
  if (placement) setHostPlayerPlace(labelHostPlace(placement));
  reportBox();
}

function reportBox() {
  if (!hosted || mode !== "winamp") return;
  const wins = [...document.querySelectorAll("#webamp-host [id$='-window'], #webamp-host .gen-window")].filter((w) => w.offsetParent !== null);
  if (!wins.length) return;
  const r = wins.map((w) => w.getBoundingClientRect());
  // The player windows alone (no transient menus): the host sizes its scale from this.
  const bx = Math.min(...r.map((b) => b.left)), by = Math.min(...r.map((b) => b.top));
  const base = { bw: Math.ceil(Math.max(...r.map((b) => b.right)) - bx), bh: Math.ceil(Math.max(...r.map((b) => b.bottom)) - by) };
  // An open context menu (EQ presets) must not be clipped by the overlay: count it
  // and its visible submenu in the frame Hermes sizes the pop-out to.
  for (const m of document.querySelectorAll("#webamp-context-menu ul")) {
    const b = m.getBoundingClientRect();
    if (b.width > 1 && b.height > 1 && getComputedStyle(m).visibility !== "hidden") r.push(b);
  }
  const x = Math.min(...r.map((b) => b.left)), y = Math.min(...r.map((b) => b.top));
  const box = { x: Math.floor(x), y: Math.floor(y), ...base,
    w: Math.ceil(Math.max(...r.map((b) => b.right)) - x), h: Math.ceil(Math.max(...r.map((b) => b.bottom)) - y) };
  const key = JSON.stringify(box);
  if (key === lastBox) return;
  lastBox = key;
  window.parent.postMessage({ type: "revamp:bbox", ...box }, "*");
}

// Popped out, the classic title bars move the whole floating window instead of the
// windows inside it (which showed blank space, then snapped back). Webamp starts its own
// drag on mousedown/touchstart over an element whose OWN class is "draggable"; we take
// exactly those presses first and stream screen coordinates to Hermes, which moves the
// overlay. Buttons inside the bars and double-click (window shade) are unaffected.
function hostDragsThis(e) {
  return hosted && mode === "winamp" && e.target instanceof Element && e.target.classList.contains("draggable");
}
document.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || !hostDragsThis(e)) return;
  e.preventDefault();
  e.stopPropagation();
  const bar = e.target;
  const id = e.pointerId;
  const post = (phase, ev) => window.parent.postMessage({ type: "revamp:drag", phase, x: ev.screenX, y: ev.screenY }, "*");
  // One message per frame, with the latest position: a message per pointer event flooded Hermes.
  let last = null, frame = 0;
  const flush = () => { frame = 0; if (last) { post("move", last); last = null; } };
  const move = (ev) => {
    if (ev.pointerId !== id) return;
    last = { screenX: ev.screenX, screenY: ev.screenY };
    if (!frame) frame = requestAnimationFrame(flush);
  };
  const end = (ev) => {
    if (ev.pointerId !== id) return;
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    last = null;
    post("end", ev);
    bar.removeEventListener("pointermove", move);
    bar.removeEventListener("pointerup", end);
    bar.removeEventListener("pointercancel", end);
  };
  try { bar.setPointerCapture(id); } catch { /* element gone */ }
  bar.addEventListener("pointermove", move);
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
  post("start", e);
}, true);
for (const type of ["mousedown", "touchstart"]) {
  document.addEventListener(type, (e) => { if (hostDragsThis(e)) e.stopPropagation(); }, true);
}

// In its own desktop window the classic windows stay put. Webamp starts a drag
// on mousedown/touchstart/pointerdown over an element whose own class is
// "draggable"; stopping those presses keeps the title-bar buttons and
// double-click shade working, and leaves the player fixed in the window.
function floatLocksDrag(e) {
  return desktopFloat && e.target instanceof Element && e.target.classList.contains("draggable");
}
for (const type of ["mousedown", "touchstart", "pointerdown"]) {
  // Window capture, registered before Webamp exists, so stopImmediatePropagation
  // beats a later listener on window. stopPropagation keeps the press off the
  // title-bar element, which is where Webamp starts its own drag.
  window.addEventListener(type, (e) => {
    if (!floatLocksDrag(e)) return;
    e.stopImmediatePropagation();
    e.stopPropagation();
  }, true);
}

function playerRects() {
  const nodes = document.querySelectorAll("#webamp-host #main-window, #webamp-host #equalizer-window, #webamp-host #playlist-window, #webamp-host #milkdrop-window, #webamp-host .gen-window");
  const rects = [];
  for (const n of nodes) {
    if (!(n instanceof Element)) continue;
    const style = getComputedStyle(n);
    if (style.display === "none" || style.visibility === "hidden") continue;
    const r = n.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    rects.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height });
  }
  return rects;
}

function stackDrifted() {
  const main = document.getElementById("main-window")?.getBoundingClientRect();
  const eq = document.getElementById("equalizer-window")?.getBoundingClientRect();
  const pl = document.getElementById("playlist-window")?.getBoundingClientRect();
  if (!main || !eq || !pl || main.height < 2 || eq.height < 2 || pl.height < 2) return true;
  if (Math.abs(main.left) > 1 || Math.abs(main.top) > 1) return true;
  if (Math.abs(eq.left) > 1 || Math.abs(eq.top - main.bottom) > 2) return true;
  if (Math.abs(pl.left) > 1 || Math.abs(pl.top - eq.bottom) > 2) return true;
  if (!player?.visualizerOpen?.()) return false;
  const md = document.querySelector("#milkdrop-window, #webamp-host .gen-window")?.getBoundingClientRect();
  if (!md || md.height < 20) return true;
  const stackH = pl.bottom - main.top;
  return Math.abs(md.left - main.right) > 2 || Math.abs(md.top - main.top) > 2 || Math.abs(md.height - stackH) > 2;
}

let fitFlight = null;
let driftPasses = 0;
let fitting = false;
let lastResizeKey = "";
let lastPayload = "";
function fitDesktopWindow() {
  if (!desktopFloat || !player?.layoutDesktop || fitting) return Promise.resolve(false);
  fitting = true;
  try {
  if (!player?.layoutDesktop) return Promise.resolve(false);
  // Pin a few times so Webamp's centering pass can't leave the stack offset,
  // then size to whatever is actually on screen. A border that never lands
  // inside 2px must not block the resize forever.
  if (stackDrifted() && driftPasses < 8) {
    driftPasses += 1;
    player.layoutDesktop();
    return Promise.resolve(false);
  }
  driftPasses = 0;
  const box = contentSize(playerRects());
  window.__revampFit = box;
  if (!box || box.w < 40 || box.h < 40 || box.w > 2400 || box.h > 1600) return Promise.resolve(false);
  if (Math.abs(window.innerWidth - box.w) <= 2 && Math.abs(window.innerHeight - box.h) <= 2) return Promise.resolve(true);
  const outer = outerWindowSize(box.w, box.h, window.innerWidth, window.innerHeight, window.outerWidth, window.outerHeight);
  const payload = {
    width: box.w, height: box.h,
    innerWidth: window.innerWidth, innerHeight: window.innerHeight,
    outerWidth: window.outerWidth, outerHeight: window.outerHeight,
  };
  window.__revampFit = { ...box, outer };
  const payloadKey = JSON.stringify(payload);
  if (payloadKey === lastPayload) return Promise.resolve(true);
  lastPayload = payloadKey;
  const resizeKey = `${outer.width}x${outer.height}`;
  if (resizeKey !== lastResizeKey) {
    lastResizeKey = resizeKey;
    try { window.resizeTo(outer.width, outer.height); } catch { /* some app windows ignore scripted resize */ }
  }
  fitFlight = apiPost("/api/window/size", payload).then((out) => {
    window.__revampFit = { ...box, outer, result: out };
    if (out?.reason === "debugger") lastPayload = ""; // the port may not be up yet: try again
    return true;
  }).catch(() => { lastPayload = ""; return false; });
  return fitFlight;
  } finally {
    fitting = false;
  }
}

function kickDesktopFit() {
  if (!desktopFloat) return;
  fitDesktopWindow();
  requestAnimationFrame(fitDesktopWindow);
  setTimeout(fitDesktopWindow, 80);
  setTimeout(fitDesktopWindow, 320);
  setTimeout(fitDesktopWindow, 700);
}

// Webamp positions its context menus from the click point. The EQ's presets
// button sits at x=217 inside the 275px EQ window, so that menu (and the preset
// submenu it opens to the right) ran past the stack edge and the winamp stage
// clipped it. Pull any menu that overflows back inside and flip its submenus to
// the left, where the freed space is. Menus that already fit are left alone.
const FLIP_CLASS = "revamp-flip";

/** Size of a menu's submenu, measured without showing it. Webamp hides
 *  submenus until hover, and a hidden element reports 0. */
function submenuBox(menu) {
  const ul = menu.querySelector("ul");
  if (!ul) return { w: 0, h: 0 };
  const prev = ul.getAttribute("style") || "";
  ul.style.visibility = "hidden";
  ul.style.display = "block";
  const r = ul.getBoundingClientRect();
  const size = { w: Math.round(r.width || 0), h: Math.round(r.height || 0) };
  if (prev) ul.setAttribute("style", prev);
  else ul.removeAttribute("style");
  return size;
}

function fitContextMenus() {
  const host = document.getElementById("webamp-context-menu");
  const stack = document.getElementById("webamp-host");
  if (!host || !stack) return;
  // Fit to the classic windows themselves: the docked Hermes pane and the pop-out clip the
  // iframe to the stack (275 wide), and the iframe can be wider than what is visible.
  const wins = [...stack.querySelectorAll("#main-window, #equalizer-window, #playlist-window")]
    .map((w) => w.getBoundingClientRect()).filter((b) => b.width > 1 && b.height > 1);
  const frame = stack.getBoundingClientRect();
  const box = wins.length
    ? { left: Math.min(...wins.map((b) => b.left)), right: Math.max(...wins.map((b) => b.right)),
        top: Math.min(...wins.map((b) => b.top)), bottom: Math.max(...wins.map((b) => b.bottom)) }
    : frame;
  box.width = box.right - box.left;
  if (box.width < 2) return;
  for (const menu of host.querySelectorAll(".context-menu")) {
    const style = getComputedStyle(menu);
    if (style.display === "none") continue;
    const r = menu.getBoundingClientRect();
    if (r.width < 2) continue;
    // Judge the menu where Webamp put it, not where we last moved it, so a
    // repeat pass reaches the same answer instead of chasing itself.
    const applied = parseFloat(style.marginLeft) || 0;
    const naturalRight = r.right - applied;
    const over = Math.round(naturalRight - box.right);
    const want = over > 0 ? -(over + 2) : 0;
    if (Math.abs(applied - want) > 0.5) menu.style.marginLeft = want ? `${want}px` : "";
    const sub = submenuBox(menu);
    // A submenu opens 184px wide: flip it left whenever opening right would
    // leave the stage, even when the menu row itself still fits.
    const fitsRight = sub.w === 0 || naturalRight + sub.w <= box.right;
    menu.classList.toggle(FLIP_CLASS, !fitsRight);
    // In a 275px stack the presets menu sits at x~137: the 184px list fits neither
    // right (25px free) nor left (137px free). Then align it with the stack's left
    // edge and cap its width to the stack, overlapping the menu like a drawer.
    const menuLeft = r.left - applied + want;
    const fitsLeft = sub.w === 0 || menuLeft - sub.w >= box.left;
    const overlay = !fitsRight && !fitsLeft;
    menu.classList.toggle("revamp-overlay", overlay);
    if (overlay) {
      menu.style.setProperty("--revamp-sub-left", `${Math.round(box.left - menuLeft + 2)}px`);
      menu.style.setProperty("--revamp-sub-w", `${Math.max(120, Math.floor(box.width - 4))}px`);
    } else {
      menu.style.removeProperty("--revamp-sub-left");
      menu.style.removeProperty("--revamp-sub-w");
    }
    // The preset list is taller than the stack: cap it and let it scroll rather
    // than running off the bottom edge of the stage.
    const ul = menu.querySelector("ul");
    if (ul && sub.h > 0) {
      const room = Math.round(box.bottom - r.top - 16);
      if (sub.h > room) {
        ul.style.maxHeight = `${Math.max(96, room)}px`;
        ul.style.overflowY = "auto";
      } else if (ul.style.maxHeight) {
        ul.style.maxHeight = "";
        ul.style.overflowY = "";
      }
    }
  }
}

function watchContextMenus() {
  // Menus open and close without a window moving: refresh the reported frame too.
  document.addEventListener("mouseover", () => requestAnimationFrame(reportBox), true);
  const attach = () => {
    const host = document.getElementById("webamp-context-menu");
    if (!host) return false;
    if (host.dataset.revampFitted) return true;
    host.dataset.revampFitted = "1";
    // childList only: our own marginLeft writes must not re-trigger this.
    new MutationObserver(() => {
      fitContextMenus();
      requestAnimationFrame(fitContextMenus);
    }).observe(host, { childList: true, subtree: true });
    fitContextMenus();
    return true;
  };
  if (attach()) return;
  // The menu host is Webamp's own portal, so watch the document for it — the
  // first menu-bearing insert attaches us, then this observer goes away.
  const boot = new MutationObserver(() => {
    if (attach()) boot.disconnect();
  });
  boot.observe(document.body, { childList: true, subtree: true });
}

watchContextMenus();

function mountDesktopFloat() {
  if (!desktopFloat) return;
  window.addEventListener("revamp:desktop-layout", kickDesktopFit);
  kickDesktopFit();
  // Playlist resize and a late Webamp centering pass both change the stack.
  const fitIfShown = () => { if (!document.hidden) fitDesktopWindow(); };
  setInterval(fitIfShown, 1000);
  document.addEventListener("visibilitychange", fitIfShown);
}

function installMilkdropMenuKeyboardAccess() {
  const option = document.querySelector("#main-window #option");
  if (option && !option.dataset.revampKeyboard) {
    option.dataset.revampKeyboard = "true";
    option.tabIndex = 0;
    option.setAttribute("role", "button");
    option.setAttribute("aria-label", "Winamp menu");
    option.title = "Winamp Menu (MilkDrop: Ctrl+Shift+M)";
    option.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault(); option.click();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || !e.shiftKey || e.key.toLowerCase() !== "m") return;
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) return;
    e.preventDefault(); player?.toggleVisualizer?.();
  });
}

// Waveform feed for the host's status-bar meter: {type: "revamp:wave", on} in,
// {type: "revamp:wave", samples} out at 12 fps while playing. Same trace as the
// bundled Radio's meter: a rising-edge-triggered ~4 ms window, fixed gain.
let waveTimer = null;
let waveBuf = null;
let waveSilent = false;
function waveFrame() {
  const analyser = (() => { try { return player?.webamp?.media?.getAnalyser?.(); } catch { return null; } })();
  const status = (() => { try { return player.webamp.store.getState().media.status; } catch { return ""; } })();
  if (!analyser || status !== "PLAYING") {
    if (!waveSilent) window.parent.postMessage({ type: "revamp:wave", samples: null }, "*");
    waveSilent = true;
    return;
  }
  waveSilent = false;
  if (!waveBuf || waveBuf.length !== analyser.fftSize) waveBuf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(waveBuf);
  const span = Math.floor((analyser.context?.sampleRate || 48000) * 0.004);
  let start = 0;
  for (let i = 1; i < waveBuf.length - span; i++) if (waveBuf[i - 1] < 0 && waveBuf[i] >= 0) { start = i; break; }
  const cols = 72;
  const samples = Array.from({ length: cols }, (_, x) => {
    const from = start + Math.floor((x * span) / cols), to = start + Math.floor(((x + 1) * span) / cols);
    let sum = 0;
    for (let i = from; i < to; i++) sum += waveBuf[i];
    return Math.max(-1, Math.min(1, (sum / Math.max(1, to - from)) * 2));
  });
  window.parent.postMessage({ type: "revamp:wave", samples }, "*");
}

// ---------------------------------------------------------------- updates
// After an install the page files change and /api/health reports a new "build". Reload so
// the new code is what runs: at once when nothing is playing, otherwise when it stops.
const LOADED_BUILD = document.querySelector('meta[name="revamp-build"]')?.content || "";
let updateNoticed = false;
async function checkForUpdate() {
  if (!LOADED_BUILD) return;
  let build = "";
  try { build = (await (await fetch("/api/health", { cache: "no-store" })).json()).build || ""; } catch { return; }
  if (!build || build === LOADED_BUILD) return;
  if ((window.revampPlayerApi?.nowStatus?.() || "STOPPED") === "STOPPED") return location.reload();
  if (!updateNoticed) {
    updateNoticed = true;
    toast("REVamp was updated. It refreshes itself when playback stops.", { kind: "info", ttl: 10000 });
  }
}
function runWhileVisible(ms, fn) {
  const tick = () => { if (!document.hidden) fn(); };
  const timer = setInterval(tick, ms);
  document.addEventListener("visibilitychange", tick);
  return timer;
}
runWhileVisible(20000, checkForUpdate);

window.addEventListener("message", (e) => {
  if (e.source !== window.parent || !e.data || typeof e.data !== "object") return;
  if (e.data.type === "revamp:reload") return location.reload(); // Hermes: "REVamp: Reload player"
  if (e.data.type === "revamp:mode") setMode(e.data.mode, e.data.placement);
  if (e.data.type === "revamp:visualizer") player?.toggleVisualizer?.(); // Hermes: show/hide MilkDrop
  // The player_location command lands here (SSE → handleCommand in player.js): tell Hermes
  // where the player should live, so its plugin moves the same iframe.
  if (e.data.type === "revamp:location" && hosted) {
    window.parent.postMessage({ type: "revamp:location", location: e.data.location }, "*");
  }
  if (e.data.type === "revamp:wave") {
    clearInterval(waveTimer);
    waveTimer = e.data.on ? setInterval(() => { if (!document.hidden) waveFrame(); }, 1000 / 12) : null;
  }
});
window.addEventListener("resize", () => {
  applyLayoutClasses();
  reportBox();
  if (desktopFloat) kickDesktopFit();
});
// Windows open/close/shade/move inside Webamp; a cheap poll keeps the frame fitted.
runWhileVisible(400, reportBox);

// Control mode has no local player: views (Music, Books, Radio, Skins, likes) still call
// window.revampPlayerApi, so give them a stand-in that drives the one live player through
// server commands. Without it, Play in the library silently did nothing.
function installControlPlayerApi() {
  if (window.revampPlayerApi) return;
  const cmd = (body) => apiPost("/api/command", body);
  window.revampPlayerApi = {
    control: true,
    loadTracks: (items, o = {}) => cmd({ action: "load", items: items || [], mode: o.mode || "replace", play: o.play !== false }),
    playIndex: (index) => cmd({ action: "play_index", index: Math.max(0, index | 0) }),
    async playItemsAt(index, list) {
      await cmd({ action: "load", items: list || [], mode: "replace", play: false });
      return cmd({ action: "play_index", index: Math.max(0, index | 0) });
    },
    play: () => cmd({ action: "play" }),
    pause: () => cmd({ action: "pause" }),
    toggle: () => cmd({ action: "toggle" }),
    stop: () => cmd({ action: "stop" }),
    next: () => cmd({ action: "next" }),
    prev: () => cmd({ action: "prev" }),
    setSkin: (id) => cmd({ action: "skin", id }),
    setLiked: (trackId, liked) => trackId ? apiPost("/api/likes", { track_id: trackId, liked: !!liked }) : Promise.resolve(),
    nowStatus: () => "STOPPED",
  };
}

// ---------------------------------------------------------------- boot
const CONTROL = new URLSearchParams(location.search).has("control");
function mountControlStage() {
  const stage = document.getElementById("stage");
  if (!stage) return;
  const copy = { docked: "Playing in the frame", floating: "Playing in the pop-out", desktop: "Playing on the desktop" };
  const render = (place = "docked") => {
    stage.replaceChildren();
    const box = document.createElement("div");
    box.style.cssText = "display:flex;flex-direction:row;flex-wrap:nowrap;align-items:center;justify-content:space-between;gap:8px;min-width:0;max-width:100%;box-sizing:border-box;padding:6px 10px;border:1px solid var(--line,#303746);border-radius:10px;background:rgba(255,255,255,0.03);color:var(--txt,#dbe2f0)";
    const label = document.createElement("div");
    label.style.cssText = "display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;min-width:0;flex:1 1 auto";
    const dot = document.createElement("span");
    dot.style.cssText = "width:8px;height:8px;border-radius:50%;background:var(--accent,#4cc2ff);box-shadow:0 0 0 3px rgba(76,194,255,0.2)";
    dot.style.flex = "0 0 auto";
    const text = document.createElement("span");
    text.style.cssText = "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    text.textContent = copy[place] || copy.docked;
    label.append(dot, text);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn sm";
    button.style.cssText = "flex:0 0 auto;white-space:nowrap;font-size:11.5px;padding:3px 9px";
    button.textContent = "Bring the player here";
    button.addEventListener("click", () => window.parent.postMessage({ type: "revamp:bring-player-here" }, "*"));
    box.append(label, button);
    stage.append(box, queue);
  };
  // Webamp's playlist window belongs to the real iframe, so control mode keeps
  // a small live queue here instead. Its edits are ordinary server commands.
  const queue = document.createElement("div");
  queue.style.cssText = "min-width:0;max-height:260px;overflow:auto;padding:8px;border:1px solid var(--line,#303746);border-radius:10px;background:var(--panel,#151a27);font-size:12px";
  const paintQueue = (list) => {
    queue.replaceChildren();
    const title = document.createElement("strong"); title.textContent = `Queue · ${list.length}`; queue.append(title);
    list.slice(0, 12).forEach((item, index) => {
      const row = document.createElement("div"); row.style.cssText = "display:flex;gap:6px;align-items:center;margin-top:5px";
      const play = document.createElement("button"); play.className = "btn btn-ghost"; play.textContent = item.title || "Untitled"; play.style.cssText = "flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
      play.addEventListener("click", () => apiPost("/api/command", { action: "play_index", index }));
      const remove = document.createElement("button"); remove.className = "btn btn-ghost"; remove.textContent = "×";
      remove.addEventListener("click", async () => { await apiPost("/api/command", { action: "queue_remove", index }); refreshQueue(); });
      row.append(play, remove); queue.append(row);
    });
  };
  const refreshQueue = async () => {
    try {
      const state = await apiGet("/api/state"), list = Array.isArray(state.playlist) ? state.playlist : [];
      paintQueue(list);
    } catch { /* the connection pill already carries the offline state */ }
  };
  // The live player already publishes queue changes on the event stream. Poll
  // /api/state only while that stream is down, and not while the page is hidden.
  let queuePoll = null;
  const armQueuePoll = (streamUp) => {
    clearInterval(queuePoll);
    queuePoll = null;
    if (streamUp) return;
    queuePoll = setInterval(() => { if (!document.hidden) refreshQueue(); }, 15000);
  };
  connectEvents(() => {}, () => {}, () => {}, (state) => {
    if (Array.isArray(state?.playlist)) paintQueue(state.playlist);
  }, armQueuePoll);
  stage.style.cssText += ";position:relative;display:flex;flex-direction:column;align-items:stretch;gap:8px;padding:8px";
  stage.append(queue); refreshQueue();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshQueue(); });
  window.addEventListener("message", (e) => {
    if (e.source === window.parent && e.data?.type === "revamp:control-place") {
      setHostPlayerPlace(labelHostPlace(e.data.place));
      render(e.data.place);
    }
  });
  render();
}
async function boot() {
  mountThemeButton(document.querySelector(".panel-head"), document.getElementById("conn-pill"));
  // Control mode is a complete library shell, but never imports Webamp or initializes
  // a playback backend. Its API actions operate on the one player owned by Hermes.
  if (CONTROL) {
    installControlPlayerApi();
    applyLayoutClasses();
    buildNav();
    globalKeys();
    mountControlStage();
    try {
      const h = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json());
      setConn(true, `v${h.version || "?"}`);
    } catch { setConn(false, "server down"); }
    setView("library");
    if (hosted) window.parent.postMessage({ type: "revamp:ready", bridge: Boolean(BRIDGE_KEY), control: true }, "*");
    return;
  }
  applyLayoutClasses();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) document.body.classList.add("reduce-motion");

  buildNav();
  globalKeys();

  // health check (no token needed)
  try {
    const h = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json());
    setConn(true, `v${h.version || "?"}`);
  } catch {
    setConn(false, "server down");
    toast("Cannot reach the REVamp server. Is the plugin running?", { kind: "error", ttl: 8000 });
  }

  player = await initPlayer({
    apiGet, apiPost, apiPut, apiDelete, authedUrl, token: TOKEN,
    setNowPlaying, setConn, pushTicker, setApprovalWaiting, setAgentBusy,
    connectEvents, toast, setView,
  });
  mountDesktopFloat();
  installMilkdropMenuKeyboardAccess();
  setView("library");
  // Empty start with backups on disk? Offer to restore before the user rebuilds anything.
  maybeOfferRestore(viewApi).catch((e) => console.warn("restore offer", e));
  // Book controls and lyrics on the now-playing card (optional: the page works without them).
  import("./views/npextras.js").then((m) => m.mount(player, viewApi)).catch((e) => console.warn("npextras", e));
  if (hosted) window.parent.postMessage({ type: "revamp:ready", bridge: Boolean(BRIDGE_KEY) }, "*");
}

boot().catch((e) => {
  console.error("revamp boot failed", e);
  document.getElementById("panel-body").innerHTML =
    `<div class="empty"><span class="big">⚠</span>Boot failed: ${String(e.message || e)}</div>`;
});

// ---------------------------------------------------------------- REST bridge
// The Hermes mini player normally reaches this server through the Hermes gateway, whose
// connection can go stale while this page keeps playing. So it may ask us instead: we
// make the call with our own token and post the answer back. Only our direct parent may
// ask, only from a local origin (Hermes loads from a file; a website embedding this page
// is refused), only /api paths, JSON in and out.
const BRIDGE_PATH = /^\/[A-Za-z0-9_\-./]*(\?[A-Za-z0-9_\-.~%=&+:@,*!']*)?$/;
const BRIDGE_METHODS = new Set(["GET", "POST", "PUT", "DELETE"]);
// Only the Hermes plugin knows this (it gets it from the server through Hermes); a page that
// merely embeds this one cannot. Without it the bridge stays off.
const sameKey = (a) => {
  if (typeof a !== "string" || !BRIDGE_KEY || a.length !== BRIDGE_KEY.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ BRIDGE_KEY.charCodeAt(i);
  return diff === 0;
};
const localOrigin = (o) => o === "null" || o === "file://" || /^(file|app|hermes|electron):/.test(o)
  || /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(o);
if (hosted) {
  window.addEventListener("message", async (e) => {
    const d = e.data;
    if (e.source !== window.parent || !d || d.type !== "revamp:rest" || typeof d.id !== "string") return;
    const reply = (r) => window.parent.postMessage({ type: "revamp:rest-result", id: d.id, ...r }, e.origin === "null" ? "*" : e.origin);
    if (!localOrigin(e.origin) || !sameKey(d.key)) { console.warn("revamp bridge: refused", e.origin); return; }
    const path = String(d.path || ""), method = String(d.method || "GET").toUpperCase();
    if (!BRIDGE_PATH.test(path) || path.includes("..") || /^\/info\b/.test(path) || !BRIDGE_METHODS.has(method)) {
      return reply({ ok: false, status: 400, error: "not allowed" });
    }
    const body = d.body === undefined || method === "GET" ? undefined : JSON.stringify(d.body);
    if (body && body.length > 2_000_000) return reply({ ok: false, status: 413, error: "too large" });
    try {
      const headers = { "X-Revamp-Token": TOKEN };
      if (body) headers["Content-Type"] = "application/json";
      const res = await fetch(new URL("/api" + path, location.origin), { method, headers, body });
      let data = null;
      try { data = await res.json(); } catch { /* not json */ }
      reply({ ok: res.ok, status: res.status, body: data, error: res.ok ? undefined : (data?.error || `HTTP ${res.status}`) });
    } catch (err) {
      reply({ ok: false, status: 0, error: String(err?.message || err) }); // this server is down too
    }
  });
}
