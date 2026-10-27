// Focus view geometry + screenshots. Run: node tests/probe_focus.mjs
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const outDir = process.env.REVAMP_PROBE_DIR || new URL("../out/probe-focus", import.meta.url).pathname;
const browser = process.env.CHROME_BIN || [
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chromium", "google-chrome",
].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
if (!browser) throw new Error("no Chrome/Chromium");

const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, t = 25000) { const u = Date.now() + t; let l; while (Date.now() < u) { try { if (await fn()) return; } catch (e) { l = e; } await sleep(100); } throw new Error(`timeout ${what} ${l?.message || ""}`); }
async function json(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

const REPORT = `(() => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), left: Math.round(r.left) }; };
  const rows = [...document.querySelectorAll('.focus-row')].map((row) => {
    const r = row.getBoundingClientRect();
    const track = row.querySelector('.focus-track');
    const rng = row.querySelector('input[type="range"]');
    const head = row.querySelector('.focus-head');
    return { rowW: Math.round(r.width), headW: head ? Math.round(head.getBoundingClientRect().width) : null,
             trackW: track ? Math.round(track.getBoundingClientRect().width) : null,
             rangeW: rng ? Math.round(rng.getBoundingClientRect().width) : null,
             value: row.querySelector('.focus-value')?.textContent ?? null };
  });
  const chips = [...document.querySelectorAll('.focus-tabs button')].map((b) => { const r = b.getBoundingClientRect();
    return { label: b.textContent, w: Math.round(r.width), h: Math.round(r.height), active: b.classList.contains('active') }; });
  const start = document.querySelector('.focus-start');
  const hero = document.querySelector('.focus-hero');
  return {
    inner: { w: window.innerWidth, h: window.innerHeight },
    hero: box(hero), start: box(start),
    startFont: start ? getComputedStyle(start).fontSize : null,
    startRadius: start ? getComputedStyle(start).borderRadius : null,
    rows, chips,
    advSummary: box(document.querySelector('.focus-adv > summary')),
    note: box(document.querySelector('.focus-note')),
  };
})()`;

const out = [];
let server, page;
try {
  mkdirSync(outDir, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-focus-"));
  const library = join(temp, "library"); mkdirSync(library);
  const data = join(temp, "data"); const p = portOf();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  readFileSync(join(data, "token"), "utf8");

  const profile = mkdtempSync(join(tmpdir(), "revamp-focus-chrome-"));
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
  await cdp("Page.enable"); await cdp("Runtime.enable");

  for (const [w, h, label] of [[700, 1180, "tall-700"], [420, 1100, "panel-420"], [360, 1000, "narrow-360"]]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: false });
    await cdp("Page.navigate", { url: `http://127.0.0.1:${p}/` });
    await waitFor(async () => await ev("Boolean(document.querySelector('.sidebar'))"), "shell", 30000);
    await sleep(1000);
    await ev(`(() => { const b = [...document.querySelectorAll('.sidebar .nav button')].find((x) => /focus/i.test(x.textContent)); if (b) b.click(); return true })()`);
    await sleep(900);
    // The narrow layout puts the panel below the stage: scroll it into view.
    await ev(`(() => { const shell = document.querySelector('.shell'); const panel = document.querySelector('.panel'); if (shell && panel) shell.scrollTop = panel.offsetTop + panel.offsetHeight - shell.clientHeight + 12; return true })()`);
    await sleep(400);
    const rep = await ev(REPORT);
    out.push({ label, rep });
    console.log(`\n--- ${label} inner ${rep.inner.w}x${rep.inner.h}`);
    console.log(`start button ${JSON.stringify(rep.start)} font=${rep.startFont} radius=${rep.startRadius} hero=${JSON.stringify(rep.hero)}`);
    for (const r of rep.rows) console.log(`  row w=${r.rowW} track=${r.trackW} range=${r.rangeW} value=${r.value}`);
    const tracks = rep.rows.filter((r) => r.rangeW != null).map((r) => r.rangeW);
    console.log(`  track alignment: ${new Set(tracks).size === 1 ? "aligned" : "MISALIGNED"} ${JSON.stringify(tracks)}`);
    console.log(`  chips ${JSON.stringify(rep.chips)}`);
    console.log(`  advanced summary ${JSON.stringify(rep.advSummary)}`);
    await snap(`focus-${label}.png`);
  }

  socket.close(); proc.kill("SIGTERM");
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
} catch (err) {
  console.error("ERROR", err.message);
} finally {
  if (server) server.kill("SIGTERM");
  writeFileSync(join(outDir, "probe.json"), JSON.stringify(out, null, 2));
  console.log(`\nartifacts in ${outDir}`);
}
