// Visual evidence at the stack's own width: EQ open, EQ closed, presets menu.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const outDir = new URL("../out/evidence", import.meta.url).pathname;
const browser = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "google-chrome", "chromium"].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, t = 25000) { const u = Date.now() + t; let l; while (Date.now() < u) { try { if (await fn()) return; } catch (e) { l = e; } await sleep(100); } throw new Error(`timeout ${what} ${l?.message || ""}`); }
async function json(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

mkdirSync(outDir, { recursive: true });
const temp = mkdtempSync(join(tmpdir(), "revamp-ev-"));
const library = join(temp, "library"); mkdirSync(library);
const data = join(temp, "data"); const p = portOf();
const server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
readFileSync(join(data, "token"), "utf8");

const profile = mkdtempSync(join(tmpdir(), "revamp-ev-chrome-"));
const debug = portOf();
const proc = spawn(browser, ["--headless=new", "--no-sandbox", "--mute-audio", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
await waitFor(async () => { try { await json(`http://127.0.0.1:${debug}/json/list`); return true; } catch { return false; } }, "chrome");
const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find((x) => x.type === "page");
const socket = new WebSocket(target.webSocketDebuggerUrl);
const reqs = new Map(); let seq = 0;
socket.onmessage = (e) => { const m = JSON.parse(String(e.data)); const d = reqs.get(m.id); if (d) { reqs.delete(m.id); d(m); } };
await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; reqs.set(id, (m) => (m.error ? bad(new Error(m.error.message)) : ok(m.result))); socket.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text); return r.result.value; };
const snap = async (name) => { const s = await cdp("Page.captureScreenshot", { format: "png" }); writeFileSync(join(outDir, name), Buffer.from(s.data, "base64")); };
const PRESS = (sel) => `(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) return 'no'; const r = n.getBoundingClientRect();
  const o = { bubbles: true, cancelable: true, view: window, clientX: Math.round(r.left + r.width/2), clientY: Math.round(r.top + r.height/2) };
  for (const t of ['mousedown','mouseup','click']) n.dispatchEvent(new MouseEvent(t, o)); return 'ok'; })()`;

await cdp("Page.enable"); await cdp("Runtime.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: 280, height: 420, deviceScaleFactor: 2, mobile: false });
await cdp("Page.navigate", { url: `http://127.0.0.1:${p}/?mode=winamp` });
await waitFor(async () => await ev("Boolean(document.querySelector('#main-window'))"), "windows", 30000);
await sleep(1000);
await ev("window.revampPlayerApi.setVisualizerPlacement('floating')");
await sleep(900);

await snap("1-eq-open.png");
console.log("eq open gap:", await ev(`(() => { const m=document.getElementById('main-window').getBoundingClientRect(); const pl=document.getElementById('playlist-window').getBoundingClientRect(); return Math.round(pl.top - m.bottom); })()`));

await ev(PRESS("#equalizer-close"));
await sleep(1000);
console.log("eq closed gap:", await ev(`(() => { const m=document.getElementById('main-window').getBoundingClientRect(); const pl=document.getElementById('playlist-window').getBoundingClientRect(); return Math.round(pl.top - m.bottom); })()`));
await snap("2-eq-closed.png");

// reopen from the main window's EQ button, then open the presets menu
await ev(PRESS("#equalizer-button"));
await sleep(1000);
console.log("eq reopened:", await ev("Boolean(document.getElementById('equalizer-window'))"));
await ev(PRESS("#presets"));
await sleep(700);
await ev(`(() => { const ul = document.querySelector('#webamp-context-menu .context-menu ul'); if (ul) ul.style.display = 'block'; return true })()`);
await sleep(400);
await snap("3-presets-menu.png");

socket.close(); proc.kill("SIGTERM"); server.kill("SIGTERM");
await sleep(300);
rmSync(profile, { recursive: true, force: true });
console.log("evidence in", outDir);
