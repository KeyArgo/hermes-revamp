"""Hermes Radio: Hermes Agent Radio's six channels, and a render of the hero.

The lineup is Hermes Agent Radio only (the old desktop lineup was removed). The render
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


_SITE = "https://hermesagentradio.com/"
AGENT_RADIO_ROWS = [
    ("hermes-agent-radio-main", "Hermes Agent Radio", "Everything we play, one stream", "Hermes Agent Radio",
     "https://hermesagentradio.com/stream", _SITE),
    ("hermes-agent-radio-hiphop", "Hermes Agent Radio Hip-Hop", "Bars, beats and agents with something to say",
     "Hermes Agent Radio", "https://hermesagentradio.com/stream/hiphop", _SITE),
    ("hermes-agent-radio-dance", "Hermes Agent Radio Dance", "Four on the floor, all night", "Hermes Agent Radio",
     "https://hermesagentradio.com/stream/dance", _SITE),
    ("hermes-agent-radio-rock", "Hermes Agent Radio Rock", "Guitars, grit and volume", "Hermes Agent Radio",
     "https://hermesagentradio.com/stream/rock", _SITE),
    ("hermes-agent-radio-smooth", "Hermes Agent Radio Smooth", "Slow it down and stay a while", "Hermes Agent Radio",
     "https://hermesagentradio.com/stream/smooth", _SITE),
    ("hermes-agent-radio-techno", "Hermes Agent Radio Techno", "Machines, bass and no brakes", "Hermes Agent Radio",
     "https://hermesagentradio.com/stream/techno", _SITE),
]


def test_agent_radio_channels_are_https_and_listed_first():
    rows = [_row(s) for s in radio.agent_radio()]
    assert rows == AGENT_RADIO_ROWS
    assert all(s["url"].startswith("https://") for s in radio.agent_radio())
    assert radio.hermes_lineup()[0]["name"] == "Hermes Agent Radio"


def test_agent_radio_endpoint(server):
    status, _, body = server.client.get("/api/radio/agent-radio")
    assert status == 200
    assert [_row(s) for s in body] == AGENT_RADIO_ROWS


def test_status_bar_radio_list_leads_with_agent_radio_and_links_the_site():
    plugin = (REPO / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "rest('/radio/agent-radio')" in plugin
    assert "const rows = [...leading, ...pinned," in plugin
    assert "openExternal?.(AGENT_RADIO_SITE)" in plugin
    assert "const AGENT_RADIO_SITE = 'https://hermesagentradio.com/'" in plugin
    api = (REPO / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert '@router.get("/radio/agent-radio")' in api


def test_hermes_radio_is_only_hermes_agent_radio():
    """The hero lists Hermes Agent Radio's channels and nothing else (owner ask, 11-19)."""
    rows = radio.hermes_lineup()
    assert [_row(s) for s in rows] == AGENT_RADIO_ROWS
    assert all(s["name"].startswith("Hermes Agent Radio") for s in rows)
    assert not hasattr(radio, "HERMES_LINEUP")
    assert "hermes-fm" not in {s["id"] for s in radio.PRESETS}


def test_hermes_lineup_endpoint(server):
    status, _, body = server.client.get("/api/radio/hermes")
    assert status == 200
    assert [_row(s) for s in body] == AGENT_RADIO_ROWS
    assert all("url" in s and s["url"] for s in body)


def test_tool_lists_and_plays_a_hermes_station(server):
    import importlib
    import __init__ as plugin_root
    importlib.reload(plugin_root)
    listed = json.loads(plugin_root._tool_radio({"action": "hermes"}))
    assert listed["ok"] is True
    assert [s["id"] for s in listed["stations"]] == [row[0] for row in AGENT_RADIO_ROWS]
    assert "coming_soon" not in listed
    played = json.loads(plugin_root._tool_radio({"action": "play", "station_id": "hermes-agent-radio-techno"}))
    assert played["ok"] is True and played["station"] == "Hermes Agent Radio Techno"
    # A preset id still resolves to the built-in preset, not the hero.
    preset = json.loads(plugin_root._tool_radio({"action": "play", "station_id": "somafm-groovesalad"}))
    assert preset["ok"] is True and preset["station"] == "SomaFM Groove Salad"


def test_radio_opens_on_popular_and_the_hermes_entry_opens_hermes_radio():
    text = RADIO_JS.read_text(encoding="utf-8")
    assert 'let tab = opts.tab === "hermes" ? "hermes" : "popular";' in text
    # Popular is the first tab, Hermes Radio right after it.
    assert text.index('data-t="popular"') < text.index('data-t="hermes"') < text.index('data-t="browse"')
    assert "loadHermes();" in text
    assert "autoplay" not in text
    assert ASK in text
    assert "Hermes FM" not in text and "hermes-soon" not in text
    assert "Hermes FM" not in (REPO / "__init__.py").read_text(encoding="utf-8")
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    assert app.index('id: "hermes-radio"') < app.index('id: "library"')
    assert 'renderRadio(root, api, { tab: "hermes" })' in app


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
  el.removeAttribute = (name) => { el._attrs.delete(name); };
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
const state = { url: null, status: 'STOPPED', plays: 0, pauses: 0, stops: 0, items: [], slow: false };
global.revampPlayerApi = {
  get items() { return state.items; },
  webamp: { store: { getState: () => ({ media: { status: state.status }, playlist: { currentTrack: state.url ? 0 : null, trackOrder: state.url ? [0] : [] } }) } },
  playItemsAt(index, list) {
    state.plays += 1;
    state.url = list[index].url;
    state.status = state.slow ? 'STOPPED' : 'PLAYING';
    state.items = list.map((row) => ({ kind: 'stream', playback: { stream: row.url }, title: row.title }));
  },
  pause() { state.pauses += 1; state.status = 'PAUSED'; },
  play() { state.status = 'PLAYING'; },
  stop() { state.stops += 1; state.status = 'STOPPED'; },
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
  await mod.render(root, api, { tab: 'hermes' });
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
  if (root.querySelector('.hermes-soon') || root.textContent.includes('Hermes FM')) fail('Hermes FM card is back');
  if (root.querySelector('audio')) fail('audio element on first paint');
  const web = root.querySelector('.hermes-web-link');
  if (!web || web.getAttribute('href') !== 'https://hermesagentradio.com/' || web.getAttribute('target') !== '_blank') fail('web listening link');
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
  // A stream that takes a while to connect shows it was pressed straight away, and a
  // second press while it connects cancels instead of doing nothing.
  state.slow = true;
  plays[1].click();
  await new Promise((r) => _st(r, 20));
  const card1 = plays[1].parentNode;
  if (plays[1].getAttribute('aria-pressed') !== 'true') fail('no pressed state while connecting');
  if (plays[1].getAttribute('aria-busy') !== 'true') fail('no busy state while connecting');
  if (!plays[1].getAttribute('aria-label').startsWith('Starting ')) fail('label while connecting ' + plays[1].getAttribute('aria-label'));
  if (card1.querySelector('.hermes-starting').hidden) fail('Starting… note hidden');
  plays[1].click();
  await new Promise((r) => _st(r, 20));
  if (state.stops !== 1) fail('second press while connecting did not cancel');
  if (plays[1].getAttribute('aria-pressed') !== 'false' || plays[1].getAttribute('aria-busy')) fail('still busy after cancel');
  state.slow = false;
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
  // Browse lists Hermes Agent Radio as a genre, even with nothing from Radio Browser; it opens Hermes Radio.
  const hermesCat = root.querySelector('.radio-catcard-hermes');
  if (!hermesCat || !hermesCat.textContent.includes('Hermes Agent Radio')) fail('Browse has no Hermes Agent Radio card');
  hermesCat.click();
  await new Promise((r) => _st(r, 30));
  if (root.querySelector('button[data-t="hermes"]').getAttribute('aria-selected') !== 'true') fail('Hermes card did not open Hermes Radio');
  root.querySelector('button[data-t="browse"]').click();
  await new Promise((r) => _st(r, 20));
  root.querySelector('button[data-t="hermes"]').click();
  await new Promise((r) => _st(r, 30));
  if (!root.querySelector('.hermes-hero')) fail('hermes tab did not come back');
  if (root.querySelectorAll('.hermes-play').length !== stations.length) fail('stations missing after return');
  console.log('ok');
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


# Inside Hermes Desktop the page is a control frame: the player lives in another frame and
# window.revampPlayerApi has no Webamp store, only the server's view of the player. Before
# the fix the buttons never showed a press, and a second press restarted instead of pausing.
NODE_CONTROL = NODE_SMOKE[:NODE_SMOKE.index("const calls = [];")] + r"""
const calls = [];
const server = { url: null, status: 'STOPPED', loads: 0, pauses: 0, refreshes: 0 };
global.revampPlayerApi = {
  control: true,
  live: { url: null, status: 'STOPPED' },
  liveStream() { return this.live; },
  async refreshLive() { server.refreshes += 1; this.live = { url: server.url, status: server.status }; return this.live; },
  async playItemsAt(index, list) { server.loads += 1; server.url = list[index].url; server.status = 'PLAYING'; },
  pause() { server.pauses += 1; server.status = 'PAUSED'; },
  play() { server.status = 'PLAYING'; },
  stop() { server.status = 'STOPPED'; },
};
window.revampPlayerApi = global.revampPlayerApi;
(async () => {
  const mod = await import(pathToFileURL(process.argv[1]).href);
  const root = document.createElement('div');
  document.body.appendChild(root);
  const api = async (path) => {
    calls.push(String(path));
    if (String(path).startsWith('/api/radio/hermes')) return stations;
    if (String(path).includes('nowplaying')) return { title: 'Synthetic Calm' };
    return [];
  };
  await mod.render(root, api, { tab: 'hermes' });
  const fail = (msg) => { throw new Error(msg); };
  const plays = root.querySelectorAll('.hermes-play');
  if (plays.length !== 6) fail('expected 6 channels, got ' + plays.length);
  plays[2].click();
  await new Promise((r) => _st(r, 20));
  if (plays[2].getAttribute('aria-pressed') !== 'true') fail('no pressed state right after the click');
  await new Promise((r) => _st(r, 700));
  if (!plays[2].getAttribute('aria-label').startsWith('Pause ')) fail('never showed playing: ' + plays[2].getAttribute('aria-label'));
  plays[2].click();
  await new Promise((r) => _st(r, 700));
  if (server.pauses !== 1 || server.loads !== 1) fail('second press restarted instead of pausing ' + JSON.stringify(server));
  if (plays[2].getAttribute('aria-pressed') !== 'false') fail('still pressed after pause');
  root.remove ? root.remove() : null;
  console.log('ok');
  process.exit(0);
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hermes_radio_buttons_follow_the_player_inside_hermes_desktop():
    res = subprocess.run(
        ["node", "-e", NODE_CONTROL, str(RADIO_JS), ASK],
        input=json.dumps(radio.hermes_lineup()),
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr or res.stdout
    assert res.stdout.strip().splitlines()[-1] == "ok"


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
    wait("document.querySelectorAll('.hermes-station').length >= 6")
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
        soon: document.querySelector('.hermes-soon') != null || document.body.innerText.includes('Hermes FM'),
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
        assert info["soon"] is False, info
        assert info["count"] == 6, info
        assert info["playing"] is False, info
        assert info["status"] != "PLAYING", info
        assert info["overflow"] is False, info
        assert info["panelWidth"] >= 200, info
        assert out.stat().st_size > 1000
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
        RUNTIME_ALIAS.unlink(missing_ok=True)
