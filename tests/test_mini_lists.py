"""Hermes mini player: endless lists, radio search paging, and row actions.

The player page is plain JS; its queue-insertion helper is checked with node.
The server tests use the real fixture server (see conftest.py) with no network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "desktop" / "plugin.js"
PLAYER = REPO / "web" / "player.js"
RADIO_VIEW = REPO / "web" / "views" / "radio.js"


class RadioResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_radio_search_passes_offset_to_radio_browser(monkeypatch):
    from revamp_core import radio

    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url if hasattr(req, "full_url") else str(req))
        return RadioResponse([])

    monkeypatch.setattr(radio.urllib.request, "urlopen", fake_urlopen)
    q = "offset-test-station"
    assert radio.search_radiobrowser(q=q, offset=40) == []
    assert seen, "Radio Browser was never called"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
    assert query["offset"] == ["40"]
    assert query["name"] == [q]


def test_radio_search_route_forwards_offset(server, monkeypatch):
    from revamp_core import radio

    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(radio, "search_radiobrowser", fake_search)
    status, _headers, body = server.client.get("/api/radio/search?q=x&offset=40")
    assert status == 200
    assert body == []
    assert calls and calls[0]["offset"] == 40


def test_full_page_radio_search_forwards_each_endless_page_offset():
    """The full-page Search loader must not keep requesting page zero."""
    text = RADIO_VIEW.read_text(encoding="utf-8")
    start = text.index("async function doSearch(q)")
    end = text.index("let debounceT", start)
    search = text[start:end]
    assert 'p.set("offset", String(offset))' in search


def test_library_tracks_returns_second_page_with_total(server, monkeypatch):
    from revamp_core import library

    fake = {}
    for i in range(130):
        fake[f"t{i:03d}"] = {
            "id": f"t{i:03d}",
            "title": f"Track {i:03d}",
            "artist": "Test Artist",
            "album": "Test Album",
            "path": str(server.lib / f"file{i:03d}.mp3"),
            "duration": 90,
            "genre": "Music",
            "track_no": i,
            "added": i,
        }
    monkeypatch.setattr(library, "_INDEX", fake)

    status, _headers, first = server.client.get("/api/library/tracks?offset=0&limit=100")
    assert status == 200
    assert first["total"] == 130
    assert len(first["tracks"]) == 100

    status, _headers, second = server.client.get("/api/library/tracks?offset=100&limit=100")
    assert status == 200
    assert second["total"] == 130
    assert len(second["tracks"]) == 30
    assert first["tracks"][0]["id"] == "t000"
    assert second["tracks"][0]["id"] == "t100"


def test_load_with_mode_next_is_accepted(monkeypatch):
    from revamp_core import playback

    item = {
        "id": "local:track:0123456789abcdef",
        "source": "local",
        "kind": "music",
        "title": "Queued Next",
        "playback": {"backend": "webamp", "url": "/api/media/0123456789abcdef"},
    }

    def broadcast(_event, _payload):
        return 1

    status, reply = playback.dispatch(
        {"action": "load", "items": [item], "mode": "next"}, broadcast)
    assert status == 200
    assert reply["accepted"] is True
    assert reply["backend"] == "webamp"


def test_player_insert_after_current_index():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = """
const fs = require('fs')
const src = fs.readFileSync(process.argv[1], 'utf8')
const match = src.match(/function insertAfter\\([^)]*\\) \\{[\\s\\S]*?\\n\\}/)
if (!match) throw new Error('insertAfter helper not found')
const insertAfter = new Function(match[0] + '; return insertAfter')()
const middle = insertAfter(['a', 'b', 'c'], 1, ['x', 'y'])
const tail = insertAfter(['a', 'b'], 1, ['x'])
const head = insertAfter([], -1, ['x'])
if (JSON.stringify(middle) !== JSON.stringify(['a', 'b', 'x', 'y', 'c'])) throw new Error('middle: ' + JSON.stringify(middle))
if (JSON.stringify(tail) !== JSON.stringify(['a', 'b', 'x'])) throw new Error('tail: ' + JSON.stringify(tail))
if (JSON.stringify(head) !== JSON.stringify(['x'])) throw new Error('head: ' + JSON.stringify(head))
console.log('ok')
"""
    result = subprocess.run([node, "-e", script, str(PLAYER)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert 'mode === "next"' in PLAYER.read_text(encoding="utf-8")


def test_plugin_has_context_menu_and_queue_labels():
    src = PLUGIN.read_text(encoding="utf-8")
    assert "onContextMenu" in src
    assert "onKeyDown" in src
    assert "ContextMenu" in src
    for label in ("Play now", "Play next", "Add to queue"):
        assert label in src
    assert "mode: 'append'" in src
    assert "mode: 'next'" in src
    assert "Loading more…" in src


def test_mini_shuffle_replaces_queue_with_only_the_visible_rows():
    """Shuffle in the mini picker must not reach outside its shown search/list."""
    src = PLUGIN.read_text(encoding="utf-8")
    start = src.index("async function shuffleVisible(")
    end = src.index("function stationItem(", start)
    helper = src[start:end]
    assert "const shuffled = [...rows]" in helper
    assert "await playItems(shuffled.map(toItem))" in helper
    assert "command('shuffle', { on: true })" in helper
    radio = src[src.index("function RadioList("):src.index("function LibraryList(")]
    library = src[src.index("function LibraryList("):src.index("const BooksList")]
    assert "shuffleVisible(rows, stationItem)" in radio
    assert "shuffleVisible(tracks, trackItem)" in library

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('async function shuffleVisible(');
const b = src.indexOf('function stationItem(', a);
const helper = src.slice(a, b);
const calls = [];
const shuffleVisible = new Function('playItems', 'holdNp', 'command', helper + '; return shuffleVisible')(
  async items => calls.push(['load', items]),
  (key, value) => calls.push(['hold', key, value]),
  (action, body) => calls.push(['command', action, body]),
);
const oldRandom = Math.random;
Math.random = () => 0;
const shown = [{ id: 'one' }, { id: 'two' }, { id: 'three' }];
shuffleVisible(shown, row => ({ id: row.id })).then(() => {
  Math.random = oldRandom;
  const want = JSON.stringify([{id:'two'}, {id:'three'}, {id:'one'}]);
  if (JSON.stringify(calls[0][1]) !== want) throw new Error(JSON.stringify(calls));
  if (JSON.stringify(calls.at(-1)) !== JSON.stringify(['command', 'shuffle', {on:true}])) throw new Error(JSON.stringify(calls));
}).catch(err => { console.error(err); process.exit(1); });
"""
    result = subprocess.run([node, "-e", script, str(PLUGIN)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

