// Focus view behaviour: start/stop, timer chips, slider → value readout.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("../..", import.meta.url).pathname);
const browser = process.env.CHROME_BIN || ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chromium"].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, t = 25000) { const u = Date.now() + t; let l; while (Date.now() < u) { try { if (await fn()) return; } catch (e) { l = e; } await sleep(100); } throw new Error(`timeout ${what} ${l?.message || ""}`); }
async function json(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

const fail = [];
const check = (n, ok, d = "") => { if (ok) console.log(`ok   ${n}`); else { fail.push(`${n}${d ? `: ${d}` : ""}`); console.error(`FAIL ${n} ${d}`); } };

const temp = mkdtempSync(join(tmpdir(), "revamp-focusfn-"));
const library = join(temp, "library"); mkdirSync(library);
const data = join(temp, "data"); const p = portOf();
const server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
readFileSync(join(data, "token"), "utf8");

const profile = mkdtempSync(join(tmpdir(), "revamp-focusfn-chrome-"));
const debug = portOf();
const proc = spawn(browser, ["--headless=new", "--no-sandbox", "--mute-audio", "--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
await waitFor(async () => { try { await json(`http://127.0.0.1:${debug}/json/list`); return true; } catch { return false; } }, "chrome");
const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find((x) => x.type === "page");
const socket = new WebSocket(target.webSocketDebuggerUrl);
const reqs = new Map(); let seq = 0;
socket.onmessage = (e) => { const m = JSON.parse(String(e.data)); const d = reqs.get(m.id); if (d) { reqs.delete(m.id); d(m); } };
await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; reqs.set(id, (m) => (m.error ? bad(new Error(m.error.message)) : ok(m.result))); socket.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text); return r.result.value; };

await cdp("Page.enable"); await cdp("Runtime.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: 700, height: 1100, deviceScaleFactor: 1, mobile: false });
await cdp("Page.navigate", { url: `http://127.0.0.1:${p}/` });
await waitFor(async () => await ev("Boolean(document.querySelector('.sidebar'))"), "shell", 30000);
await sleep(1200);
await ev(`(() => { const b = [...document.querySelectorAll('.sidebar .nav button')].find((x) => /focus/i.test(x.textContent)); if (b) b.click(); return true })()`);
await sleep(800);

check("focus view rendered", await ev("Boolean(document.querySelector('.focus-start'))"));
check("value readouts present", await ev("document.querySelectorAll('.focus-value').length === 4"), String(await ev("document.querySelectorAll('.focus-value').length")));
check("four timer chips", await ev("document.querySelectorAll('.focus-tabs button').length === 4"));

// slider -> readout
await ev(`(() => { const r = document.querySelector('input[data-focus="noise"]'); r.value = '0.9'; r.dispatchEvent(new Event('input', { bubbles: true })); return true })()`);
await sleep(300);
const noiseVal = await ev(`document.querySelector('input[data-focus="noise"]').closest('.focus-row').querySelector('.focus-value').textContent`);
check("slider drag updates its readout", noiseVal === "90%", noiseVal);

// timer chip
await ev(`(() => { const b = [...document.querySelectorAll('.focus-tabs button')].find((x) => x.textContent.includes('25')); b.click(); return true })()`);
await sleep(400);
check("timer chip selects", await ev(`[...document.querySelectorAll('.focus-tabs button')].find((x) => x.textContent.includes('25')).classList.contains('active')`));

// start / stop
await ev("document.querySelector('.focus-start').click()");
await sleep(900);
const started = await ev("document.querySelector('.focus-start').textContent");
check("start button flips to a stop action", started === "Stop", started);
const line = await ev("document.querySelector('.focus-line').textContent");
check("status line reports playback", /Playing/.test(line), line);
await ev("document.querySelector('.focus-start').click()");
await sleep(600);
check("stops again", (await ev("document.querySelector('.focus-start').textContent")) === "Start Focus");

socket.close(); proc.kill("SIGTERM"); server.kill("SIGTERM");
await sleep(300);
rmSync(profile, { recursive: true, force: true });
console.log(fail.length ? `${fail.length} FAILURE(S)` : "all focus behaviour checks passed");
if (fail.length) process.exitCode = 1;
