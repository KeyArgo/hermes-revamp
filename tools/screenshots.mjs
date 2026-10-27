#!/usr/bin/env node
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname), output = join(root, "assets/screenshots"), fixture = join(root, "lanes/_e2e/lib"), temp = mkdtempSync(join(tmpdir(), "revamp-shots-")), books = join(temp, "books"), browser = process.env.CHROME_BIN || "google-chrome";
let server;
const sleep = (n) => new Promise((r) => setTimeout(r, n));
const port = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, timeout = 20000) { const until = Date.now() + timeout; while (Date.now() < until) { if (await fn()) return; await sleep(100); } throw Error(`Timed out: ${what}`); }
async function json(url, opts = {}) { const r = await fetch(url, opts); if (!r.ok) throw Error(`${r.status} ${url}`); return r.json(); }
const auth = (token) => ({ "Content-Type": "application/json", "X-Revamp-Token": token });
const post = (p, t, path, body) => json(`http://127.0.0.1:${p}${path}`, { method: "POST", headers: auth(t), body: JSON.stringify(body) });
const put = (p, t, body) => json(`http://127.0.0.1:${p}/api/state`, { method: "PUT", headers: auth(t), body: JSON.stringify(body) });
const putRoute = (p, t, path, body) => json(`http://127.0.0.1:${p}${path}`, { method: "PUT", headers: auth(t), body: JSON.stringify(body) });
const command = (p, t, body) => post(p, t, "/api/command", body);
const connected = (p, t) => json(`http://127.0.0.1:${p}/api/state`, { headers: { "X-Revamp-Token": t } }).then((s) => s.live?.player_connected);

async function pageFor(serverPort, width = 1440, height = 900, query = "") {
  const debug = port(), profile = mkdtempSync(join(tmpdir(), "revamp-chrome-"));
  const process = spawn(browser, ["--headless=new", "--no-sandbox", "--mute-audio", "--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader", `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${debug}/json/list`); return true; } catch { return false; } }, "Chrome");
  const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find((x) => x.type === "page");
  const socket = new WebSocket(target.webSocketDebuggerUrl), requests = new Map(); let seq = 0;
  socket.onmessage = (e) => { const m = JSON.parse(String(e.data)), done = requests.get(m.id); if (done) { requests.delete(m.id); done(m); } };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; requests.set(id, (m) => m.error ? bad(Error(m.error.message)) : ok(m.result)); socket.send(JSON.stringify({ id, method, params })); });
  await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false }); await cdp("Page.navigate", { url: `http://127.0.0.1:${serverPort}/${query}` });
  await waitFor(async () => (await cdp("Runtime.evaluate", { expression: "Boolean(document.querySelector('#webamp-host')?.offsetWidth)" })).result.value, "REVamp page");
  return { process, profile, socket, cdp };
}
async function evalJs(page, expression) { return (await page.cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result.value; }
async function snap(page, name) { const out = await page.cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }); writeFileSync(join(output, name), Buffer.from(out.data, "base64")); }
async function close(page) { page.socket.close(); page.process.kill("SIGTERM"); await sleep(250); rmSync(page.profile, { recursive: true, force: true }); }
async function playing(page, p, t, items, at = 2) { await waitFor(() => connected(p, t), "player connection"); await command(p, t, { action: "load", items, mode: "replace", play: true }); await sleep(1100); await command(p, t, { action: "seek", seconds: at }); }
async function view(page, id, text) { await evalJs(page, `window.revampSetView(${JSON.stringify(id)})`); await waitFor(() => evalJs(page, `document.querySelector('#panel-body')?.innerText.includes(${JSON.stringify(text)})`), `${id} view`); }

try {
  mkdirSync(output, { recursive: true }); mkdirSync(books, { recursive: true });
  const meta = join(temp, "chapters.ffmeta");
  writeFileSync(meta, ";FFMETADATA1\ntitle=The REVamp Field Guide\nartist=REVamp Studio\nalbum=Listening Notes\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=60000\ntitle=Welcome to the library\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=60000\nEND=120000\ntitle=The long walk\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=120000\nEND=180000\ntitle=Closing notes\n");
  execFileSync("ffmpeg", ["-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100", "-f", "ffmetadata", "-i", meta, "-map_metadata", "1", "-map_chapters", "1", "-t", "180", "-c:a", "aac", "-b:a", "64k", join(books, "The REVamp Field Guide.m4b")], { stdio: "ignore" });
  const p = port(); server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", join(temp, "data"), "--library", fixture], { cwd: root, env: { ...process.env, REVAMP_HOME: join(temp, "data"), REVAMP_PORT: String(p) }, stdio: "ignore" });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  const token = readFileSync(join(temp, "data/token"), "utf8").trim();
  await putRoute(p, token, "/api/library/folders", { folders: [{ path: fixture, kind: "music" }, { path: books, kind: "books" }] });
  await waitFor(async () => !(await json(`http://127.0.0.1:${p}/api/library/status`, { headers: { "X-Revamp-Token": token } })).scanning, "library scan");
  const items = (await json(`http://127.0.0.1:${p}/api/sources/local/browse/tracks`, { headers: { "X-Revamp-Token": token } })).items, music = items.filter((x) => x.kind !== "audiobook").slice(0, 4), book = items.find((x) => x.kind === "audiobook");
  if (music.length < 3 || !book) throw Error("fixtures did not scan");
  const lyricData = { synced: [{ t: 0, line: "A small song begins in the library" }, { t: 4, line: "Every shelf remembers where you stopped" }, { t: 9, line: "Keep listening, keep building" }], plain: "", instrumental: false }, lyricCache = {};
  for (const item of items.filter((x) => x.kind !== "audiobook")) lyricCache[JSON.stringify([(item.artist || "").toLowerCase(), (item.title || "").toLowerCase(), (item.album || "").toLowerCase(), Math.round(item.duration || 0)])] = { hit: true, ts: Date.now() / 1000, data: lyricData };
  writeFileSync(join(temp, "data/lyrics-cache.json"), JSON.stringify(lyricCache));
  await post(p, token, "/api/library/resume", { track_id: book.id.split(":").at(-1), position: 74 }); await post(p, token, "/api/playlists", { name: "Screenshot mix", items: music.slice(0, 3) });
  await post(p, token, "/api/agent_event", { kind: "tool_start", tool: "revamp_library", preview: "Queueing a focused work mix" }); await post(p, token, "/api/agent_event", { kind: "approval_request", tool: "shell", preview: "Waiting for approval" });
  let page;
  if (!process.env.REVAMP_CAPTURE_TAIL) {
  await put(p, token, { playlist: music, current_index: 0, skin: "classic", settings: { milkdropOpen: true, ticker: true, duck_on_approval: true } }); page = await pageFor(p); await playing(page, p, token, music); await sleep(3500); await snap(page, "full-player.png"); await close(page);
  await put(p, token, { playlist: music, current_index: 0, skin: "builtin:REVamp-Nous.wsz", settings: { milkdropOpen: false } }); page = await pageFor(p); await playing(page, p, token, music); await sleep(1800); await snap(page, "nous.png"); await close(page);
  await put(p, token, { playlist: [book], current_index: 0, skin: "classic", settings: { milkdropOpen: false } }); page = await pageFor(p); await playing(page, p, token, [book], 74); await view(page, "books", "The REVamp Field Guide"); await waitFor(() => evalJs(page, "document.querySelector('.np-book:not([hidden])') && document.querySelector('.np-chapter:not([hidden])')"), "book controls"); await snap(page, "books.png"); await close(page);
  await put(p, token, { playlist: music, current_index: 0, skin: "classic", settings: { milkdropOpen: false } }); page = await pageFor(p); await playing(page, p, token, music); await view(page, "radio", "Popular"); await waitFor(() => evalJs(page, "document.querySelectorAll('.card-row').length > 2"), "radio presets"); await snap(page, "radio.png"); await close(page);
  page = await pageFor(p); await playing(page, p, token, music); await evalJs(page, "window.revampSetView('library')"); await sleep(900); await evalJs(page, "document.querySelector('button[data-t=artists]')?.click()"); await sleep(600); await snap(page, "library.png"); await close(page);
  page = await pageFor(p); await playing(page, p, token, music); await view(page, "skins", "Winamp Skin Museum"); await sleep(1200); await snap(page, "skins.png"); await close(page);
  }
  page = await pageFor(p); await playing(page, p, token, music); await waitFor(() => evalJs(page, "!document.querySelector('.np-lyrics-btn')?.hidden"), "lyrics"); await evalJs(page, "document.querySelector('.np-lyrics-btn')?.click()"); await sleep(900); await snap(page, "lyrics.png"); await close(page);
  page = await pageFor(p); await playing(page, p, token, music); await post(p, token, "/api/agent_event", { kind: "tool_start", tool: "revamp_library", preview: "Queueing a focused work mix" }); await post(p, token, "/api/agent_event", { kind: "approval_request", tool: "shell", preview: "Waiting for approval" }); await waitFor(() => evalJs(page, "document.querySelector('#ticker-track')?.innerText.includes('revamp_library')"), "agent activity"); await snap(page, "agent-activity.png"); await close(page);
  await put(p, token, { playlist: music, current_index: 0, skin: "classic", settings: { milkdropOpen: false } }); page = await pageFor(p, 620, 620, "?embed=1"); await playing(page, p, token, music); await sleep(1000); await snap(page, "mini-player.png"); await close(page);
  await put(p, token, { playlist: music, current_index: 0, skin: "classic", settings: { milkdropOpenFloating: true } }); page = await pageFor(p, 900, 620, "?mode=winamp"); await page.cdp("Emulation.setDefaultBackgroundColorOverride", { color: { r: 8, g: 12, b: 18, a: 1 } }); await playing(page, p, token, music); await evalJs(page, "window.revampPlayerApi?.setVisualizerPlacement('floating')"); await sleep(3500); await snap(page, "winamp-milkdrop.png"); await close(page);
  page = await pageFor(p); await playing(page, p, token, music); await view(page, "agent", "Duck volume on approval"); await snap(page, "settings.png"); await close(page);
  rmSync(join(output, "nous-dark.png"), { force: true }); console.log(`Saved screenshots in ${output}`);
} finally { if (server && !server.killed) server.kill("SIGTERM"); rmSync(temp, { recursive: true, force: true }); }
