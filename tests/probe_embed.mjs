// The Hermes docked pane loads ?embed=1, which has its own shell rules
// (body.embed). Check the same invariants there: rail not clipped, ticker not
// squeezed, focus controls sized and aligned.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const outDir = new URL("../out/probe-embed", import.meta.url).pathname;
const browser = process.env.CHROME_BIN || ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chromium"].find((n) => spawnSync(n, ["--version"], { encoding: "utf8" }).status === 0);
const sleep = (n) => new Promise((r) => setTimeout(r, n));
const portOf = () => Number(execFileSync("python3", ["-c", "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"], { encoding: "utf8" }));
async function waitFor(fn, what, t = 25000) { const u = Date.now() + t; let l; while (Date.now() < u) { try { if (await fn()) return; } catch (e) { l = e; } await sleep(100); } throw new Error(`timeout ${what} ${l?.message || ""}`); }
async function json(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

const fail = [];
const check = (n, ok, d = "") => { if (ok) console.log(`ok   ${n}`); else { fail.push(n); console.error(`FAIL ${n} ${d}`); } };

const REPORT = `(() => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
  const sb = document.querySelector('.sidebar');
  const sbBox = sb.getBoundingClientRect();
  const btns = [...document.querySelectorAll('.sidebar .nav button')].map((b) => { const r = b.getBoundingClientRect();
    return { label: b.textContent.trim().slice(0, 12), clipped: r.bottom > sbBox.bottom + 1 || r.top < sbBox.top - 1 }; });
  const tracks = [...document.querySelectorAll('.focus-row .focus-track')].map((t) => {
    const rng = t.querySelector('input[type="range"]'); return rng ? Math.round(rng.getBoundingClientRect().width) : null; }).filter((v) => v != null);
  return {
    bodyClass: document.body.className,
    sidebar: { ...box(sb), scrollH: sb.scrollHeight, overflowY: getComputedStyle(sb).overflowY },
    clippedButtons: btns.filter((b) => b.clipped).map((b) => b.label),
    ticker: { ...box(document.querySelector('.ticker')), scrollH: document.querySelector('.ticker')?.scrollHeight },
    start: box(document.querySelector('.focus-start')),
    tracks,
    chips: [...document.querySelectorAll('.focus-tabs button')].map((b) => Math.round(b.getBoundingClientRect().width)),
    adv: box(document.querySelector('.focus-adv > summary')),
  };
})()`;

mkdirSync(outDir, { recursive: true });
const temp = mkdtempSync(join(tmpdir(), "revamp-embed-"));
const library = join(temp, "library"); mkdirSync(library);
const data = join(temp, "data"); const p = portOf();
const server = spawn("python3", ["-m", "revamp_core.server", "--port", String(p), "--data", data, "--library", library], { cwd: root, stdio: "ignore", env: { ...process.env, REVAMP_HOME: data } });
await waitFor(async () => { try { await json(`http://127.0.0.1:${p}/api/health`); return true; } catch { return false; } }, "server");
readFileSync(join(data, "token"), "utf8");

const profile = mkdtempSync(join(tmpdir(), "revamp-embed-chrome-"));
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
const snap = async (n) => { const s = await cdp("Page.captureScreenshot", { format: "png" }); writeFileSync(join(outDir, n), Buffer.from(s.data, "base64")); };

await cdp("Page.enable"); await cdp("Runtime.enable");

for (const [w, h, label] of [[900, 620, "embed-900x620"], [640, 560, "embed-640x560"], [420, 500, "embed-420x500"]]) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: false });
  await cdp("Page.navigate", { url: `http://127.0.0.1:${p}/?embed=1` });
  await waitFor(async () => await ev("Boolean(document.querySelector('.sidebar'))"), "shell", 30000);
  await sleep(1200);
  await ev(`(() => { const b = [...document.querySelectorAll('.sidebar .nav button')].find((x) => /focus/i.test(x.textContent)); if (b) b.click(); return true })()`);
  await sleep(800);
  await ev(`(() => { const shell = document.querySelector('.shell'); const panel = document.querySelector('.panel'); if (shell && panel) shell.scrollTop = panel.offsetTop + panel.offsetHeight - shell.clientHeight + 12; return true })()`);
  await sleep(400);
  const rep = await ev(REPORT);
  console.log(`\n--- ${label} body="${rep.bodyClass}"`);
  console.log(`sidebar h=${rep.sidebar.h} scrollH=${rep.sidebar.scrollH} overflowY=${rep.sidebar.overflowY} clipped=${JSON.stringify(rep.clippedButtons)}`);
  console.log(`ticker h=${rep.ticker.h} scrollH=${rep.ticker.scrollH}`);
  console.log(`start ${JSON.stringify(rep.start)} tracks ${JSON.stringify(rep.tracks)} chips ${JSON.stringify(rep.chips)} adv ${JSON.stringify(rep.adv)}`);
  check(`${label}: embed layout applied`, /embed/.test(rep.bodyClass), rep.bodyClass);
  check(`${label}: no nav button clipped`, rep.clippedButtons.length === 0, JSON.stringify(rep.clippedButtons));
  check(`${label}: ticker not squeezed`, rep.ticker.h >= rep.ticker.scrollH - 1, `h=${rep.ticker.h} scrollH=${rep.ticker.scrollH}`);
  check(`${label}: start button sane`, rep.start && rep.start.h <= 52 && rep.start.w <= 340, JSON.stringify(rep.start));
  check(`${label}: slider tracks aligned`, new Set(rep.tracks).size <= 1, JSON.stringify(rep.tracks));
  check(`${label}: chips equal`, new Set(rep.chips).size <= 1, JSON.stringify(rep.chips));
  check(`${label}: advanced visible`, Boolean(rep.adv), JSON.stringify(rep.adv));
  await snap(`embed-${label}.png`);
}

socket.close(); proc.kill("SIGTERM"); server.kill("SIGTERM");
await sleep(300);
rmSync(profile, { recursive: true, force: true });
console.log(fail.length ? `\n${fail.length} FAILURE(S): ${fail.join(", ")}` : "\nall embed checks passed");
if (fail.length) process.exitCode = 1;
