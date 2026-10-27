// Probe: the EQ-closed gap and the presets-menu clipping, in the in-app
// (floating / frame) homes, across viewport sizes.
// Run: node tests/probe_eq_gap.mjs
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const outDir = process.env.REVAMP_PROBE_DIR || new URL("../out/probe", import.meta.url).pathname;
const browser = process.env.CHROME_BIN || [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "google-chrome", "chromium",
].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
if (!browser) throw new Error("no Chrome/Chromium");

const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, timeout = 25000) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { if (await fn()) return; } catch (err) { last = err; }
    await sleep(100);
  }
  throw new Error(`Timed out: ${what}${last ? ` (${last.message})` : ""}`);
}
async function json(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }

async function openPage(serverPort, width, height, query) {
  const debug = portOf();
  const profile = mkdtempSync(join(tmpdir(), "revamp-probe-chrome-"));
  const proc = spawn(browser, [
    "--headless=new", "--no-sandbox", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
    "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
    `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: "ignore" });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${debug}/json/list`); return true; } catch { return false; } }, "Chrome");
  const target = (await json(`http://127.0.0.1:${debug}/json/list`)).find((x) => x.type === "page");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const requests = new Map();
  let seq = 0;
  socket.onmessage = (e) => { const m = JSON.parse(String(e.data)); const done = requests.get(m.id); if (done) { requests.delete(m.id); done(m); } };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  const cdp = (method, params = {}) => new Promise((ok, bad) => {
    const id = ++seq;
    requests.set(id, (m) => (m.error ? bad(new Error(m.error.message)) : ok(m.result)));
    socket.send(JSON.stringify({ id, method, params }));
  });
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: `http://127.0.0.1:${serverPort}/${query}` });
  await waitFor(async () => (await cdp("Runtime.evaluate", { expression: "Boolean(document.querySelector('#main-window'))" })).result.value, "player windows", 30000);
  await sleep(800);
  return {
    proc, profile, socket, cdp,
    async eval(expression) {
      const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || "eval failed");
      return r.result.value;
    },
    async snap(name) {
      const shot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      writeFileSync(join(outDir, name), Buffer.from(shot.data, "base64"));
    },
    async close() { socket.close(); proc.kill("SIGTERM"); await sleep(200); rmSync(profile, { recursive: true, force: true }); },
  };
}

const RECTS = `(() => {
  const one = (id) => {
    const n = document.getElementById(id);
    if (!n) return null;
    const s = getComputedStyle(n);
    const r = n.getBoundingClientRect();
    return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom),
             width: Math.round(r.width), height: Math.round(r.height), display: s.display, visibility: s.visibility };
  };
  return { main: one('main-window'), eq: one('equalizer-window'), pl: one('playlist-window'),
           inner: { w: window.innerWidth, h: window.innerHeight },
           bodyBg: getComputedStyle(document.body).backgroundColor };
})()`;

const EQ_STATE = `(() => {
  const n = document.getElementById('equalizer-window');
  if (!n) return 'missing';
  const s = getComputedStyle(n); const r = n.getBoundingClientRect();
  return { display: s.display, visibility: s.visibility, h: Math.round(r.height), w: Math.round(r.width) };
})()`;

// Webamp binds on mousedown for its window buttons.
const PRESS = (sel) => `(() => {
  const n = document.querySelector(${JSON.stringify(sel)});
  if (!n) return 'no ' + ${JSON.stringify(sel)};
  for (const t of ['mousedown', 'mouseup', 'click']) n.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return 'pressed ' + ${JSON.stringify(sel)};
})()`;

const MENU_RECTS = `(() => {
  const host = document.getElementById('webamp-context-menu');
  if (!host) return { host: false };
  const menus = [...host.querySelectorAll('.context-menu')].map((n) => {
    const r = n.getBoundingClientRect();
    const s = getComputedStyle(n);
    return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom),
             width: Math.round(r.width), height: Math.round(r.height), display: s.display };
  }).filter((m) => m.display !== 'none' && m.width > 1);
  const hostStyle = getComputedStyle(host);
  return { host: true, hostRect: (() => { const r = host.getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) }; })(),
           hostOverflow: hostStyle.overflow, menus, inner: { w: window.innerWidth, h: window.innerHeight } };
})()`;

const failures = [];
const check = (name, ok, detail = "") => {
  if (ok) console.log(`ok   ${name}`);
  else { failures.push(`${name}${detail ? `: ${detail}` : ""}`); console.error(`FAIL ${name}${detail ? `: ${detail}` : ""}`); }
};

const out = [];
let server, page;
try {
  mkdirSync(outDir, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-probe-"));
  const library = join(temp, "library");
  mkdirSync(library);
  const data = join(temp, "data");
  const p = portOf();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library],
    { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  readFileSync(join(data, "token"), "utf8");

  for (const [w, h, label] of [[1280, 800, "1280x800"], [1440, 900, "1440x900"], [1920, 1080, "1920x1080"], [1024, 700, "1024x700"], [900, 600, "900x600"]]) {
    for (const placement of ["floating", "docked"]) {
      page = await openPage(p, w, h, "?mode=winamp");
      await page.eval(`window.revampPlayerApi?.setVisualizerPlacement('${placement}')`);
      await sleep(900);
      const withEq = await page.eval(RECTS);
      const eqOpen0 = await page.eval(EQ_STATE);
      // close the EQ the way the window's own ✕ does
      await page.eval(PRESS("#equalizer-close"));
      await sleep(900);
      const noEq = await page.eval(RECTS);
      const eqOpen1 = await page.eval(EQ_STATE);
      const gap = noEq.main && noEq.pl ? noEq.pl.top - noEq.main.bottom : null;
      out.push({ label, placement, eqOpen0, eqOpen1, withEq, noEq, gap });
      console.log(`\n--- ${label} ${placement}: eq ${eqOpen0}->${eqOpen1}  main.bottom=${noEq.main?.bottom} pl.top=${noEq.pl?.top} GAP=${gap}`);
      await page.snap(`eqclosed-${placement}-${label}.png`);
      await page.close();
      page = null;
    }
  }
} catch (err) {
  console.error("ERROR", err.message);
  failures.push(err.message);
} finally {
  if (page) await page.close();
  if (server) server.kill("SIGTERM");
  writeFileSync(join(outDir, "probe.json"), JSON.stringify(out, null, 2));
  console.log(`\n${failures.length ? `${failures.length} FAILURE(S)` : "all checks passed"} — artifacts in ${outDir}`);
  if (failures.length) process.exitCode = 1;
}
