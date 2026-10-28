// Probe: the EQ presets menu, in the in-app homes, across viewport sizes.
// Run: node tools/probes/probe_eq_menu.mjs
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("../..", import.meta.url).pathname);
const outDir = process.env.REVAMP_PROBE_DIR || new URL("../../out/probe-menu", import.meta.url).pathname;
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
  const until = Date.now() + timeout; let last;
  while (Date.now() < until) { try { if (await fn()) return; } catch (e) { last = e; } await sleep(100); }
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
  const requests = new Map(); let seq = 0;
  socket.onmessage = (e) => { const m = JSON.parse(String(e.data)); const d = requests.get(m.id); if (d) { requests.delete(m.id); d(m); } };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  const cdp = (method, params = {}) => new Promise((ok, bad) => {
    const id = ++seq; requests.set(id, (m) => (m.error ? bad(new Error(m.error.message)) : ok(m.result)));
    socket.send(JSON.stringify({ id, method, params }));
  });
  await cdp("Page.enable"); await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: `http://127.0.0.1:${serverPort}/${query}` });
  await waitFor(async () => (await cdp("Runtime.evaluate", { expression: "Boolean(document.querySelector('#main-window'))" })).result.value, "player windows", 30000);
  await sleep(900);
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

const PRESS = (sel) => `(() => {
  const n = document.querySelector(${JSON.stringify(sel)});
  if (!n) return 'no ' + ${JSON.stringify(sel)};
  const r = n.getBoundingClientRect();
  const o = { bubbles: true, cancelable: true, view: window, clientX: Math.round(r.left + r.width / 2), clientY: Math.round(r.top + r.height / 2) };
  for (const t of ['mousedown', 'mouseup', 'click']) n.dispatchEvent(new MouseEvent(t, o));
  return 'pressed ' + ${JSON.stringify(sel)};
})()`;

const REPORT = `(() => {
  const one = (id) => { const n = document.getElementById(id); if (!n) return null; const r = n.getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) }; };
  const host = document.getElementById('webamp-context-menu');
  const menus = host ? [...host.querySelectorAll('.context-menu')].map((n) => {
    const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
    const sub = n.querySelector('ul');
    const ss = sub ? getComputedStyle(sub) : null;
    return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height), display: s.display, cls: n.className, marginLeft: s.marginLeft, submenu: ss ? { left: ss.left, right: ss.right, marginRight: ss.marginRight } : null };
  }).filter((m) => m.display !== 'none' && m.width > 1) : [];
  const clip = [];
  let el = host;
  while (el && el !== document.documentElement) {
    const s = getComputedStyle(el);
    if (s.overflow !== 'visible' || s.overflowX !== 'visible' || s.overflowY !== 'visible') {
      const r = el.getBoundingClientRect();
      clip.push({ id: el.id || el.className || el.tagName, overflow: s.overflow, overflowX: s.overflowX, overflowY: s.overflowY, rect: { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom) } });
    }
    el = el.parentElement;
  }
  const stack = document.getElementById('webamp-host');
  return { eq: one('equalizer-window'), main: one('main-window'), pl: one('playlist-window'), menus, host: host ? one('webamp-context-menu') : null,
           hostDataset: host ? JSON.stringify(host.dataset) : null, stackRect: stack ? one('webamp-host') : null, hostParent: host?.parentElement?.id || host?.parentElement?.className || null, clip, inner: { w: window.innerWidth, h: window.innerHeight } };
})()`;

const out = []; let server, page;
try {
  mkdirSync(outDir, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-probe-"));
  const library = join(temp, "library"); mkdirSync(library);
  const data = join(temp, "data");
  const p = portOf();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library],
    { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  readFileSync(join(data, "token"), "utf8");

  for (const [w, h, label] of [[280, 420, "stack-width-280"], [420, 520, "overlay-420"], [1280, 800, "1280x800"]]) {
    for (const placement of ["floating", "docked"]) {
      page = await openPage(p, w, h, "?mode=winamp");
      await page.eval(`window.revampPlayerApi?.setVisualizerPlacement('${placement}')`);
      await sleep(900);
      const pressed = await page.eval(PRESS("#presets"));
      await sleep(700);
      // :hover cannot be synthesised, so force the submenu open and measure it.
      await page.eval(`(() => { const ul = document.querySelector('#webamp-context-menu .context-menu ul'); if (ul) ul.style.display = 'block'; return true })()`);
      await sleep(200);
      const sub = await page.eval(`(() => {
        const ul = document.querySelector('#webamp-context-menu .context-menu ul');
        if (!ul) return null;
        const r = ul.getBoundingClientRect();
        const host = document.getElementById('webamp-host').getBoundingClientRect();
        return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width),
                 insideHost: r.left >= host.left - 1 && r.right <= host.right + 1, insideHostV: r.top >= host.top - 1 && r.bottom <= host.bottom + 1, hostRight: Math.round(host.right), hostBottom: Math.round(host.bottom) };
      })()`);
      const rep = await page.eval(REPORT);
      const stack = rep.eq ? rep.eq : null;
      for (const m of rep.menus) {
        m.overflowRight = stack ? m.right - (stack.left + 275) : null;
        m.clippedByViewport = m.right > rep.inner.w;
      }
      out.push({ label, placement, pressed, rep });
      console.log(`\n--- ${label} ${placement} ${pressed}`);
      console.log("eq", JSON.stringify(rep.eq), "inner", JSON.stringify(rep.inner));
      console.log("menus", JSON.stringify(rep.menus));
      console.log("submenu", JSON.stringify(sub));
      console.log("hostDataset", rep.hostDataset, "stackRect", JSON.stringify(rep.stackRect));
      console.log("clip ancestors", JSON.stringify(rep.clip));
      await page.snap(`menu-${placement}-${label}.png`);
      await page.close(); page = null;
    }
  }
} catch (err) {
  console.error("ERROR", err.message);
} finally {
  if (page) await page.close();
  if (server) server.kill("SIGTERM");
  writeFileSync(join(outDir, "probe.json"), JSON.stringify(out, null, 2));
  console.log(`\nartifacts in ${outDir}`);
}
