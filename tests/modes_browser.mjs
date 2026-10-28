// Headless, muted checks for the placement surfaces, plus screenshots.
// Mini player and the Hermes pane/overlay chrome live in the desktop host;
// this covers the player each mode actually shows, and the desktop window's fit.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const outDir = process.env.REVAMP_MODES_DIR || new URL("../out/modes", import.meta.url).pathname;
const browser = process.env.CHROME_BIN || ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"].find((name) => spawnSync("which", [name], { encoding: "utf8" }).status === 0);
if (!browser) throw new Error("no Chrome/Chromium");

const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, timeout = 20000) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { if (await fn()) return; } catch (err) { last = err; }
    await sleep(100);
  }
  throw new Error(`Timed out: ${what}${last ? ` (${last.message})` : ""}`);
}
async function json(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

let server;
let page;
const failures = [];
function check(name, ok, detail = "") {
  if (ok) console.log(`ok  ${name}`);
  else { failures.push(`${name}${detail ? `: ${detail}` : ""}`); console.error(`FAIL ${name}${detail ? `: ${detail}` : ""}`); }
}

async function openPage(serverPort, width, height, query) {
  const debug = portOf();
  const profile = mkdtempSync(join(tmpdir(), "revamp-modes-chrome-"));
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
  socket.onmessage = (e) => {
    const m = JSON.parse(String(e.data));
    const done = requests.get(m.id);
    if (done) { requests.delete(m.id); done(m); }
  };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  const cdp = (method, params = {}) => new Promise((ok, bad) => {
    const id = ++seq;
    requests.set(id, (m) => m.error ? bad(new Error(m.error.message)) : ok(m.result));
    socket.send(JSON.stringify({ id, method, params }));
  });
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: `http://127.0.0.1:${serverPort}/${query}` });
  await waitFor(async () => (await cdp("Runtime.evaluate", { expression: "Boolean(document.querySelector('#main-window'))" })).result.value, "player windows", 30000);
  await sleep(600);
  return {
    proc, profile, socket, cdp,
    async eval(expression) {
      const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "eval failed");
      return result.result.value;
    },
    async snap(name) {
      const shot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      writeFileSync(join(outDir, name), Buffer.from(shot.data, "base64"));
    },
    async size(width2, height2) {
      await cdp("Emulation.setDeviceMetricsOverride", { width: width2, height: height2, deviceScaleFactor: 1, mobile: false });
      await sleep(400);
    },
    async close() {
      socket.close();
      proc.kill("SIGTERM");
      await sleep(200);
      rmSync(profile, { recursive: true, force: true });
    },
  };
}

const rects = `(() => {
  const one = (id) => {
    const n = document.getElementById(id);
    if (!n) return null;
    const r = n.getBoundingClientRect();
    return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) };
  };
  const md = document.querySelector('#milkdrop-window, #webamp-host .gen-window');
  const m = md ? md.getBoundingClientRect() : null;
  return {
    main: one('main-window'), eq: one('equalizer-window'), pl: one('playlist-window'),
    milk: m && m.width > 2 ? { left: Math.round(m.left), top: Math.round(m.top), right: Math.round(m.right), bottom: Math.round(m.bottom), width: Math.round(m.width), height: Math.round(m.height) } : null,
    bg: getComputedStyle(document.body).backgroundColor,
    fit: window.__revampFit || null,
    inner: { w: window.innerWidth, h: window.innerHeight },
  };
})()`;

try {
  mkdirSync(outDir, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-modes-"));
  const library = join(temp, "library");
  mkdirSync(library);
  const data = join(temp, "data");
  const p = portOf();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], {
    cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data },
  });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  readFileSync(join(data, "token"), "utf8");

  page = await openPage(p, 1280, 800, "");
  await page.snap("full-screen.png");
  const full = await page.eval(rects);
  check("full screen shows the classic stack", full.main && full.eq && full.pl, JSON.stringify(full.main));
  await page.close();

  page = await openPage(p, 900, 700, "?mode=winamp");
  await page.eval("window.revampPlayerApi?.setVisualizerPlacement('docked')");
  await sleep(800);
  let box = await page.eval(rects);
  check("frame pins the stack at the origin", box.main && Math.abs(box.main.left) <= 2 && Math.abs(box.main.top) <= 2, JSON.stringify(box.main));
  if (box.main && box.pl) await page.size(Math.max(280, box.pl.right), Math.max(200, box.pl.bottom));
  await page.eval("window.revampPlayerApi?.setVisualizerPlacement('docked')");
  await sleep(500);
  await page.snap("frame-right.png");
  await page.close();

  page = await openPage(p, 900, 700, "?mode=winamp");
  await page.eval("window.revampPlayerApi?.setVisualizerPlacement('floating')");
  await sleep(800);
  box = await page.eval(rects);
  check("pop into Hermes pins the stack", box.main && Math.abs(box.main.left) <= 2 && Math.abs(box.main.top) <= 2, JSON.stringify(box.main));
  if (box.main && box.pl) await page.size(Math.max(280, box.pl.right + (box.milk ? box.milk.width : 0)), Math.max(200, box.pl.bottom));
  await page.eval("window.revampPlayerApi?.setVisualizerPlacement('floating')");
  await sleep(400);
  await page.snap("pop-into-hermes.png");
  await page.close();

  page = await openPage(p, 1100, 800, "?mode=winamp&float=1");
  await waitFor(async () => {
    const fit = await page.eval("window.__revampFit");
    const main = await page.eval("document.getElementById('main-window')?.getBoundingClientRect().left");
    return fit && fit.w > 40 && Math.abs(main) <= 2;
  }, "desktop fit", 8000);
  const before = await page.eval(rects);
  const dragged = await page.eval(`(() => {
    const bar = document.querySelector('#main-window .draggable');
    const main = document.getElementById('main-window');
    if (!bar || !main) return { missing: true };
    const start = main.getBoundingClientRect().left;
    const r = bar.getBoundingClientRect();
    const fire = (type, x, y) => bar.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window }));
    fire('mousedown', r.left + 8, r.top + 4);
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: r.left + 160, clientY: r.top + 90 }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: r.left + 160, clientY: r.top + 90 }));
    return { start, end: main.getBoundingClientRect().left, moved: Math.abs(main.getBoundingClientRect().left - start) };
  })()`);
  check("desktop player does not drag inside the window", dragged && !dragged.missing && dragged.moved < 2, JSON.stringify(dragged));
  check("desktop background is dark", before.bg === "rgb(11, 14, 20)", before.bg);
  check("desktop uses the Winamp menu for MilkDrop", !document.getElementById('float-milkdrop') && !!document.querySelector('#main-window #option'));
  check("desktop stack is pinned with no offset", before.main && before.eq && before.pl && Math.abs(before.main.left) <= 2 && Math.abs(before.main.top) <= 2 && Math.abs(before.eq.top - before.main.bottom) <= 8 && Math.abs(before.pl.top - before.eq.bottom) <= 8, JSON.stringify(before));
  const tightW = before.pl.right;
  const tightH = before.pl.bottom;
  await page.size(tightW, tightH);
  await waitFor(async () => {
    const now = await page.eval(rects);
    return now.main && Math.abs(now.main.left) <= 2 && Math.abs(now.inner.w - now.pl.right) <= 4;
  }, "desktop cropped to the stack", 5000);
  await page.snap("pop-out-desktop.png");
  const cropped = await page.eval(rects);
  check("desktop window content matches the stack", cropped.inner && Math.abs(cropped.inner.w - cropped.pl.right) <= 4 && Math.abs(cropped.inner.h - cropped.pl.bottom) <= 4, JSON.stringify({ inner: cropped.inner, pl: cropped.pl }));

  const toggled = await page.eval("window.revampPlayerApi.toggleVisualizer(true)");
  check("MilkDrop toggle turns on", toggled === true, String(toggled));
  await sleep(900);
  await waitFor(async () => {
    const now = await page.eval(rects);
    return now.milk && Math.abs(now.milk.left - now.main.right) <= 3 && Math.abs(now.milk.height - (now.pl.bottom - now.main.top)) <= 3;
  }, "milkdrop beside the stack", 5000);
  const withViz = await page.eval(rects);
  check("MilkDrop sits beside the player at the same height", withViz.milk && Math.abs(withViz.milk.left - withViz.main.right) <= 3 && Math.abs(withViz.milk.top - withViz.main.top) <= 3 && Math.abs(withViz.milk.height - (withViz.pl.bottom - withViz.main.top)) <= 3, JSON.stringify(withViz.milk));
  check("MilkDrop stays in the Winamp menu", !document.getElementById('float-milkdrop') && !!document.querySelector('#main-window #option'));
  await page.size(withViz.milk.right, Math.max(withViz.pl.bottom, withViz.milk.bottom));
  await sleep(700);
  await page.snap("pop-out-desktop-milkdrop.png");
  const wide = await page.eval(rects);
  check("window grows to player plus MilkDrop", wide.inner && wide.milk && Math.abs(wide.inner.w - wide.milk.right) <= 4 && Math.abs(wide.inner.h - Math.max(wide.pl.bottom, wide.milk.bottom)) <= 4, JSON.stringify({ inner: wide.inner, milk: wide.milk, pl: wide.pl }));
  await page.close();
  page = null;

  if (failures.length) {
    console.error(`\n${failures.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nall checks passed; screenshots in ${outDir}`);
  }
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  if (page) await page.close().catch(() => {});
  if (server && !server.killed) server.kill("SIGTERM");
}
