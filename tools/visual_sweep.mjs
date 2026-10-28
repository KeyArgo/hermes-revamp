#!/usr/bin/env node
/* Capture the supported responsive matrix and compare it to approved images.
 * `node tools/visual_sweep.mjs --approve` refreshes baselines intentionally.
 * Normal runs never modify tests/visual/baseline.
 */
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const approve = process.argv.includes("--approve");
const output = resolve(process.env.REVAMP_VISUAL_DIR || join(root, "out", "visual-sweep"));
const baseline = join(root, "tests", "visual", "baseline");
const browser = process.env.CHROME_BIN || ["google-chrome", "chromium"].find(x => spawnSync(x, ["--version"], { stdio: "ignore" }).status === 0);
if (!browser) throw new Error("Chrome/Chromium is required (set CHROME_BIN)");
const diskTmp = process.env.TMPDIR || tmpdir(); mkdirSync(diskTmp, { recursive: true }); mkdirSync(output, { recursive: true });
const port = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
const wait = async (fn, label, ms = 30000) => { const end = Date.now() + ms; let err; while (Date.now() < end) { try { if (await fn()) return; } catch (e) { err = e; } await new Promise(r => setTimeout(r, 100)); } throw new Error(`timeout: ${label}${err ? ` (${err.message})` : ""}`); };
const json = async u => { const r = await fetch(u); if (!r.ok) throw new Error(`${r.status} ${u}`); return r.json(); };

async function page(serverPort, width, mode, theme) {
  const debug = port(), profile = mkdtempSync(join(diskTmp, "revamp-visual-chrome-"));
  const proc = spawn(browser, ["--headless=new", "--no-sandbox", "--mute-audio", `--remote-debugging-port=${debug}`, "--remote-allow-origins=*", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  await wait(() => json(`http://127.0.0.1:${debug}/json/list`), "Chrome");
  const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find(x => x.type === "page");
  const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
  let id = 0; const pending = new Map(); ws.onmessage = e => { const m = JSON.parse(String(e.data)); if (pending.has(m.id)) { const f = pending.get(m.id); pending.delete(m.id); m.error ? f[1](Error(m.error.message)) : f[0](m.result); } };
  const call = (method, params = {}) => new Promise((ok, no) => { const n = ++id; pending.set(n, [ok, no]); ws.send(JSON.stringify({ id: n, method, params })); });
  await call("Page.enable"); await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", { width, height: 800, deviceScaleFactor: 1, mobile: false });
  await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await call("Page.navigate", { url: `http://127.0.0.1:${serverPort}/?${mode === "control" ? "control=1" : "mode=full"}` });
  await wait(async () => (await call("Runtime.evaluate", { expression: "Boolean(document.querySelector('#nav'))", returnByValue: true })).result.value, "page render");
  return { async png() { return Buffer.from((await call("Page.captureScreenshot", { format: "png" })).data, "base64"); }, async close() {
    ws.close(); proc.kill("SIGTERM"); await new Promise(ok => setTimeout(ok, 150));
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } };
}

const serverData = mkdtempSync(join(diskTmp, "revamp-visual-data-")), serverPort = port();
const server = spawn("python3", ["-m", "revamp_core.server", "--port", String(serverPort), "--data", serverData, "--library", serverData], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: serverData } });
const changed = [];
try {
  await wait(() => json(`http://127.0.0.1:${serverPort}/api/health`), "REVamp server");
  for (const width of [240, 400, 700, 1000]) for (const mode of ["full", "control"]) for (const theme of ["light", "dark"]) {
    const name = `${width}-${mode}-${theme}.png`, file = join(output, name), p = await page(serverPort, width, mode, theme);
    writeFileSync(file, await p.png()); await p.close();
    const base = join(baseline, name);
    if (approve) { mkdirSync(baseline, { recursive: true }); execFileSync("magick", [file, "-resize", "50%", "PNG8:" + base]); continue; }
    if (!existsSync(base)) { changed.push(`${name}: missing baseline`); continue; }
    const metric = spawnSync("magick", [base, file, "-resize", "50%", "-metric", "AE", "-compare", "null:"], { encoding: "utf8" });
    const pixels = (width / 2) * 400; const differing = Number((metric.stderr.match(/[0-9]+/) || ["0"])[0]); const pct = differing * 100 / pixels;
    if (pct > 0.25) changed.push(`${name}: ${pct.toFixed(2)}%`);
  }
} finally { server.kill("SIGTERM"); rmSync(serverData, { recursive: true, force: true }); }
if (approve) console.log(`approved visual baselines in ${baseline}`);
else if (changed.length) { console.error(`visual changes:\n${changed.join("\n")}`); process.exitCode = 1; }
else console.log("visual sweep: all views match approved baselines");
