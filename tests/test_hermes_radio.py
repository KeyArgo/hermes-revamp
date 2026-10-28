"""Hermes Radio: the desktop plugin's lineup, and a render of the hero.

The lineup is Hermes Desktop's bundled radio plugin (read-only). The render
smoke drives web/views/radio.js under Node with a small DOM — the same shape
as the other Node checks in this suite — and asserts the hero, the other
tabs, and that nothing plays until a play button is pressed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from revamp_core import radio  # noqa: E402

PLUGIN = Path(os.environ.get("HERMES_RADIO_PLUGIN", str(
    Path.home() / ".hermes" / "hermes-agent" / "apps" / "desktop" / "src" / "plugins" / "radio" / "plugin.js"
)))
RADIO_JS = REPO / "web" / "views" / "radio.js"
TMP_DIR = Path(os.environ.get("TMPDIR", str(Path.home() / ".cache" / "revamp-gate"))) / "hermesradio-screenshots"
SHOT_DIR = TMP_DIR / "shots"
# Chrome's Unix-domain singleton socket is limited to 108 bytes. Keep the
# alias beside the disk-backed temp root, not under a potentially long checkout.
RUNTIME_ALIAS = TMP_DIR.parent / ".rr-runtime"

# id, name, description, provider, url, homepage — the plugin's PRESETS, resolved.
EXPECTED = [
    ("chillsynth", "Chillsynth", "Soft focus · warm synths", "Nightride FM",
     "https://stream.nightride.fm/chillsynth.mp3", "https://nightride.fm/?station=chillsynth"),
    ("nightride", "Nightride", "Synthwave · after hours", "Nightride FM",
     "https://stream.nightride.fm/nightride.mp3", "https://nightride.fm/"),
    ("darksynth", "Darksynth", "Dark electronics · high energy", "Nightride FM",
     "https://stream.nightride.fm/darksynth.mp3", "https://nightride.fm/?station=darksynth"),
    ("spacesynth", "Spacesynth", "Cosmic synths · retro futures", "Nightride FM",
     "https://stream.nightride.fm/spacesynth.mp3", "https://nightride.fm/?station=spacesynth"),
    ("paradise-main", "Main Mix", "Eclectic · human selected", "Radio Paradise",
     "https://stream.radioparadise.com/aac-128", "https://radioparadise.com/"),
    ("paradise-mellow", "Mellow Mix", "A gentler pace", "Radio Paradise",
     "https://stream.radioparadise.com/mellow-flac", "https://radioparadise.com/"),
    ("eve-radio", "EVE Radio", "GamingNow · EVE community radio", "GamingNow",
     "https://media01.gamingnow.net:8010/erweb.mp3", "https://gamingnow.net/eve-radio/"),
]
ASK = "Play something on Hermes Radio that fits what I'm doing"


def _row(station: dict) -> tuple:
    return (station["id"], station["name"], station["description"], station["provider"],
            station["url"], station["homepage"])


def _plugin_presets() -> list[tuple]:
    text = PLUGIN.read_text(encoding="utf-8")
    base = re.search(r"const NIGHTRIDE = '([^']+)'", text)
    block = re.search(r"const PRESETS = \[([\s\S]*?)\n\]", text)
    assert base and block, "Hermes radio plugin has no PRESETS"
    rows = []
    pat = re.compile(
        r"\{ id: '([^']+)', name: '([^']+)', description: '([^']+)', "
        r"provider: '([^']+)', url: ([^,]+), homepage: '([^']+)' \}"
    )
    for match in pat.finditer(block.group(1)):
        url = match.group(5).strip()
        if url.startswith("`"):
            url = url.strip("`").replace("${NIGHTRIDE}", base.group(1))
        else:
            url = url.strip("'\"")
        rows.append((match.group(1), match.group(2), match.group(3), match.group(4), url, match.group(6)))
    return rows


def test_hermes_lineup_matches_the_desktop_plugin():
    assert [_row(s) for s in radio.HERMES_LINEUP] == EXPECTED
    assert [_row(s) for s in radio.hermes_lineup()] == EXPECTED
    assert all(s["url"].startswith("https://") for s in radio.HERMES_LINEUP)
    assert all(s["source"] == "hermes" for s in radio.HERMES_LINEUP)
    # Hermes FM is a placeholder in the page, not a stream we can hand to the player.
    assert "hermes-fm" not in {s["id"] for s in radio.HERMES_LINEUP}
    assert "hermes-fm" not in {s["id"] for s in radio.PRESETS}
    if PLUGIN.exists():
        assert _plugin_presets() == EXPECTED


def test_hermes_lineup_is_its_own_list():
    """The SomaFM fallback presets stay the Radio Browser outage list."""
    ids = [s["id"] for s in radio.PRESETS]
    assert "spacesynth" not in ids and "eve-radio" not in ids and "paradise-main" not in ids
    assert len(radio.presets()) == len(radio.PRESETS)


def test_hermes_lineup_endpoint(server):
    status, _, body = server.client.get("/api/radio/hermes")
    assert status == 200
    assert [_row(s) for s in body] == EXPECTED
    assert all("url" in s and s["url"] for s in body)


def test_tool_lists_and_plays_a_hermes_station(server):
    import importlib
    import __init__ as plugin_root
    importlib.reload(plugin_root)
    listed = json.loads(plugin_root._tool_radio({"action": "hermes"}))
    assert listed["ok"] is True
    assert [s["id"] for s in listed["stations"]] == [row[0] for row in EXPECTED]
    assert "not on the air" in listed["coming_soon"]
    played = json.loads(plugin_root._tool_radio({"action": "play", "station_id": "spacesynth"}))
    assert played["ok"] is True and played["station"] == "Spacesynth"
    # A preset id still resolves to the built-in preset, not the hero.
    preset = json.loads(plugin_root._tool_radio({"action": "play", "station_id": "somafm-groovesalad"}))
    assert preset["ok"] is True and preset["station"] == "SomaFM Groove Salad"


def test_radio_page_defaults_to_hermes_and_does_not_autoplay():
    text = RADIO_JS.read_text(encoding="utf-8")
    assert 'let tab = "hermes"' in text
    assert 'data-t="hermes" class="active"' in text
    assert "loadHermes();" in text
    assert "autoplay" not in text
    assert ASK in text
    assert "Hermes FM — coming soon" in text
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    assert app.index('id: "hermes-radio"') < app.index('id: "library"')


NODE_SMOKE = r"""
const fs = require('fs');
const { pathToFileURL } = require('url');
const stations = JSON.parse(fs.readFileSync(0, 'utf8'));
const ASK = process.argv[2];

const listeners = new Map();
function on(map, type, fn) {
  if (!map.has(type)) map.set(type, []);
  map.get(type).push(fn);
}
function matches(el, sel) {
  if (!el || el.nodeType !== 1) return false;
  let rest = sel.trim();
  const tag = rest.match(/^[a-zA-Z0-9-]+/);
  if (tag) {
    if (el.tagName !== tag[0].toUpperCase()) return false;
    rest = rest.slice(tag[0].length);
  }
  while (rest) {
    if (rest[0] === '.') {
      const m = rest.match(/^\.([a-zA-Z0-9_-]+)/);
      if (!m || !el.classList.contains(m[1])) return false;
      rest = rest.slice(m[0].length);
    } else if (rest[0] === '#') {
      const m = rest.match(/^#([a-zA-Z0-9_-]+)/);
      if (!m || el.id !== m[1]) return false;
      rest = rest.slice(m[0].length);
    } else if (rest[0] === '[') {
      const m = rest.match(/^\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]/);
      if (!m) return false;
      const val = el.getAttribute(m[1]);
      if (val === null) return false;
      if (m[2] !== undefined && val !== m[2]) return false;
      rest = rest.slice(m[0].length);
    } else return false;
  }
  return true;
}
function walk(node, fn) {
  for (const child of node.childNodes || []) {
    if (child.nodeType === 1) { fn(child); walk(child, fn); }
  }
}
function queryAll(root, selector) {
  const steps = selector.trim().split(/\s+/);
  let current = [root];
  for (const step of steps) {
    const next = [];
    for (const node of current) {
      walk(node, (el) => { if (matches(el, step)) next.push(el); });
    }
    current = next;
  }
  return current;
}
function parseHTML(html, parent) {
  parent.childNodes = [];
  const re = /<([a-zA-Z0-9]+)([^>]*)>|<\/([a-zA-Z0-9]+)>|([^<]+)/g;
  const stack = [parent];
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) {
      const el = createElement(m[1]);
      const attrs = m[2] || '';
      const selfClose = /\/\s*$/.test(attrs) || ['input', 'br', 'img'].includes(m[1].toLowerCase());
      const attrRe = /([:@a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"/g;
      let a;
      while ((a = attrRe.exec(attrs))) el.setAttribute(a[1], a[2]);
      stack[stack.length - 1].appendChild(el);
      if (!selfClose) stack.push(el);
    } else if (m[3]) {
      if (stack.length > 1) stack.pop();
    } else if (m[4] && m[4].trim()) {
      stack[stack.length - 1].appendChild(createText(m[4]));
    }
  }
}
function createText(value) {
  return { nodeType: 3, nodeValue: String(value), textContent: String(value), parentNode: null, childNodes: [] };
}
function createElement(tag) {
  const el = {
    nodeType: 1,
    tagName: String(tag).toUpperCase(),
    childNodes: [],
    parentNode: null,
    id: '',
    _attrs: new Map(),
    _classes: new Set(),
    _className: '',
    _listeners: new Map(),
    _hidden: false,
    _value: '',
    _tabIndex: null,
    style: {},
  };
  el.classList = {
    contains: (name) => el._classes.has(name),
    add: (...names) => { names.forEach((n) => el._classes.add(n)); el._className = [...el._classes].join(' '); el._attrs.set('class', el._className); },
    remove: (...names) => { names.forEach((n) => el._classes.delete(n)); el._className = [...el._classes].join(' '); el._attrs.set('class', el._className); },
    toggle: (name, force) => {
      const on = force === undefined ? !el._classes.has(name) : !!force;
      if (on) el.classList.add(name); else el.classList.remove(name);
      return on;
    },
  };
  Object.defineProperty(el, 'className', {
    get: () => el._className,
    set: (v) => {
      el._className = String(v || '');
      el._classes = new Set(el._className.split(/\s+/).filter(Boolean));
      el._attrs.set('class', el._className);
    },
  });
  Object.defineProperty(el, 'textContent', {
    get: () => el.childNodes.map((n) => n.textContent || '').join(''),
    set: (v) => { el.childNodes = v === '' || v == null ? [] : [createText(v)]; },
  });
  Object.defineProperty(el, 'innerHTML', {
    get: () => '',
    set: (v) => { if (!v) el.childNodes = []; else parseHTML(String(v), el); },
  });
  Object.defineProperty(el, 'hidden', {
    get: () => el._hidden,
    set: (v) => {
      el._hidden = !!v;
      if (el._hidden) el._attrs.set('hidden', '');
      else el._attrs.delete('hidden');
    },
  });
  Object.defineProperty(el, 'value', {
    get: () => el._value || '',
    set: (v) => { el._value = String(v); },
  });
  Object.defineProperty(el, 'tabIndex', {
    get: () => el._tabIndex == null ? (el.tagName === 'BUTTON' ? 0 : -1) : el._tabIndex,
    set: (v) => { el._tabIndex = Number(v); },
  });
  Object.defineProperty(el, 'dataset', {
    get: () => new Proxy({}, {
      get: (_t, prop) => {
        if (typeof prop !== 'string') return undefined;
        const attr = 'data-' + prop.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
        const val = el.getAttribute(attr);
        return val === null ? undefined : val;
      },
      set: (_t, prop, value) => {
        const attr = 'data-' + prop.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
        el.setAttribute(attr, value);
        return true;
      },
    }),
  });
  el.setAttribute = (name, value) => {
    if (name === 'class') { el.className = value; return; }
    el._attrs.set(name, String(value));
    if (name === 'id') el.id = String(value);
  };
  el.getAttribute = (name) => {
    if (name === 'class') return el._className;
    return el._attrs.has(name) ? el._attrs.get(name) : null;
  };
  el.appendChild = (child) => {
    child.parentNode = el;
    el.childNodes.push(child);
    return child;
  };
  el.append = (...nodes) => nodes.forEach((n) => el.appendChild(n));
  el.replaceChildren = (...nodes) => { el.childNodes = []; el.append(...nodes); };
  el.remove = () => {
    const p = el.parentNode;
    if (!p) return;
    const i = p.childNodes.indexOf(el);
    if (i >= 0) p.childNodes.splice(i, 1);
    el.parentNode = null;
  };
  el.addEventListener = (type, fn) => on(el._listeners, type, fn);
  el.removeEventListener = () => {};
  el.querySelectorAll = (sel) => queryAll(el, sel);
  el.querySelector = (sel) => queryAll(el, sel)[0] || null;
  el.focus = () => { global.document.activeElement = el; };
  el.select = () => {};
  el.click = () => {
    const ev = { target: el, currentTarget: el, preventDefault() {}, stopPropagation() {} };
    for (const fn of el._listeners.get('click') || []) fn(ev);
  };
  Object.defineProperty(el, 'isConnected', {
    get: () => {
      let n = el;
      while (n) {
        if (n === global.document || n === global.document.body) return true;
        n = n.parentNode;
      }
      return false;
    },
  });
  return el;
}

const document = createElement('#document');
document.body = createElement('body');
document.body.parentNode = document;
document.appendChild = (child) => { child.parentNode = document; return child; };
document.childNodes = [document.body];
document.addEventListener = (type, fn) => on(listeners, type, fn);
document.removeEventListener = () => {};
document.createElement = createElement;
document.createTextNode = createText;
document.querySelector = (sel) => queryAll(document.body, sel)[0] || null;
document.querySelectorAll = (sel) => queryAll(document.body, sel);
document.activeElement = null;
document.execCommand = () => false;
global.document = document;
global.window = global;
// Node's navigator is a getter with no setter, so `global.navigator = ...` is a no-op.
Object.defineProperty(global, "navigator", {
  configurable: true,
  enumerable: true,
  writable: true,
  value: { clipboard: { writeText: async (text) => { global.__copied = text; } } },
});
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
global.requestAnimationFrame = (fn) => { fn(); return 1; };
const _st = global.setTimeout;
const _si = global.setInterval;
global.setTimeout = (fn, ms, ...a) => { const id = _st(fn, ms, ...a); if (id && id.unref) id.unref(); return id; };
global.setInterval = (fn, ms, ...a) => { const id = _si(fn, ms, ...a); if (id && id.unref) id.unref(); return id; };
global.addEventListener = (type, fn) => on(listeners, type, fn);
global.removeEventListener = () => {};

const calls = [];
const state = { url: null, status: 'STOPPED', plays: 0, pauses: 0, items: [] };
global.revampPlayerApi = {
  get items() { return state.items; },
  webamp: { store: { getState: () => ({ media: { status: state.status }, playlist: { currentTrack: state.url ? 0 : null, trackOrder: state.url ? [0] : [] } }) } },
  playItemsAt(index, list) {
    state.plays += 1;
    state.url = list[index].url;
    state.status = 'PLAYING';
    state.items = list.map((row) => ({ kind: 'stream', playback: { stream: row.url }, title: row.title }));
  },
  pause() { state.pauses += 1; state.status = 'PAUSED'; },
  play() { state.status = 'PLAYING'; },
};
window.revampPlayerApi = global.revampPlayerApi;

(async () => {
  const mod = await import(pathToFileURL(process.argv[1]).href);
  const root = document.createElement('div');
  document.body.appendChild(root);
  const api = async (path) => {
    calls.push(String(path));
    if (String(path).startsWith('/api/radio/hermes')) return stations;
    if (String(path).startsWith('/api/radio/favorites')) return [];
    if (String(path).includes('nowplaying')) return { title: 'Synthetic Calm' };
    return [];
  };
  await mod.render(root, api);
  const fail = (msg) => { throw new Error(msg + '\n' + root.textContent.slice(0, 500)); };
  if (!root.querySelector('#hermes-radio-heading')) fail('missing heading');
  if (root.querySelector('.tabs button.active')?.getAttribute('data-t') !== 'hermes' &&
      root.querySelector('.tabs button.active')?.dataset.t !== 'hermes') fail('default tab is not hermes');
  const active = root.querySelector('.tabs button.active');
  if (active.getAttribute('data-t') !== 'hermes') fail('active tab ' + active.getAttribute('data-t'));
  for (const s of stations) {
    if (!root.textContent.includes(s.name)) fail('missing name ' + s.name);
    if (!root.textContent.includes(s.provider)) fail('missing provider ' + s.provider);
    if (!root.textContent.includes(s.description)) fail('missing description ' + s.description);
  }
  const plays = root.querySelectorAll('.hermes-play');
  if (plays.length !== stations.length) fail('play buttons ' + plays.length);
  for (const btn of plays) {
    if (btn.tagName !== 'BUTTON' || btn.tabIndex < 0) fail('play control is not keyboard reachable');
    if (!btn.getAttribute('aria-label').startsWith('Play ')) fail('play label ' + btn.getAttribute('aria-label'));
  }
  const soon = root.querySelector('.hermes-soon');
  if (!soon) fail('missing coming soon');
  if (!soon.textContent.includes('Hermes FM — coming soon')) fail('soon title');
  if (!soon.textContent.includes('DJs between your library and live radio')) fail('soon idea');
  if (soon.querySelector('button') || soon.querySelector('audio') || soon.querySelector('a')) fail('coming soon has a control');
  if (/https?:/i.test(soon.textContent)) fail('coming soon looks like a stream');
  if (root.querySelector('audio')) fail('audio element on first paint');
  if (state.plays !== 0 || calls.some((p) => p.includes('nowplaying') || p.includes('/stream'))) fail('autoplay ' + JSON.stringify(calls));
  const search = root.querySelector('input');
  if (!search) fail('search field missing');
  plays[0].focus();
  if (document.activeElement !== plays[0]) fail('focus did not land on play');
  plays[0].click();
  await new Promise((r) => _st(r, 30));
  if (state.plays !== 1 || state.url !== stations[0].url) fail('play did not start the first station');
  if (plays[0].getAttribute('aria-pressed') !== 'true') fail('pause state not shown');
  const title = root.querySelector('.hermes-station .hermes-now-title');
  if (!title || title.textContent !== 'Synthetic Calm') fail('now playing ' + (title && title.textContent));
  if (root.querySelectorAll('.hermes-now-title').filter((n) => n.textContent === 'Synthetic Calm').length !== 1) fail('title leaked');
  plays[0].click();
  await new Promise((r) => _st(r, 20));
  if (state.pauses !== 1 || state.plays !== 1) fail('second press should pause, not restart');
  if (plays[0].getAttribute('aria-pressed') !== 'false') fail('still pressed after pause');
  root.querySelector('.hermes-ask').click();
  await new Promise((r) => _st(r, 20));
  if (global.__copied !== ASK) fail('clipboard ' + global.__copied);
  if (state.plays !== 1) fail('ask started playback');
  root.querySelector('button[data-t="popular"]').click();
  await new Promise((r) => _st(r, 20));
  if (root.querySelector('.hermes-hero')) fail('popular kept the hero');
  if (root.querySelector('button[data-t="popular"]').getAttribute('aria-selected') !== 'true') fail('popular not selected');
  root.querySelector('button[data-t="search"]').click();
  if (root.querySelector('button[data-t="search"]').getAttribute('aria-selected') !== 'true') fail('search not selected');
  root.querySelector('button[data-t="favorites"]').click();
  await new Promise((r) => _st(r, 20));
  if (!root.textContent.includes('favorite')) fail('favorites tab did not render');
  root.querySelector('button[data-t="browse"]').click();
  await new Promise((r) => _st(r, 20));
  if (root.querySelector('button[data-t="browse"]').getAttribute('aria-selected') !== 'true') fail('browse not selected');
  root.querySelector('button[data-t="hermes"]').click();
  await new Promise((r) => _st(r, 30));
  if (!root.querySelector('.hermes-hero')) fail('hermes tab did not come back');
  if (root.querySelectorAll('.hermes-play').length !== stations.length) fail('stations missing after return');
  console.log('ok');
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hermes_radio_render_smoke():
    res = subprocess.run(
        ["node", "-e", NODE_SMOKE, str(RADIO_JS), ASK],
        input=json.dumps(radio.hermes_lineup()),
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr or res.stdout
    assert res.stdout.strip().splitlines()[-1] == "ok"


def _chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _shot_page(chrome, port, width, height, url, profile):
    import base64
    websocket = pytest.importorskip("websocket")
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--no-sandbox", "--mute-audio",
            "--disable-dev-shm-usage", "--disable-crash-reporter", "--disable-breakpad",
            "--use-gl=angle", "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader",
            f"--remote-debugging-port={port}", "--remote-allow-origins=*",
            f"--user-data-dir={profile}", f"--disk-cache-dir={profile}/cache",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "TMPDIR": str(RUNTIME_ALIAS), "TEMP": str(RUNTIME_ALIAS), "TMP": str(RUNTIME_ALIAS)},
    )
    import json as _json
    import time
    import urllib.request
    deadline = time.time() + 20
    last = None
    ws = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = _json.loads(resp.read())
            target = next(t for t in targets if t.get("type") == "page")
            ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10)
            break
        except Exception as exc:  # chrome still starting
            last = exc
            time.sleep(0.2)
    if ws is None:
        proc.kill()
        raise AssertionError(f"chrome CDP never came up: {last}")
    seq = {"n": 0}

    def call(method, params=None):
        seq["n"] += 1
        mid = {"id": seq["n"], "method": method, "params": params or {}}
        ws.send(_json.dumps(mid))
        while True:
            msg = _json.loads(ws.recv())
            if msg.get("id") == mid["id"]:
                if "error" in msg:
                    raise AssertionError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def ev(expression):
        result = call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"].get("text") or result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def wait(expression, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ev(f"Boolean({expression})"):
                return
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for: {expression}")

    try:
        return _shot_drive(call, ev, wait, width, height, url)
    finally:
        try:
            ws.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _shot_drive(call, ev, wait, width, height, url):
    import base64
    call("Page.enable")
    call("Runtime.enable")
    call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
    })
    call("Page.navigate", {"url": url})
    # Boot lands on Music after the player exists. Clicking earlier gets overwritten.
    wait(
        "document.querySelector('#webamp-host') && document.getElementById('panel-title')?.textContent === 'Music'",
        timeout=45,
    )
    ev("document.querySelector('#nav button[data-view=\"hermes-radio\"]').click()")
    wait("document.querySelectorAll('.hermes-station').length >= 7")
    wait("document.querySelector('.hermes-soon')")
    info = ev("""(() => {
      const panel = document.getElementById('panel-body');
      const hero = document.querySelector('.hermes-hero');
      hero.scrollIntoView({block: 'start'});
      const pb = panel.getBoundingClientRect();
      const stations = [...document.querySelectorAll('.hermes-station')].map((s) => {
        const r = s.getBoundingClientRect();
        return {right: r.right, left: r.left, width: r.width};
      });
      const status = window.revampPlayerApi?.webamp?.store?.getState?.()?.media?.status || '';
      return {
        title: document.getElementById('panel-title')?.textContent,
        nav0: document.querySelector('#nav button')?.dataset.view,
        active: document.querySelector('.tabs button.active')?.dataset.t,
        ask: document.querySelector('.hermes-ask')?.textContent,
        soon: document.querySelector('.hermes-soon')?.innerText,
        soonControls: !!(document.querySelector('.hermes-soon button') || document.querySelector('.hermes-soon audio')),
        audio: document.querySelectorAll('audio').length,
        status,
        playing: document.querySelector('.hermes-station.is-playing') != null,
        count: stations.length,
        overflow: stations.some((s) => s.right > pb.right + 2 || s.left < pb.left - 2 || s.width < 80),
        panelWidth: Math.round(pb.width),
      };
    })()""")
    try:
        png = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    except AssertionError:
        png = call("Page.captureScreenshot", {"format": "png"})
    return info, base64.b64decode(png["data"])


@pytest.mark.skipif(_chrome() is None, reason="chrome is not installed")
@pytest.mark.parametrize("width", [400, 1200])
def test_hermes_radio_screenshots(server, width):
    pytest.importorskip("websocket")
    import socket
    chrome = _chrome()
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    if RUNTIME_ALIAS.is_symlink() or RUNTIME_ALIAS.exists():
        RUNTIME_ALIAS.unlink()
    os.symlink(TMP_DIR, RUNTIME_ALIAS, target_is_directory=True)
    profile = TMP_DIR / f"chrome-{width}"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    try:
        info, png = _shot_page(chrome, port, width, 1400 if width < 700 else 1100, server.base + "/", profile)
        out = SHOT_DIR / f"hermes-radio-{width}.png"
        out.write_bytes(png)
        assert info["nav0"] == "hermes-radio", info
        assert info["title"] == "Hermes Radio", info
        assert info["active"] == "hermes", info
        assert info["ask"] == "Ask Hermes to pick", info
        assert "Hermes FM" in info["soon"] and "coming soon" in info["soon"].lower(), info
        assert info["soonControls"] is False, info
        assert info["count"] == 7, info
        assert info["playing"] is False, info
        assert info["status"] != "PLAYING", info
        assert info["overflow"] is False, info
        assert info["panelWidth"] >= 200, info
        assert out.stat().st_size > 1000
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
        RUNTIME_ALIAS.unlink(missing_ok=True)
