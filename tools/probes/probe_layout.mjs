// Layout overflow probe for the full player page: which containers clip their
// content at a given viewport, and by how much. Run: node tools/probes/probe_layout.mjs
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("../..", import.meta.url).pathname);
const outDir = process.env.REVAMP_PROBE_DIR || new URL("../../out/probe-layout", import.meta.url).pathname;
const browser = process.env.CHROME_BIN || [
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "chromium", "google-chrome",
].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
if (!browser) throw new Error("no Chrome/Chromium");

const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, t = 25000) { const u = Date.now() + t; let l; while (Date.now() < u) { try { if (await fn()) return; } catch (e) { l = e; } await sleep(100); } throw new Error(`timeout ${what} ${l?.message || ""}`); }
async function json(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

const REPORT = `(() => {
  const q = (sel) => document.querySelector(sel);
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width), h: Math.round(r.height) }; };
  const over = (el) => { if (!el) return null; return { scrollH: el.scrollHeight, clientH: el.clientHeight, overflowY: el.scrollHeight - el.clientHeight, styleOverflowY: getComputedStyle(el).overflowY }; };
  const sidebar = q('.sidebar');
  const navButtons = [...document.querySelectorAll('.sidebar .nav button')].map((b) => {
    const r = b.getBoundingClientRect();
    const s = sidebar.getBoundingClientRect();
    return { label: b.textContent.trim().slice(0, 18), top: Math.round(r.top), bottom: Math.round(r.bottom),
             clippedBelow: r.bottom > s.bottom + 1, clippedAbove: r.top < s.top - 1 };
  });
  const np = q('.nowplaying-card');
  return {
    inner: { w: window.innerWidth, h: window.innerHeight },
    bodyScroll: { scrollH: document.body.scrollHeight, clientH: document.body.clientHeight },
    sidebar: { ...box(sidebar), ...over(sidebar) },
    navButtons,
    nowPlaying: box(np),
    ticker: { ...box(q('.ticker')), ...over(q('.ticker')), text: (q('.ticker')?.textContent || '').trim().slice(0, 40) },
    panel: { ...box(q('.panel')), ...over(q('.panel-body')) },
    panelBody: over(q('.panel-body')),
    shell: { ...box(q('.shell')), ...over(q('.shell')) },
    app: over(q('#app')),
  };
})()`;

const out = [];
let server, page;
try {
  mkdirSync(outDir, { recursive: true });
  const temp = mkdtempSync(join(tmpdir(), "revamp-layout-"));
  const library = join(temp, "library"); mkdirSync(library);
  const data = join(temp, "data"); const p = portOf();
  server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
  await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
  const token = readFileSync(join(data, "token"), "utf8").trim();

  const profile = mkdtempSync(join(tmpdir(), "revamp-layout-chrome-"));
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

  for (const [w, h, label] of [[1440, 900, "1440x900"], [1000, 460, "1000x460"], [1000, 380, "1000x380"], [1400, 400, "1400x400"], [860, 700, "860x700-embed"]]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: `http://127.0.0.1:${p}/` });
    await waitFor(async () => await ev("Boolean(document.querySelector('.sidebar'))"), "shell", 30000);
    await sleep(1200);

    // the skins view is where the rail was reported cut off
    await ev(`(() => { const b = [...document.querySelectorAll('.sidebar .nav button')].find((x) => /skin/i.test(x.textContent)); if (b) b.click(); return true })()`);
    await sleep(900);
    const rep = await ev(REPORT);
    // Scroll the rail to its end: every button must become reachable.
    const scrolled = await ev(`(() => {
      const sb = document.querySelector('.sidebar');
      sb.scrollTop = sb.scrollHeight;
      const s = sb.getBoundingClientRect();
      const btns = [...document.querySelectorAll('.sidebar .nav button')].map((b) => { const r = b.getBoundingClientRect();
        return { label: b.textContent.trim().slice(0, 14), visible: r.top >= s.top - 1 && r.bottom <= s.bottom + 1 }; });
      const card = document.querySelector('.nowplaying-card');
      const cr = card ? card.getBoundingClientRect() : null;
      const out = { scrollTop: Math.round(sb.scrollTop), scrollable: sb.scrollHeight > sb.clientHeight,
        lastButtonVisible: btns.length ? btns[btns.length - 1].visible : null,
        hiddenButtons: btns.filter((b) => !b.visible).map((b) => b.label),
        cardVisible: cr ? cr.bottom <= s.bottom + 1 : null };
      sb.scrollTop = 0;
      return out;
    })()`);
    rep.scrolled = scrolled;
    // Now with a busy agent ticker: does the bottom strip eat the shell?
    await ev(`(() => { const t = document.querySelector('.ticker'); if (!t) return false;
      const track = t.querySelector('.ticker-track') || t;
      track.innerHTML = Array.from({ length: 12 }, (_, i) => '<span class="t-item"><b class="t-tool">revamp_player</b> status ok ' + i + '</span>').join('');
      return true })()`);
    await sleep(400);
    const busy = await ev(REPORT);
    out.push({ label, rep, busy });
    const clipped = rep.navButtons.filter((b) => b.clippedBelow || b.clippedAbove);
    console.log(`\n--- ${label} inner ${rep.inner.w}x${rep.inner.h}`);
    console.log(`sidebar h=${rep.sidebar.h} scrollH=${rep.sidebar.scrollH} overflowY=${rep.sidebar.styleOverflowY}`);
    console.log(`nav buttons clipped: ${clipped.length ? JSON.stringify(clipped) : "none"}`);
    console.log(`nowplaying bottom=${rep.nowPlaying?.bottom} (sidebar bottom ${rep.sidebar.bottom})`);
    console.log(`ticker h=${rep.ticker.h} bottom=${rep.ticker.bottom}`);
    console.log(`panelBody overflowY=${rep.panelBody?.overflowY}`);
    console.log(`rail scrollable=${rep.scrolled.scrollable} scrollTop=${rep.scrolled.scrollTop} lastBtnVisible=${rep.scrolled.lastButtonVisible} cardVisible=${rep.scrolled.cardVisible} hidden=${JSON.stringify(rep.scrolled.hiddenButtons)}`);
    const busyClipped = busy.navButtons.filter((b) => b.clippedBelow || b.clippedAbove);
    console.log(`[busy ticker] ticker h=${busy.ticker.h} scrollH=${busy.ticker.scrollH} shell h=${busy.shell.h} sidebar h=${busy.sidebar.h}`);
    console.log(`[busy ticker] nav clipped: ${busyClipped.length ? JSON.stringify(busyClipped) : "none"}`);
    await snap(`skins-${label}.png`);
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
