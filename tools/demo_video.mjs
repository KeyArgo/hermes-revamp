#!/usr/bin/env node
/**
 * Record the launch demo from the real REVamp server/UI (v2: hi-DPI capture + demo_edit).
 */
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { renderDemoEdit } from "./demo_edit.mjs";

const root = resolve(new URL("..", import.meta.url).pathname);
const OUT = process.env.DEMO_V2_OUT || new URL("../out/demo", import.meta.url).pathname;
const CAPTURE_W = 1440;
const CAPTURE_H = 900;
const DEVICE_SCALE = 2;
const fixture = join(root, "lanes/_e2e/lib");
const browser = process.env.CHROME_BIN || "google-chrome";
const FPS = 30, DURATION = 14.5;
let server;
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const port = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, timeout = 20000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await fn()) return; await sleep(100); } throw Error(`Timed out: ${what}`); }
async function json(url, opts = {}) { const r = await fetch(url, opts); if (!r.ok) throw Error(`${r.status} ${url}`); return r.json(); }
const headers = (token) => ({ "Content-Type": "application/json", "X-Revamp-Token": token });
const post = (p, token, path, body) => json(`http://127.0.0.1:${p}${path}`, { method: "POST", headers: headers(token), body: JSON.stringify(body) });
const put = (p, token, path, body) => json(`http://127.0.0.1:${p}${path}`, { method: "PUT", headers: headers(token), body: JSON.stringify(body) });
const state = (p, token, body) => json(`http://127.0.0.1:${p}/api/state`, { method: "PUT", headers: headers(token), body: JSON.stringify(body) });
const command = (p, token, body) => post(p, token, "/api/command", body);

const DEMO_CAPTURE_CSS = `
html, body, #revamp-demo-capture { overflow: hidden !important; scrollbar-width: none !important; }
#revamp-demo-capture .stage,
#revamp-demo-capture .shell,
#revamp-demo-capture .panel,
#revamp-demo-capture .panel-body,
#revamp-demo-capture .ticker { overflow: hidden !important; scrollbar-width: none !important; }
#revamp-demo-capture *::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; background: transparent !important; }
`;

async function injectDemoChrome(page) {
  await page.cdp("Runtime.evaluate", {
    expression: `(function() {
      document.documentElement.id = 'revamp-demo-capture';
      const style = document.createElement('style');
      style.id = 'revamp-demo-capture-style';
      style.textContent = ${JSON.stringify(DEMO_CAPTURE_CSS)};
      document.head.appendChild(style);
    })()`,
  });
}

async function centerPlayerStack(page) {
  await evaluate(page, `(() => {
    const host = document.getElementById('webamp-host');
    const stage = document.getElementById('stage');
    const api = window.revampPlayerApi;
    if (!host || !stage || !api?.webamp?.store) return false;
    const wins = [...host.querySelectorAll('[id$="-window"], .gen-window')].filter((w) => w.offsetParent);
    if (!wins.length) return false;
    const boxes = wins.map((w) => w.getBoundingClientRect());
    const minX = Math.min(...boxes.map((b) => b.left));
    const minY = Math.min(...boxes.map((b) => b.top));
    const maxX = Math.max(...boxes.map((b) => b.right));
    const maxY = Math.max(...boxes.map((b) => b.bottom));
    const stackW = maxX - minX, stackH = maxY - minY;
    const sr = stage.getBoundingClientRect();
    const dx = Math.round(sr.left + (sr.width - stackW) / 2 - minX);
    const dy = Math.round(sr.top + (sr.height - stackH) / 2 - minY);
    if (!dx && !dy) return true;
    const pos = api.webamp.store.getState()?.windows?.positions || {};
    const next = {};
    for (const [id, p] of Object.entries(pos)) {
      if (p && typeof p.x === 'number' && typeof p.y === 'number') next[id] = { x: p.x + dx, y: p.y + dy };
    }
    try {
      api.webamp.store.dispatch({ type: 'UPDATE_WINDOW_POSITIONS', absolute: true, positions: next });
    } catch { return false; }
    return true;
  })()`);
  await sleep(400);
}

async function measureCropManifest(page) {
  return evaluate(page, `(() => {
    const rect = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    };
    const host = document.getElementById('webamp-host');
    const wins = host ? [...host.querySelectorAll('[id$="-window"], .gen-window')].filter((w) => w.offsetParent) : [];
    const boxes = wins.map((w) => w.getBoundingClientRect());
    const stack = boxes.length ? {
      x: Math.min(...boxes.map((b) => b.x)),
      y: Math.min(...boxes.map((b) => b.y)),
      w: Math.max(...boxes.map((b) => b.right)) - Math.min(...boxes.map((b) => b.x)),
      h: Math.max(...boxes.map((b) => b.bottom)) - Math.min(...boxes.map((b) => b.y)),
    } : null;
    const main = document.getElementById('main-window');
    const mainR = main?.getBoundingClientRect();
    const volume = mainR ? {
      x: mainR.x + mainR.width * 0.52,
      y: mainR.y + mainR.height * 0.55,
      w: mainR.width * 0.38,
      h: mainR.height * 0.28,
    } : stack;
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      stage: rect(document.getElementById('stage')),
      ticker: rect(document.getElementById('ticker')),
      stack,
      volume,
    };
  })()`);
}

async function openPage(serverPort, width, height) {
  const debug = port(), profile = mkdtempSync(join(tmpdir(), "revamp-demo-chrome-"));
  const process = spawn(browser, ["--headless=new", "--no-sandbox", "--mute-audio", "--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader", `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${debug}/json/list`); return true; } catch { return false; } }, "Chrome");
  const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find((item) => item.type === "page");
  const socket = new WebSocket(target.webSocketDebuggerUrl), pending = new Map(); let sequence = 0;
  const page = { process, profile, socket, cdp: null, frames: null };
  socket.onmessage = (event) => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Page.screencastFrame" && page.frames) {
      const index = String(page.frames.count++).padStart(6, "0");
      writeFileSync(join(page.frames.dir, `${index}.png`), Buffer.from(message.params.data, "base64"));
      page.cdp("Page.screencastFrameAck", { sessionId: message.params.sessionId }).catch(() => {});
    }
    const resolve = pending.get(message.id);
    if (resolve) { pending.delete(message.id); message.error ? resolve.reject(Error(message.error.message)) : resolve.ok(message.result); }
  };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  page.cdp = (method, params = {}) => new Promise((ok, reject) => { const id = ++sequence; pending.set(id, { ok, reject }); socket.send(JSON.stringify({ id, method, params })); });
  await page.cdp("Page.enable"); await page.cdp("Runtime.enable");
  await page.cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: DEVICE_SCALE, mobile: false });
  await page.cdp("Page.navigate", { url: `http://127.0.0.1:${serverPort}/` });
  await waitFor(async () => (await page.cdp("Runtime.evaluate", { expression: "Boolean(document.querySelector('#webamp-host')?.offsetWidth)" })).result.value, "REVamp page");
  await injectDemoChrome(page);
  return page;
}
async function evaluate(page, expression) { return (await page.cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result.value; }
async function close(page) { try { page.socket.close(); } catch {} page.process.kill("SIGTERM"); await sleep(250); rmSync(page.profile, { recursive: true, force: true }); }
async function start(page, dir) {
  mkdirSync(dir, { recursive: true });
  page.frames = { dir, count: 0 };
  await page.cdp("Page.startScreencast", {
    format: "png",
    quality: 100,
    everyNthFrame: 1,
    maxWidth: CAPTURE_W * DEVICE_SCALE,
    maxHeight: CAPTURE_H * DEVICE_SCALE,
  });
}
async function stop(page) { await page.cdp("Page.stopScreencast"); await sleep(350); const frames = page.frames; page.frames = null; return frames.count; }
async function at(started, seconds, fn) { const delay = started + seconds * 1000 - Date.now(); if (delay > 0) await sleep(delay); await fn(); }

async function pinColorfulPreset(page) {
  const name = await evaluate(page, `(async () => {
    const w = window.__webamp;
    const presets = w?.store?.getState()?.milkdrop?.presets || [];
    if (!presets.length) return "no-presets";
    const md = w.store.getState().milkdrop;
    if (md.cycling) w.store.dispatch({ type: "TOGGLE_PRESET_CYCLING" });
    if (md.randomize) w.store.dispatch({ type: "TOGGLE_RANDOMIZE_PRESETS" });
    const needles = ["flexi - mindblob [shiny mix]", "flexi - mindblob mix", "double mindblob neon"];
    let index = -1;
    for (const needle of needles) {
      index = presets.findIndex((p) => String(p.name || "").toLowerCase().includes(needle));
      if (index >= 0) break;
    }
    if (index < 0) return "no-match";
    const preset = presets[index];
    const json = preset.type === "RESOLVED" ? preset.preset : await preset.getPreset();
    w.store.dispatch({ type: "RESOLVE_PRESET_AT_INDEX", index, json });
    w.store.dispatch({ type: "SELECT_PRESET_AT_INDEX", index, transitionType: 0 });
    return preset.name;
  })()`);
  console.log(`MilkDrop preset: ${name}`);
}

async function record(serverPort, token, music, temp) {
  await state(serverPort, token, { playlist: music, current_index: 0, skin: "classic", volume: 80, settings: { milkdropOpenFloating: true, milkdropOpen: true, ticker: true, duck_on_approval: true } });
  const page = await openPage(serverPort, CAPTURE_W, CAPTURE_H);
  await waitFor(() => evaluate(page, "Boolean(window.revampPlayerApi?.loadTracks && window.revampPlayerApi?.setVisualizerPlacement)"), "player API");
  await waitFor(async () => (await json(`http://127.0.0.1:${serverPort}/api/state`, { headers: { "X-Revamp-Token": token } })).live?.player_connected, "player connection");
  await command(serverPort, token, { action: "load", items: music, mode: "replace", play: true });
  await evaluate(page, `window.revampPlayerApi.loadTracks(${JSON.stringify(music)}, { mode: "replace", play: true }); window.revampPlayerApi.setVisualizerPlacement("floating");`);
  await sleep(900); await evaluate(page, "window.revampPlayerApi.play(); window.revampPlayerApi.seek(1);");
  await waitFor(() => evaluate(page, "Boolean(document.querySelector('#milkdrop-window canvas, .gen-window canvas')?.width)"), "milkdrop canvas", 12000);
  await pinColorfulPreset(page);
  await sleep(1600);
  await centerPlayerStack(page);
  await sleep(700);
  const manifestPath = join(OUT, "crop-manifest.json");
  mkdirSync(OUT, { recursive: true });
  const classic = await measureCropManifest(page);
  writeFileSync(manifestPath, JSON.stringify(classic, null, 2));
  const frameDir = join(temp, "frames-v2"); await start(page, frameDir); const started = Date.now();
  const event = async (body, text) => { await post(serverPort, token, "/api/agent_event", body); await waitFor(() => evaluate(page, `document.querySelector('#ticker-track')?.innerText.includes(${JSON.stringify(text)})`), `ticker: ${text}`, 3500); };
  await at(started, 4.0, () => event({ kind: "session_start", source: "hermes", session_id: "demo-v2" }, "session_start"));
  await at(started, 4.15, () => event({ kind: "tool_start", source: "hermes", tool: "revamp_radio", preview: "Playing jazz radio", session_id: "demo-v2" }, "Playing jazz radio"));
  await at(started, 5.25, () => event({ kind: "tool_end", source: "hermes", tool: "revamp_radio", ok: true, session_id: "demo-v2" }, "revamp_radio"));
  await at(started, 5.40, () => event({ kind: "tool_start", source: "hermes", tool: "revamp_library", preview: "Queueing a focus mix", session_id: "demo-v2" }, "Queueing a focus mix"));
  await at(started, 7.0, () => event({ kind: "approval_request", source: "hermes", preview: "Waiting for your approval", session_id: "demo-v2" }, "approval_request"));
  await at(started, 8.8, () => event({ kind: "approval_response", source: "hermes", ok: true, session_id: "demo-v2" }, "approval_response"));
  await at(started, 10.2, async () => {
    await state(serverPort, token, { skin: "builtin:REVamp-Nous.wsz" });
    await evaluate(page, "window.revampPlayerApi.setSkin('builtin:REVamp-Nous.wsz')");
    await sleep(500);
    await centerPlayerStack(page);
  });
  await at(started, DURATION, async () => {});
  const count = await stop(page);
  const manifestAfter = await measureCropManifest(page);
  writeFileSync(manifestPath, JSON.stringify({ ...manifestAfter, classic, recorded_at: new Date().toISOString() }, null, 2));
  await close(page);
  if (count < 100) throw Error(`Only ${count} screencast frames recorded`);  // SwiftShader at 2x can land near 8–14 fps; the edit retimes to 30
  return { frameDir, count, manifestPath };
}

function editEnv() {
  process.env.DEMO_V2_OUT = OUT;
  process.env.DEMO_SRC_W = String(CAPTURE_W);
  process.env.DEMO_SRC_H = String(CAPTURE_H);
  process.env.DEMO_DSF = String(DEVICE_SCALE);
  process.env.DEMO_DURATION = String(DURATION);
}

if (process.argv.includes("--edit-only")) {
  editEnv();
  await renderDemoEdit({
    manifestPath: join(OUT, "crop-manifest.json"),
    workDir: join(OUT, "_work"),
    fromRaw: true,
  });
  console.log(`Wrote launch demo v2 edit to ${OUT}`);
} else try {
  mkdirSync(OUT, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-demo-"));
  const p = port();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", join(temp, "data"), "--library", fixture], { cwd: root, env: { ...process.env, REVAMP_HOME: join(temp, "data"), REVAMP_PORT: String(p) }, stdio: "ignore" });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  const token = readFileSync(join(temp, "data/token"), "utf8").trim();
  await put(p, token, "/api/library/folders", { folders: [{ path: fixture, kind: "music" }] });
  await waitFor(async () => !(await json(`http://127.0.0.1:${p}/api/library/status`, { headers: { "X-Revamp-Token": token } })).scanning, "library scan");
  const music = (await json(`http://127.0.0.1:${p}/api/sources/local/browse/tracks`, { headers: { "X-Revamp-Token": token } })).items.filter((item) => item.kind !== "audiobook").slice(0, 4);
  if (music.length < 3) throw Error("fixture music did not scan");
  const captured = await record(p, token, music, temp);
  if (server && !server.killed) server.kill("SIGTERM");
  editEnv();
  await renderDemoEdit({
    frameDir: captured.frameDir,
    frameCount: captured.count,
    manifestPath: captured.manifestPath,
    workDir: join(OUT, "_work"),
  });
  console.log(`Wrote launch demo v2 to ${OUT}`);
} finally {
  if (server && !server.killed) server.kill("SIGTERM");
}
