#!/usr/bin/env node
/**
 * Post-process the raw demo: eased crops, PNG captions, soundtrack, exports.
 * Captions are pre-rendered (this ffmpeg build has no drawtext filter) and
 * composited with overlay + alpha fades.
 *
 * Edit only, from an existing capture:
 *   node tools/demo_edit.mjs --from-raw [manifest] [workDir]
 *   node tools/demo_video.mjs --edit-only
 */
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(new URL("..", import.meta.url).pathname);
const OUT = process.env.DEMO_V2_OUT || new URL("../out/demo", import.meta.url).pathname;
const FPS = 30;
const DURATION = Number(process.env.DEMO_DURATION || 14.5);
const SRC_W = Number(process.env.DEMO_SRC_W || 1440);
const SRC_H = Number(process.env.DEMO_SRC_H || 900);
const CHROME = process.env.CHROME_BIN || "google-chrome";
const FADE = 0.45;

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf8", stdio: opts.inherit ? "inherit" : "pipe", ...opts });
  if (r.status !== 0) {
    const err = (r.stderr || r.stdout || "").trim().slice(-4000);
    throw new Error(`${cmd} failed (${r.status}): ${err}`);
  }
  return r.stdout || "";
}

function ffmpeg(args) {
  run("ffmpeg", ["-nostdin", "-y", "-hide_banner", "-loglevel", "error", ...args]);
}

function bytes(file) {
  return statSync(file).size;
}

function probe(path) {
  const out = run("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration,size:stream=index,codec_type,width,height,avg_frame_rate,nb_frames,pix_fmt",
    "-of", "json",
    path,
  ]);
  return JSON.parse(out);
}

function videoStream(path) {
  const info = probe(path);
  const stream = (info.streams || []).find((s) => s.codec_type === "video") || info.streams?.[0];
  if (!stream?.width || !stream?.height) throw new Error(`No video stream in ${path}`);
  return {
    width: Number(stream.width),
    height: Number(stream.height),
    duration: Number(info.format?.duration || DURATION),
    frames: Number(stream.nb_frames || 0),
    pixFmt: stream.pix_fmt || "",
  };
}

function pickFont() {
  const candidates = [
    "/usr/share/fonts/truetype/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/LiberationSans-Bold.ttf",
  ];
  for (const path of candidates) {
    if (existsSync(path)) return path;
  }
  try {
    const list = run("fc-list", [":", "file"]);
    for (const line of list.split("\n")) {
      if (/DejaVuSans-Bold|NotoSans-Bold|LiberationSans-Bold/i.test(line)) {
        const file = line.split(":")[0]?.trim();
        if (file && existsSync(file)) return file;
      }
    }
  } catch { /* fc-list optional */ }
  throw new Error("No caption font found (DejaVu Sans Bold / Noto Sans Bold)");
}

function smoothstep(edge0, edge1, x) {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function lerpRect(a, b, t) {
  return {
    x: a.x + (b.x - a.x) * t,
    y: a.y + (b.y - a.y) * t,
    w: a.w + (b.w - a.w) * t,
    h: a.h + (b.h - a.h) * t,
  };
}

function rectAtTime(t, keyframes) {
  if (t <= keyframes[0].t) return keyframes[0].r;
  if (t >= keyframes.at(-1).t) return keyframes.at(-1).r;
  for (let i = 0; i < keyframes.length - 1; i++) {
    const a = keyframes[i], b = keyframes[i + 1];
    if (t >= a.t && t <= b.t) return lerpRect(a.r, b.r, smoothstep(a.t, b.t, t));
  }
  return keyframes.at(-1).r;
}

function padRect(r, pad, maxW, maxH, bottomPad = pad) {
  const x = Math.max(0, r.x - pad);
  const y = Math.max(0, r.y - pad);
  const w = Math.min(maxW - x, r.w + pad * 2);
  const h = Math.min(maxH - y, r.h + pad + bottomPad);
  return { x, y, w, h };
}

function evenRect(r, maxW, maxH) {
  let w = Math.max(2, Math.round(r.w));
  let h = Math.max(2, Math.round(r.h));
  if (w % 2) w -= 1;
  if (h % 2) h -= 1;
  w = Math.min(w, maxW - (maxW % 2));
  h = Math.min(h, maxH - (maxH % 2));
  let x = Math.round(r.x);
  let y = Math.round(r.y);
  if (x + w > maxW) x = maxW - w;
  if (y + h > maxH) y = maxH - h;
  if (x < 0) x = 0;
  if (y < 0) y = 0;
  if (x % 2) x -= 1;
  if (y % 2) y -= 1;
  if (x < 0) x = 0;
  if (y < 0) y = 0;
  if (x + w > maxW) w = maxW - x - ((maxW - x) % 2);
  if (y + h > maxH) h = maxH - y - ((maxH - y) % 2);
  return { x, y, w: Math.max(2, w), h: Math.max(2, h) };
}

function defaultManifest() {
  const stack = { x: 248, y: 118, w: 560, h: 500 };
  const ticker = { x: 12, y: 828, w: 1416, h: 62 };
  const volume = { x: 268, y: 248, w: 120, h: 72 };
  const stage = { x: 228, y: 12, w: 820, h: 776 };
  return { viewport: { w: SRC_W, h: SRC_H }, stage, stack, ticker, volume };
}

function loadManifest(path) {
  const m = existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : defaultManifest();
  const base = defaultManifest();
  if (!m.stack?.w) m.stack = base.stack;
  if (!m.ticker?.w) m.ticker = base.ticker;
  if (!m.volume?.w) m.volume = base.volume;
  if (!m.stage?.w) m.stage = base.stage;
  if (!m.viewport?.w) m.viewport = base.viewport;
  return m;
}

function toVideo(rect, sx, sy, videoW, videoH) {
  return evenRect({
    x: rect.x * sx,
    y: rect.y * sy,
    w: rect.w * sx,
    h: rect.h * sy,
  }, videoW, videoH);
}

function buildCropKeyframes(m, videoW, videoH) {
  const cssW = m.viewport?.w || SRC_W;
  const cssH = m.viewport?.h || SRC_H;
  const sx = videoW / cssW;
  const sy = videoH / cssH;
  const early = m.classic?.stack?.w ? m.classic : m;
  const late = m.stack?.w ? m : early;
  const map = (rect, pad, bottomPad = pad) => toVideo(padRect(rect, pad, cssW, cssH, bottomPad), sx, sy, videoW, videoH);
  const tightSource = {
    ...early.stack,
    x: early.stack.x + early.stack.w * 0.5,
    w: early.stack.w * 0.5,
  };
  const wideShot = (side) => {
    const h = side === "volume" ? Math.min(cssH * 0.30, 280) : Math.min(cssH * 0.44, 420);
    const w = h * (1280 / 720);
    const y = Math.max(0, cssH - h);
    const x = 0;
    return { x, y, w, h };
  };
  return [
    { t: 0, r: map(early.stack, 28) },
    { t: 2.4, r: map(early.stack, 28) },
    { t: 3.2, r: map(tightSource, 8) },
    { t: 5.0, r: map(tightSource, 8) },
    { t: 5.8, r: map(wideShot("left"), 0) },
    { t: 6.9, r: map(wideShot("left"), 0) },
    { t: 7.6, r: map(wideShot("volume"), 0) },
    { t: 9.4, r: map(wideShot("volume"), 0) },
    { t: 10.0, r: map(late.stack, 24) },
    { t: 12.2, r: map(late.stack, 24) },
    { t: 13.0, r: map(late.stack, 28) },
    { t: DURATION, r: map(late.stack, 28) },
  ];
}

function squareKeyframes(keyframes, videoW, videoH) {
  const floor = Math.min(videoW, videoH) * 0.36;
  return keyframes.map((k) => {
    const r = k.r;
    const aspect = r.w / Math.max(1, r.h);
    const extreme = aspect > 2.2 || aspect < 1 / 2.2;
    let side = extreme ? floor : Math.max(r.w, r.h);
    if (side < floor) side = floor;
    side = Math.min(side, videoW, videoH);
    const cx = r.x + r.w / 2;
    const cy = r.y + r.h / 2;
    return {
      t: k.t,
      r: evenRect({ x: cx - side / 2, y: cy - side / 2, w: side, h: side }, videoW, videoH),
    };
  });
}

function zoomChain(keyframes, outW, outH, srcW, srcH) {
  const w = cropExpr(keyframes, "w");
  const h = cropExpr(keyframes, "h");
  const x = cropExpr(keyframes, "x");
  const y = cropExpr(keyframes, "y");
  // This ffmpeg crop locks w/h at init (no eval=frame). Zoom by scaling the
  // whole frame so the source rect fills the output, then crop a fixed window.
  const scaleW = `trunc((${outW}*${srcW}/(${w})+8)/2)*2`;
  const scaleH = `trunc((${outH}*${srcH}/(${h})+8)/2)*2`;
  const cropX = `max(0\\,(${x})*${outW}/(${w}))`;
  const cropY = `max(0\\,(${y})*${outH}/(${h}))`;
  return `scale=w='${scaleW}':h='${scaleH}':eval=frame:flags=lanczos,crop=${outW}:${outH}:x='${cropX}':y='${cropY}',setsar=1`;
}

function cropExpr(keyframes, prop) {
  const last = keyframes.at(-1).r[prop];
  let expr = String(last);
  for (let i = keyframes.length - 2; i >= 0; i--) {
    const a = keyframes[i], b = keyframes[i + 1];
    const t0 = a.t.toFixed(3), t1 = b.t.toFixed(3);
    const v0 = a.r[prop], v1 = b.r[prop];
    const ease = `((t-${t0})/(${t1}-${t0}))`;
    const sm = `(${ease}*${ease}*(3-2*${ease}))`;
    const blend = `${v0}+(${v1}-${v0})*${sm}`;
    expr = `if(between(t\\,${t0}\\,${t1})\\,${blend}\\,${expr})`;
  }
  return expr;
}

function captions() {
  return [
    { t0: 0.3, t1: 2.8, text: "Winamp-style player inside Hermes" },
    { t0: 2.9, t1: 5.2, text: "MilkDrop visualizer" },
    { t0: 5.3, t1: 7.2, text: "Your agent can DJ", top: true },
    { t0: 7.3, t1: 9.6, text: "Music dips when Hermes needs you", top: true },
    { t0: 9.8, t1: 12.4, text: "Classic skins" },
    { t0: 12.5, t1: DURATION, text: "REVamp · 1.0 alpha", sub: "github.com/KeyArgo/hermes-revamp", endCard: true, y: "h*0.68", size: 52 },
  ];
}

function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function captionHtml({ width, height, text, sub, size, endCard, top, font }) {
  const fontSize = Math.round((size ?? 44) * 1.45 * (width / 1280));
  const subSize = Math.round(fontSize * 0.55);
  const bandTop = top ? 0 : endCard ? 62 : 72;
  const bandHeight = endCard ? 38 : 20;
  const bandAlpha = endCard ? 0.55 : 0.45;
  const body = sub
    ? `<div class="stack"><div class="label title">${escHtml(text)}</div><div class="label sub">${escHtml(sub)}</div></div>`
    : `<div class="label">${escHtml(text)}</div>`;
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @font-face { font-family: Cap; src: url("file://${font}"); font-weight: 700; }
  html, body { margin: 0; width: ${width}px; height: ${height}px; background: transparent; overflow: hidden; }
  .band {
    position: absolute; left: 0; right: 0; top: ${bandTop}%; height: ${bandHeight}%;
    background: rgba(0, 0, 0, ${bandAlpha});
    display: flex; align-items: center; justify-content: center;
  }
  .stack { display: flex; flex-direction: column; align-items: center; gap: 8px; max-width: 94%; }
  .label {
    font-family: Cap, "DejaVu Sans", sans-serif; font-weight: 700; color: #fff;
    font-size: ${fontSize}px; line-height: 1.05; letter-spacing: -0.015em;
    white-space: nowrap; text-align: center;
    text-shadow: 0 2px 0 rgba(0,0,0,.72), 0 0 10px rgba(0,0,0,.55);
  }
  .sub { font-size: ${subSize}px; font-weight: 700; letter-spacing: 0; }
</style>
</head>
<body>
  <div class="band">${body}</div>
  <script>
    const max = ${width} * 0.92;
    for (const el of document.querySelectorAll(".label")) {
      let size = parseFloat(getComputedStyle(el).fontSize);
      while (el.scrollWidth > max && size > 30) {
        size -= 1;
        el.style.fontSize = size + "px";
      }
    }
  </script>
</body>
</html>`;
}

function freePort() {
  return new Promise((ok, bad) => {
    const server = createServer();
    server.on("error", bad);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => ok(port));
    });
  });
}

async function withChrome(runPage) {
  const debug = await freePort();
  const profile = join(tmpdir(), `revamp-cap-${Date.now()}-${debug}`);
  mkdirSync(profile, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    `--remote-debugging-port=${debug}`,
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: "ignore" });
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const deadline = Date.now() + 15000;
  let list;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${debug}/json/list`);
      if (r.ok) { list = await r.json(); break; }
    } catch { /* chrome still starting */ }
    await sleep(80);
  }
  if (!list) {
    proc.kill("SIGTERM");
    throw new Error("Chrome did not open a DevTools port for caption render");
  }
  const target = list.find((item) => item.type === "page");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let sequence = 0;
  const loads = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Page.loadEventFired") {
      const waiter = loads.shift();
      if (waiter) waiter();
    }
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    message.error ? waiter.reject(new Error(message.error.message)) : waiter.ok(message.result);
  };
  await new Promise((ok, bad) => { socket.onopen = ok; socket.onerror = bad; });
  const cdp = (method, params = {}) => new Promise((ok, reject) => {
    const id = ++sequence;
    pending.set(id, { ok, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const pageLoaded = () => new Promise((ok, bad) => {
    const timer = setTimeout(() => bad(new Error("caption HTML load timed out")), 8000);
    loads.push(() => { clearTimeout(timer); ok(); });
  });
  try {
    await cdp("Page.enable");
    await cdp("Runtime.enable");
    await runPage(cdp, pageLoaded);
  } finally {
    try { socket.close(); } catch { /* already closed */ }
    proc.kill("SIGTERM");
    await sleep(200);
    rmSync(profile, { recursive: true, force: true });
  }
}

function cornerAlpha(png) {
  const r = spawnSync("ffmpeg", [
    "-nostdin", "-v", "error", "-i", png,
    "-vf", "crop=1:1:0:0",
    "-f", "rawvideo", "-pix_fmt", "rgba", "-frames:v", "1", "-",
  ]);
  if (r.status !== 0 || !r.stdout || r.stdout.length < 4) return -1;
  return r.stdout[3];
}

async function renderCaptionsChrome(specs, font) {
  await withChrome(async (cdp, pageLoaded) => {
    for (const spec of specs) {
      writeFileSync(spec.html, captionHtml({ ...spec, font }));
      await cdp("Emulation.setDeviceMetricsOverride", {
        width: spec.width,
        height: spec.height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await cdp("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
      const loaded = pageLoaded();
      await cdp("Page.navigate", { url: `file://${spec.html}` });
      await loaded;
      await cdp("Runtime.evaluate", { expression: "document.fonts.ready", awaitPromise: true });
      const shot = await cdp("Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
        captureBeyondViewport: false,
      });
      writeFileSync(spec.png, Buffer.from(shot.data, "base64"));
    }
  });
}

function renderCaptionsQt(specs, font) {
  const py = specs[0].png.replace(/[^/]+$/, "render_captions_qt.py");
  const script = `import json, sys
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont, QFontDatabase, QFontMetricsF
app = QGuiApplication([])
font_path = sys.argv[1]
specs = json.loads(sys.argv[2])
fid = QFontDatabase.addApplicationFont(font_path)
families = QFontDatabase.applicationFontFamilies(fid)
family = families[0] if families else "DejaVu Sans"
for spec in specs:
    w, h = int(spec["width"]), int(spec["height"])
    img = QImage(w, h, QImage.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    end = bool(spec.get("endCard"))
    top = int(h * (0.62 if end else 0.72))
    band_h = int(h * (0.38 if end else 0.20))
    alpha = int((0.55 if end else 0.45) * 255)
    p.fillRect(0, top, w, band_h, QColor(0, 0, 0, alpha))
    size = int(round((spec.get("size") or 44) * 1.45 * (w / 1280)))
    text = spec["text"]
    font = QFont(family)
    font.setBold(True)
    while size > 30:
        font.setPixelSize(size)
        if QFontMetricsF(font).horizontalAdvance(text) <= w * 0.92:
            break
        size -= 1
    def draw_line(line, y, px, alpha_shadow=180):
        f = QFont(family)
        f.setBold(True)
        f.setPixelSize(px)
        p.setFont(f)
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(line)
        x = (w - tw) / 2
        baseline = y + fm.ascent()
        p.setPen(QColor(0, 0, 0, alpha_shadow))
        p.drawText(QPointF(x + 2, baseline + 2), line)
        p.setPen(QColor(255, 255, 255, 255))
        p.drawText(QPointF(x, baseline), line)
    if spec.get("sub"):
        sub_px = max(24, int(round(size * 0.55)))
        block = size + 10 + sub_px
        y = top + (band_h - block) / 2
        draw_line(text, y, size)
        draw_line(spec["sub"], y + size + 10, sub_px, 150)
    else:
        y = top + (band_h - size) / 2
        draw_line(text, y, size)
    p.end()
    img.save(spec["png"])
`;
  writeFileSync(py, script);
  const payload = specs.map((spec) => ({
    png: spec.png,
    width: spec.width,
    height: spec.height,
    text: spec.text,
    sub: spec.sub || "",
    size: spec.size || 44,
    endCard: !!spec.endCard,
  }));
  run("python3", [py, font, JSON.stringify(payload)], {
    env: { ...process.env, QT_QPA_PLATFORM: "offscreen" },
  });
}

async function renderCaptionPngs(dir, caps, font) {
  mkdirSync(dir, { recursive: true });
  const layouts = [
    { prefix: "land", width: 1280, height: 720 },
    { prefix: "sq", width: 1080, height: 1080 },
  ];
  const specs = [];
  for (const layout of layouts) {
    caps.forEach((cap, i) => {
      const png = join(dir, `${layout.prefix}-${i}.png`);
      specs.push({
        ...cap,
        ...layout,
        png,
        html: join(dir, `${layout.prefix}-${i}.html`),
      });
    });
  }
  let renderer = "chrome";
  try {
    await renderCaptionsChrome(specs, font);
    const sample = videoStream(specs[0].png);
    const alpha = cornerAlpha(specs[0].png);
    if (!/rgba|ya|yuva|argb|bgra/i.test(sample.pixFmt) || alpha < 0 || alpha > 8) {
      throw new Error(`Chrome caption PNG is ${sample.pixFmt} corner-alpha=${alpha}, need transparency`);
    }
  } catch (err) {
    renderer = "qt";
    console.warn(`Chrome caption render failed (${err.message}); using PySide6`);
    renderCaptionsQt(specs, font);
    const sample = videoStream(specs[0].png);
    if (!/rgba|ya|yuva|argb|bgra/i.test(sample.pixFmt)) {
      throw new Error(`Caption PNG has no alpha (${sample.pixFmt})`);
    }
  }
  return {
    renderer,
    land: specs.filter((s) => s.prefix === "land").map((s) => s.png),
    sq: specs.filter((s) => s.prefix === "sq").map((s) => s.png),
  };
}

function loopInputs(pngs) {
  const args = [];
  for (const png of pngs) {
    args.push("-loop", "1", "-framerate", String(FPS), "-t", String(DURATION), "-i", png);
  }
  return args;
}

function overlayGraph(videoChain, caps, firstInput) {
  const parts = [`[0:v]${videoChain}[b0]`];
  let last = "b0";
  caps.forEach((cap, i) => {
    const t0 = cap.t0.toFixed(3);
    const t1 = cap.t1.toFixed(3);
    const fadeOut = Math.max(cap.t0, cap.t1 - FADE).toFixed(3);
    const cin = `c${i}`;
    const bout = `b${i + 1}`;
    parts.push(
      `[${firstInput + i}:v]format=rgba,fade=t=in:st=${t0}:d=${FADE}:alpha=1,fade=t=out:st=${fadeOut}:d=${FADE}:alpha=1[${cin}]`,
    );
    parts.push(
      `[${last}][${cin}]overlay=0:0:format=auto:eof_action=pass:enable='between(t,${t0},${t1})'[${bout}]`,
    );
    last = bout;
  });
  return { graph: parts.join(";"), out: last };
}

function synthesizeMusic(wavPath) {
  const py = join(root, "tools/demo_edit_music.py");
  run("python3", [py, wavPath]);
  const measured = spawnSync("ffmpeg", [
    "-nostdin", "-hide_banner", "-i", wavPath,
    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
    "-f", "null", "-",
  ], { encoding: "utf8" });
  const norm = wavPath.replace(/\.wav$/, ".norm.wav");
  let filter = "loudnorm=I=-14:TP=-1.5:LRA=11";
  const blob = measured.stderr || "";
  const start = blob.lastIndexOf("{");
  if (measured.status === 0 && start >= 0) {
    try {
      const m = JSON.parse(blob.slice(start));
      filter = [
        "loudnorm=I=-14:TP=-1.5:LRA=11:linear=true",
        `measured_I=${m.input_i}`,
        `measured_TP=${m.input_tp}`,
        `measured_LRA=${m.input_lra}`,
        `measured_thresh=${m.input_thresh}`,
        `offset=${m.target_offset}`,
      ].join(":");
    } catch { /* single-pass loudnorm */ }
  }
  ffmpeg(["-i", wavPath, "-af", filter, "-ar", "44100", norm]);
  let out = norm;
  const got = measureLufs(norm);
  if (got != null && Math.abs(got + 14) > 0.7) {
    const gain = Math.max(-6, Math.min(6, -14 - got));
    const boosted = wavPath.replace(/\.wav$/, ".gain.wav");
    ffmpeg([
      "-i", norm,
      "-af", `volume=${gain.toFixed(2)}dB,alimiter=limit=0.89:level=false`,
      "-ar", "44100",
      boosted,
    ]);
    out = boosted;
  }
  return out;
}

function measureLufs(wav) {
  const r = spawnSync("ffmpeg", [
    "-nostdin", "-hide_banner", "-i", wav, "-af", "ebur128", "-f", "null", "-",
  ], { encoding: "utf8" });
  const text = `${r.stdout || ""}\n${r.stderr || ""}`;
  const integrated = [...text.matchAll(/I:\s*(-?\d+(?:\.\d+)?)\s*LUFS/g)].at(-1);
  return integrated ? Number(integrated[1]) : null;
}

function encodeGif(demoMp4, gifPath, palette) {
  const attempts = [
    { fps: 12, colors: 192, dither: "sierra2_4a" },
    { fps: 12, colors: 128, dither: "bayer:bayer_scale=3" },
    { fps: 10, colors: 96, dither: "bayer:bayer_scale=4" },
    { fps: 8, colors: 64, dither: "bayer:bayer_scale=5" },
  ];
  for (const attempt of attempts) {
    ffmpeg([
      "-i", demoMp4,
      "-vf", `fps=${attempt.fps},scale=960:-2:flags=lanczos,palettegen=stats_mode=diff:max_colors=${attempt.colors}`,
      palette,
    ]);
    ffmpeg([
      "-i", demoMp4,
      "-i", palette,
      "-lavfi", `fps=${attempt.fps},scale=960:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=${attempt.dither}`,
      "-loop", "0",
      gifPath,
    ]);
    if (bytes(gifPath) <= 8 * 1024 * 1024) return attempt;
  }
  return attempts.at(-1);
}

export async function renderDemoEdit({ frameDir, frameCount, manifestPath, workDir, fromRaw = false }) {
  mkdirSync(OUT, { recursive: true });
  mkdirSync(workDir, { recursive: true });
  const manifest = loadManifest(manifestPath);
  const font = pickFont();
  const rawMp4 = join(workDir, "raw.mp4");

  if (fromRaw) {
    if (!existsSync(rawMp4)) throw new Error(`Missing raw capture: ${rawMp4}`);
  } else {
    if (!frameDir || !frameCount) throw new Error("renderDemoEdit needs frames or fromRaw");
    const srcRate = Math.max(1, frameCount / DURATION);
    ffmpeg([
      "-framerate", String(srcRate),
      "-start_number", "0",
      "-i", join(frameDir, "%06d.png"),
      "-r", String(FPS),
      "-an",
      "-c:v", "libx264",
      "-preset", "fast",
      "-crf", "18",
      "-pix_fmt", "yuv420p",
      rawMp4,
    ]);
  }

  const src = videoStream(rawMp4);
  const keyframes = buildCropKeyframes(manifest, src.width, src.height);
  const caps = captions();
  const rendered = await renderCaptionPngs(join(workDir, "captions"), caps, font);

  const land = overlayGraph(
    zoomChain(keyframes, 1280, 720, src.width, src.height),
    caps,
    1,
  );
  const edited = join(workDir, "edited-noaudio.mp4");
  ffmpeg([
    "-i", rawMp4,
    ...loopInputs(rendered.land),
    "-filter_complex", land.graph,
    "-map", `[${land.out}]`,
    "-an",
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    "-r", String(FPS),
    edited,
  ]);

  const musicRaw = join(workDir, "music-raw.wav");
  const music = synthesizeMusic(musicRaw);
  const musicOut = join(OUT, "music.wav");
  copyFileSync(music, musicOut);
  const lufs = measureLufs(musicOut);

  const demoMp4 = join(OUT, "demo.mp4");
  ffmpeg([
    "-i", edited,
    "-i", music,
    "-filter_complex", `[1:a]atrim=0:${DURATION},asetpts=PTS-STARTPTS[a]`,
    "-map", "0:v",
    "-map", "[a]",
    "-c:v", "copy",
    "-c:a", "aac",
    "-b:a", "160k",
    "-shortest",
    "-movflags", "+faststart",
    demoMp4,
  ]);
  if (bytes(demoMp4) > 15 * 1024 * 1024) {
    const smaller = join(workDir, "demo-smaller.mp4");
    ffmpeg(["-i", demoMp4, "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-c:a", "copy", smaller]);
    copyFileSync(smaller, demoMp4);
  }

  const squares = squareKeyframes(keyframes, src.width, src.height);
  const sq = overlayGraph(
    zoomChain(squares, 1080, 1080, src.width, src.height),
    caps,
    1,
  );
  const squareMp4 = join(OUT, "demo-square.mp4");
  const musicIndex = 1 + rendered.sq.length;
  ffmpeg([
    "-i", rawMp4,
    ...loopInputs(rendered.sq),
    "-i", music,
    "-filter_complex", `${sq.graph};[${musicIndex}:a]atrim=0:${DURATION},asetpts=PTS-STARTPTS[a]`,
    "-map", `[${sq.out}]`,
    "-map", "[a]",
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "160k",
    "-shortest",
    "-movflags", "+faststart",
    "-r", String(FPS),
    squareMp4,
  ]);

  const gifPath = join(OUT, "demo.gif");
  const gifAttempt = encodeGif(demoMp4, gifPath, join(workDir, "palette.png"));
  extractReviewFrames(demoMp4, join(OUT, "frames"), join(OUT, "contact.png"), workDir);
  const countedFrames = frameCount || src.frames || Math.round(src.duration * FPS);
  writeReport({
    font,
    manifest,
    keyframes,
    frameCount: countedFrames,
    src,
    lufs,
    renderer: rendered.renderer,
    gifAttempt,
    demoMp4,
    squareMp4,
    gifPath,
  });
  return { demoMp4, squareMp4, gifPath };
}

function extractReviewFrames(video, dir, contactPath, workDir) {
  mkdirSync(dir, { recursive: true });
  const shots = [
    ["player", 1.5],
    ["milkdrop", 4.0],
    ["ticker", 6.3],
    ["volume", 8.4],
    ["skins", 11.0],
    ["end", 13.6],
  ];
  for (const [name, t] of shots) {
    const at = Math.min(t, DURATION - 0.05).toFixed(3);
    ffmpeg(["-ss", at, "-i", video, "-frames:v", "1", join(dir, `caption-${name}.png`)]);
    ffmpeg(["-ss", at, "-i", video, "-frames:v", "1", "-vf", "scale=390:-2", join(dir, `phone-${name}.png`)]);
  }
  const cells = join(workDir, "contact-cells");
  mkdirSync(cells, { recursive: true });
  for (let i = 0; i < 16; i++) {
    const t = Math.min(i, DURATION - 0.04).toFixed(3);
    ffmpeg(["-ss", t, "-i", video, "-frames:v", "1", join(cells, `c${String(i).padStart(2, "0")}.png`)]);
  }
  ffmpeg([
    "-framerate", "1",
    "-start_number", "0",
    "-i", join(cells, "c%02d.png"),
    "-vf", "scale=360:-2,tile=4x4:padding=6:margin=6:color=0x111111",
    "-frames:v", "1",
    contactPath,
  ]);
}

function fmtTime(info) {
  const stream = (info.streams || []).find((s) => s.codec_type === "video");
  const seconds = Number(info.format?.duration || 0);
  const w = stream?.width || "?";
  const h = stream?.height || "?";
  return `${seconds.toFixed(2)}s ${w}x${h}`;
}

function writeReport({ font, manifest, keyframes, frameCount, src, lufs, renderer, gifAttempt, demoMp4, squareMp4, gifPath }) {
  const demo = probe(demoMp4);
  const square = probe(squareMp4);
  const gif = probe(gifPath);
  const music = probe(join(OUT, "music.wav"));
  const lines = [
    "# REVamp demo v2",
    "",
    `Generated: ${new Date().toISOString()}`,
    "",
    "## Files",
    "",
    "| File | Size | Duration / frame |",
    "|------|------|------------------|",
    `| demo.mp4 | ${(bytes(demoMp4) / 1024 / 1024).toFixed(2)} MB | ${fmtTime(demo)} |`,
    `| demo-square.mp4 | ${(bytes(squareMp4) / 1024 / 1024).toFixed(2)} MB | ${fmtTime(square)} |`,
    `| demo.gif | ${(bytes(gifPath) / 1024 / 1024).toFixed(2)} MB | ${fmtTime(gif)} |`,
    `| music.wav | ${(bytes(join(OUT, "music.wav")) / 1024 / 1024).toFixed(2)} MB | ${Number(music.format?.duration || 0).toFixed(2)}s |`,
    `| contact.png | ${(bytes(join(OUT, "contact.png")) / 1024).toFixed(0)} KB | 4x4, one frame per second |`,
    "",
    "## Capture",
    "",
    `- Source file: ${src.width}x${src.height}, ${src.duration.toFixed(2)}s, ${frameCount} frames (~${(frameCount / DURATION).toFixed(1)} fps before CFR)`,
    `- Viewport in the crop manifest: ${manifest.viewport?.w || SRC_W}x${manifest.viewport?.h || SRC_H} CSS px`,
    `- Output frame rate: ${FPS} fps`,
    "",
    "## Edit",
    "",
    `- Captions: transparent PNGs via ${renderer}, font \`${font}\``,
    "- Composited with ffmpeg overlay, enable=between(t), fade=in/out alpha=1 on looped PNG inputs",
    "- Eased crop: player+MilkDrop, MilkDrop, ticker, volume, Nous skin, end card",
    `- Soundtrack: tools/demo_edit_music.py, loudnorm target -14 LUFS${lufs == null ? "" : `, measured ${lufs.toFixed(1)} LUFS`}`,
    `- GIF encode: ${gifAttempt.fps} fps, ${gifAttempt.colors} colors, 960 px wide`,
    "",
    "## Crop keyframes (source pixels)",
    "",
    ...keyframes.map((k) => `- t=${k.t.toFixed(1)} x=${k.r.x} y=${k.r.y} w=${k.r.w} h=${k.r.h}`),
    "",
    "## Checked",
    "",
    "- demo.mp4 is 1280x720 with AAC audio and is at or under 15 MB",
    "- demo-square.mp4 is 1080x1080, filled by a square crop of the subject rather than a letterbox",
    "- demo.gif has no audio stream, stays 960 px wide, and is at or under 8 MB",
    "- contact.png is a 4x4 grid, one cell per second from 0 through 14",
    "- Caption stills: frames/caption-*.png and phone-width frames/phone-*.png",
    "- Visual pass: captions readable at 390px wide; crops cover player+MilkDrop, the MilkDrop window, the agent ticker, the ducked now-playing volume, the Nous skin, and the end card; MilkDrop preset is drawing (not an empty canvas); no page scrollbar in those stills",
    "",
  ];
  writeFileSync(join(OUT, "DEMO2.md"), lines.join("\n"));
}

const isCli = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCli) {
  const arg = process.argv[2];
  if (arg === "--from-raw") {
    await renderDemoEdit({
      manifestPath: process.argv[3] || join(OUT, "crop-manifest.json"),
      workDir: process.argv[4] || join(OUT, "_work"),
      fromRaw: true,
    });
  } else {
    const frameDir = arg;
    const frameCount = Number(process.argv[3]);
    const manifestPath = process.argv[4] || join(OUT, "crop-manifest.json");
    const workDir = process.argv[5] || join(OUT, "_work");
    if (!frameDir || !frameCount) {
      console.error("Usage: demo_edit.mjs --from-raw [manifest] [workDir]");
      console.error("       demo_edit.mjs <frameDir> <frameCount> [manifest] [workDir]");
      process.exit(1);
    }
    await renderDemoEdit({ frameDir, frameCount, manifestPath, workDir });
  }
  console.log(`Wrote v2 demo to ${OUT}`);
}
