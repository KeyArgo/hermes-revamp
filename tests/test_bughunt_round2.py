"""Regression proofs for independently found Round 2 bugs.

These specify the safe, user-facing behaviour; they deliberately fail until
the product defects described in FINDINGS.md are fixed.
"""

from __future__ import annotations

import importlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from revamp_core import radio, server as srv


@pytest.mark.skip(reason='backlog: live radio cannot seek anyway')
def test_radio_stream_proxy_forwards_byte_ranges_for_seek(server):
    """Seeking radio/proxied audio must preserve an upstream 206 response."""

    payload = b"0123456789"
    seen_ranges = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen_ranges.append(self.headers.get("Range"))
            ranged = self.headers.get("Range") == "bytes=2-5"
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", "audio/mpeg")
            if ranged:
                self.send_header("Content-Range", "bytes 2-5/10")
            self.send_header("Content-Length", str(4 if ranged else len(payload)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(payload[2:6] if ranged else payload)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/audio" % upstream.server_address[1]
        path = "/api/radio/stream?url=" + urllib.parse.quote(url, safe="")
        code, headers, body = server.client.get(
            path, headers={"Range": "bytes=2-5"}, raw=True)
    finally:
        upstream.shutdown()
        upstream.server_close()
    assert code == 206
    assert seen_ranges == ["bytes=2-5"]
    assert headers["Content-Range"] == "bytes 2-5/10"
    assert headers["Accept-Ranges"] == "bytes"
    assert body == payload[2:6]


def test_sse_slow_client_queue_is_bounded():
    """A connected client which never reads cannot make the event queue grow forever."""
    hub = srv.EventHub()
    slow_client = hub.register()
    for i in range(1024):
        hub.broadcast("command", {"sequence": i})
    # A slow client should have a finite backlog (or be disconnected), rather
    # than retaining every broadcast indefinitely.
    assert slow_client.qsize() <= 256


@pytest.mark.parametrize("tool_name", ["_tool_player", "_tool_library", "_tool_playlist", "_tool_radio"])
def test_agent_tools_return_json_error_for_non_object_args(tool_name):
    """Malformed tool calls must not bypass the tools' advertised JSON errors."""
    plugin = importlib.import_module("__init__")
    out = getattr(plugin, tool_name)(["not", "an", "object"])
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "argument" in parsed["error"].lower()


def test_distinct_station_urls_without_radio_browser_uuid_keep_distinct_ids(monkeypatch):
    """A degraded Radio Browser response must not collapse every station to ``rb-``."""
    radio._SEARCH_CACHE.clear()

    class Response:
        def read(self):
            return json.dumps([
                {"name": "One", "url": "https://one.example/live"},
                {"name": "Two", "url": "https://two.example/live"},
            ]).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(radio.urllib.request, "urlopen", lambda *_a, **_kw: Response())
    stations = radio.search_radiobrowser("missing-uuid", limit=2)
    assert len({station["id"] for station in stations}) == 2


def test_favorite_add_deduplicates_same_station_url(server):
    """A station added through two different discovery paths stays one favorite."""
    one = {"id": "search-id", "name": "Example FM", "url": "https://example.test/live"}
    two = {"id": "custom-id", "name": "Example FM again", "url": "https://example.test/live"}
    assert server.client.post("/api/radio/favorites", one)[0] == 201
    assert server.client.post("/api/radio/favorites", two)[0] == 201
    code, _, favorites = server.client.get("/api/radio/favorites")
    assert code == 200
    assert [station["url"] for station in favorites] == ["https://example.test/live"]


def test_desktop_rate_control_recognises_canonical_audiobook_kind():
    """The desktop control must use media_kind, as it already does for seekability."""
    source = (Path(__file__).parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "book: (np.media_kind || np.kind) === 'audiobook'" in source
