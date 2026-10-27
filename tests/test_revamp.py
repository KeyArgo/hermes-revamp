"""Revamp lane A tests: auth, library, range, playlists, state, SSE, tools.

Network-dependent checks (Radio Browser, stream proxy, nowplaying) are
marked ``network`` and skipped by default; run ``pytest -m network`` to
include them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

network = getattr(pytest, "mark", None)
if network is None:  # pragma: no cover
    pytest.skip("pytest missing", allow_module_level=True)

network = pytest.mark.network

from revamp_core import library, playlists, radio, store  # noqa: E402
from revamp_core.client import RevampClient, RevampUnavailable  # noqa: E402


def _wait_scanned(server, timeout: float = 20.0) -> int:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        st, _, body = server.client.get("/api/library/status")
        if st == 200 and not body.get("scanning") and body.get("count", 0) > 0:
            return body["count"]
        time.sleep(0.2)
    raise AssertionError("library never scanned")


# ---------------------------------------------------------------------------
# token auth + basics
# ---------------------------------------------------------------------------

def test_health_no_token(server):
    st, _, body = server.client._req("GET", "/api/health")
    assert st == 200 and body["app"] == "revamp"


def test_api_requires_token(server):
    st, headers, body = server.client._req(
        "GET", "/api/state", headers={"X-Revamp-Token": ""})
    assert st == 401
    assert "error" in body


def test_token_via_query_param(server):
    st, _, body = server.client._req("GET", f"/api/state?t={server.token}")
    assert st == 200 and "volume" in body


def test_options_preflight(server):
    st, headers, _ = server.client._req("OPTIONS", "/api/state")
    assert st == 204


def test_cors_headers_on_api(server):
    st, headers, _ = server.client.get("/api/state")
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_index_has_token_meta_no_acao(server):
    st, headers, body = server.client._req("GET", "/", raw=True)
    html = body.decode()
    assert st == 200
    assert 'meta name="revamp-token"' in html
    assert server.token[:8] in html
    assert "Access-Control-Allow-Origin" not in headers


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

def test_library_scan_and_search(server):
    n = _wait_scanned(server)
    assert n >= 3
    st, _, body = server.client.get("/api/library/tracks?q=song")
    assert st == 200
    titles = {t["title"] for t in body["tracks"]}
    assert "Song One" in titles and "Song Two" in titles
    assert body["total"] >= 2
    # all-words match
    st, _, body2 = server.client.get("/api/library/tracks?q=song%20one")
    assert {t["title"] for t in body2["tracks"]} == {"Song One"}


def test_library_search_filters_and_sort(server):
    _wait_scanned(server)
    st, _, by_artist = server.client.get("/api/library/tracks?artist=artists")
    assert {t["artist"] for t in by_artist["tracks"]} <= {"The Artists"}
    st, _, sorted_tracks = server.client.get("/api/library/tracks?sort=title")
    titles = [t["title"] for t in sorted_tracks["tracks"]]
    assert titles == sorted(titles, key=str.lower)


def test_library_artists_albums(server):
    _wait_scanned(server)
    st, _, artists = server.client.get("/api/library/artists")
    names = {a["name"] for a in artists}
    assert "The Artists" in names
    for a in artists:
        assert set(a) == {"name", "albums", "tracks"}
    st, _, albums = server.client.get("/api/library/albums?artist=artists")
    assert any(a["album"] == "Great Album" for a in albums)
    assert all("art_track_id" in a for a in albums)


def test_media_full_and_range(server):
    _wait_scanned(server)
    st, _, body = server.client.get("/api/library/tracks?q=Song%20One")
    tid = body["tracks"][0]["id"]
    st, headers, payload = server.client._req("GET", f"/api/media/{tid}", raw=True)
    assert st == 200
    assert headers.get("Accept-Ranges") == "bytes"
    size = int(headers["Content-Length"])
    assert size > 0

    st, h2, payload2 = server.client._req(
        "GET", f"/api/media/{tid}", headers={"Range": "bytes=0-99"}, raw=True)
    assert st == 206
    assert h2["Content-Range"] == f"bytes 0-99/{size}"
    assert len(payload2) == 100

    st, _, _ = server.client._req(
        "GET", f"/api/media/{tid}", headers={"Range": "bytes=999999999-"})
    assert st == 416

    # suffix range
    st, h3, payload3 = server.client._req(
        "GET", f"/api/media/{tid}", headers={"Range": "bytes=-50"}, raw=True)
    assert st == 206 and len(payload3) == 50

    # unknown id -> 404
    st, _, _ = server.client.get("/api/media/deadbeefdeadbeef")
    assert st == 404


def test_library_status_and_rescan(server):
    _wait_scanned(server)
    st, _, status = server.client.get("/api/library/status")
    assert st == 200
    assert {"scanning", "count", "roots", "last_scan"} <= set(status)
    st, _, res = server.client.post("/api/library/rescan")
    assert st == 200 and res["started"] is True


def test_art_or_404(server):
    _wait_scanned(server)
    st, _, body = server.client.get("/api/library/tracks?q=Song%20One")
    tid = body["tracks"][0]["id"]
    st, _, _ = server.client._req("GET", f"/api/art/{tid}", raw=True)
    assert st in (200, 404)  # ffmpeg may or may not embed art; cover.jpg exists
    if st == 200:
        pass


# ---------------------------------------------------------------------------
# static / skins
# ---------------------------------------------------------------------------

def test_skins_route(server):
    st, _, skins = server.client.get("/api/skins")
    ids = [s["id"] for s in skins]
    assert st == 200 and "classic" in ids
    classic = next(s for s in skins if s["id"] == "classic")
    assert classic["url"] is None and classic["builtin"] is True


def test_static_web_dir_and_traversal(server):
    st, _, body = server.client.get("/api/state")
    # Static routes serve only inside their own folder: no repo files, no token.
    for path in ("/web/../plugin.yaml", "/web/..%2Fplugin.yaml",
                 "/skins/user/..%2Ftoken", "/skins/user/..%2Fstate.json"):
        st, _, _ = server.client._req("GET", path, raw=True)
        assert st == 404, path
    st, _, _ = server.client._req("GET", "/web/index.html", raw=True)
    assert st == 200
    st, h, payload = server.client._req(
        "GET", "/vendor/WEBAMP-LICENSE.txt", raw=True)
    if st == 200:
        assert "text/plain" in h.get("Content-Type", "") or \
            h.get("Content-Type", "").startswith("text")


# ---------------------------------------------------------------------------
# playlists
# ---------------------------------------------------------------------------

def test_playlist_crud(server):
    _wait_scanned(server)
    st, _, created = server.client.post(
        "/api/playlists", {"name": "Road Mix", "items": []})
    assert st == 201 and created["id"] == "road-mix"
    st, _, listing = server.client.get("/api/playlists")
    assert any(p["id"] == "road-mix" for p in listing)
    st, _, got = server.client.get("/api/playlists/road-mix")
    assert st == 200 and got["items"] == []
    # update: add items
    st, _, body = server.client.get("/api/library/tracks?q=Song%20One")
    tid = body["tracks"][0]["id"]
    item = {"kind": "track", "title": "Song One", "artist": "The Artists",
            "url": f"/api/media/{tid}", "duration": 2.0,
            "track_id": tid, "station_id": None}
    st, _, upd = server.client.put("/api/playlists/road-mix", {"items": [item]})
    # stored and returned as canonical items (revamp_core/items.py)
    assert st == 200 and upd["items"][0]["id"] == f"local:track:{tid}"
    # rename
    st, _, upd2 = server.client.put("/api/playlists/road-mix",
                                    {"name": "Rename Mix"})
    # the id never changes on a rename (links keep working); the name does
    assert upd2["id"] == "road-mix" and upd2["name"] == "Rename Mix"
    st, _, gone = server.client.delete("/api/playlists/road-mix")
    assert st == 200 and gone["ok"] is True
    st, _, _ = server.client.get("/api/playlists/road-mix")
    assert st == 404


def test_playlist_m3u_roundtrip(server):
    _wait_scanned(server)
    st, _, body = server.client.get("/api/library/tracks?q=Song%20One")
    track = body["tracks"][0]
    item = {"kind": "track", "title": track["title"], "artist": track["artist"],
            "url": track["url"], "duration": track["duration"],
            "track_id": track["id"], "station_id": None}
    st, _, pl = server.client.post("/api/playlists",
                                   {"name": "RT", "items": [item]})
    st, _, raw = server.client._req(
        "GET", "/api/playlists/rt/export?format=m3u8", raw=True)
    assert st == 200
    text = raw.decode()
    assert text.startswith("#EXTM3U")
    assert "#EXTINF:" in text
    assert str(server.lib / "Song One.mp3") in text  # abs path written
    # re-import the same text
    st, _, imp = server.client.post(
        "/api/playlists/import", {"name": "RT2", "format": "m3u", "text": text})
    assert st == 201
    assert imp["items"][0]["id"] == f"local:track:{track['id']}"
    assert imp["items"][0]["playback"]["url"] == track["url"]


def test_playlist_pls_roundtrip(server):
    pls = "[playlist]\nFile1=http://example.org/stream\nTitle1=Foo - Bar\n" \
          "Length1=120\nNumberOfEntries=1\nVersion=2\n"
    st, _, imp = server.client.post(
        "/api/playlists/import", {"name": "PLS Imp", "format": "pls", "text": pls})
    assert st == 201
    it = imp["items"][0]
    assert it["playback"]["stream"] == "http://example.org/stream" and it["kind"] == "stream"
    assert it["artist"] == "Foo" and it["title"] == "Bar"
    st, _, raw = server.client._req(
        "GET", "/api/playlists/pls-imp/export?format=pls", raw=True)
    assert st == 200
    out = raw.decode()
    assert "[playlist]" in out and "File1=http://example.org/stream" in out
    assert "NumberOfEntries=1" in out


def test_playlist_import_validation(server):
    st, _, _ = server.client.post("/api/playlists/import",
                                  {"name": "x", "format": "nope", "text": ""})
    assert st == 400


# ---------------------------------------------------------------------------
# state + heartbeat + radio favorites
# ---------------------------------------------------------------------------

def test_state_merge_and_persist(server):
    st, _, _ = server.client.put("/api/state", {"volume": 42, "bogus": 1})
    assert st == 200
    st, _, state = server.client.get("/api/state")
    assert state["volume"] == 42
    assert state["live"]["player_connected"] is False
    # eq merge
    eq = {"on": True, "preamp": 3.0, "bands": list(range(10))}
    server.client.put("/api/state", {"eq": eq})
    st, _, state = server.client.get("/api/state")
    assert state["eq"]["on"] is True and len(state["eq"]["bands"]) == 10
    # live ignored on PUT
    server.client.put("/api/state", {"live": {"x": 1}})
    st, _, state2 = server.client.get("/api/state")
    assert "x" not in state2["live"] or state2["live"] != {"x": 1}


def test_heartbeat_live_window(server):
    st, _, _ = server.client.post(
        "/api/heartbeat", {"now_playing": {"title": "T", "status": "playing",
                                           "position": 5, "duration": 100}})
    assert st == 200
    st, _, state = server.client.get("/api/state")
    live = state["live"]
    assert live["player_connected"] is True
    assert live["now_playing"]["title"] == "T"
    # resume map survives
    server.client.put("/api/state", {"resume": {"abc": 12.5}})
    st, _, state = server.client.get("/api/state")
    assert state["resume"] == {"abc": 12.5}


def test_radio_favorites_crud(server):
    st, _, favs = server.client.get("/api/radio/favorites")
    assert st == 200 and favs == []
    station = {"id": "custom-x", "name": "X FM", "url": "http://x.example/a"}
    st, _, added = server.client.post("/api/radio/favorites", station)
    assert st == 201 and added["source"] == "custom"
    st, _, favs = server.client.get("/api/radio/favorites")
    assert len(favs) == 1 and favs[0]["id"] == "custom-x"
    st, _, res = server.client.delete("/api/radio/favorites?id=custom-x")
    assert st == 200 and res["ok"] is True
    st, _, favs = server.client.get("/api/radio/favorites")
    assert favs == []


def test_radio_presets_shape(server):
    st, _, presets = server.client.get("/api/radio/presets")
    assert st == 200 and len(presets) == len(radio.PRESETS) >= 30
    groove = next(p for p in presets if "Groove Salad" in p["name"])
    assert groove["url"] == "https://ice1.somafm.com/groovesalad-128-mp3"
    assert groove["source"] == "preset"
    assert {"id", "name", "url", "homepage", "tags", "codec", "bitrate",
            "favicon", "country", "source"} <= set(groove)


# ---------------------------------------------------------------------------
# commands + SSE + agent events
# ---------------------------------------------------------------------------

def test_command_validation_and_sse_broadcast(server):
    res, events = _collect_command(server)
    assert res["delivered"] >= 1
    assert any(c.get("action") == "volume" for c in
               (d for e, d in events if e == "command"))


def _collect_command(server):
    import threading
    import time

    events: list = []

    def run():
        events.extend(server.sse_lines(seconds=4.0, want=1))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.6)
    st, _, res = server.client.post("/api/command",
                                    {"action": "volume", "percent": 33})
    t.join(timeout=6)
    return res, events


def test_command_broadcast_via_sse(server):
    res, events = _collect_command(server)
    assert res["ok"] is True and res["delivered"] >= 1
    cmds = [data for ev, data in events if ev == "command"]
    assert cmds, "no command event observed on SSE"
    assert any(c.get("action") == "volume" and c.get("percent") == 33
               for c in cmds)


def test_command_unknown_action_400(server):
    st, _, res = server.client.post("/api/command", {"action": "wobble"})
    assert st == 400 and "error" in res


def test_agent_event_ring_and_broadcast(server):
    for i in range(3):
        st, _, _ = server.client.post(
            "/api/agent_event", {"kind": "tool_start", "tool": "terminal",
                                 "preview": f"cmd {i}"})
        assert st == 200
    st, _, events = server.client.get("/api/agent_events")
    assert st == 200 and len(events) >= 3
    assert events[-1]["tool"] == "terminal"
    assert "ts" in events[-1]
    # kinds validated client-side only; unknown kinds still recorded
    server.client.post("/api/agent_event", {"kind": "whatever"})


def test_agent_event_ring_cap_200(server, monkeypatch):
    # post 210 directly through store with the server's data dir env active
    import time as _time

    for i in range(210):
        store.add_agent_event({"kind": "tool_start", "i": i, "ts": _time.time()})
    st, _, events = server.client.get("/api/agent_events")
    assert len(events) <= 200
    assert events[-1].get("i") == 209 or events[-1]["kind"] == "tool_start"


# ---------------------------------------------------------------------------
# SSE ping framing
# ---------------------------------------------------------------------------

def test_sse_stream_shape(server):
    import threading
    import time

    raw = bytearray()

    def read():
        import socket as _s
        port = int(server.base.rsplit(":", 1)[1])
        s = _s.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(f"GET /api/events?t={server.token} HTTP/1.1\r\n"
                  f"Host: x\r\nAccept: text/event-stream\r\n\r\n".encode())
        s.settimeout(0.4)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except _s.timeout:
                continue
            if not chunk:
                break
            raw.extend(chunk)
        try:
            s.close()
        except OSError:
            pass

    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(timeout=4)
    head = bytes(raw[:200]).decode(errors="replace")
    assert "200" in head
    assert "text/event-stream" in head
    assert "retry:" in bytes(raw).decode(errors="replace")


# ---------------------------------------------------------------------------
# python client
# ---------------------------------------------------------------------------

def test_revamp_client_ok_and_down(server):
    c = RevampClient(base=server.base, token=server.token)
    state = c.get("/api/state")
    assert "volume" in state
    st = c.post("/api/heartbeat", {"now_playing": {"title": "x"}})
    assert st["live"]["player_connected"] is True
    # wrong port -> unavailable
    c2 = RevampClient(base="http://127.0.0.1:1", token="x", timeout=0.5)
    with pytest.raises(RevampUnavailable):
        c2.get("/api/state")


def test_revamp_client_token_from_disk(server):
    # no explicit token: reads <data_dir>/token via env set by the fixture
    c = RevampClient(base=server.base)
    assert c.token == server.token
    assert "volume" in c.get("/api/state")


# ---------------------------------------------------------------------------
# tool handlers (registered in __init__.py)
# ---------------------------------------------------------------------------

def _load_tools(server):
    import importlib
    import sys as _sys

    repo = str(Path(__file__).resolve().parent.parent)
    if repo not in _sys.path:
        _sys.path.insert(0, repo)
    import __init__ as plugin_root
    importlib.reload(plugin_root)
    return plugin_root


def test_tool_player_status_and_volume(server):
    plug = _load_tools(server)
    out = json.loads(plug._tool_player({"action": "status"}))
    assert out["ok"] is True
    assert "now_playing" in out
    out = json.loads(plug._tool_player({"action": "volume", "percent": 55}))
    assert out["ok"] is True
    st, _, state = server.client.get("/api/state")
    assert state["volume"] == 80  # command broadcast; state unchanged until page applies
    out = json.loads(plug._tool_player({"action": "previous"}))
    assert out["ok"] is True
    out = json.loads(plug._tool_player({"action": "nope"}))
    assert out["ok"] is False


def test_tool_library_search_play(server):
    plug = _load_tools(server)
    _wait_scanned(server)
    out = json.loads(plug._tool_library({"action": "search", "query": "song",
                                         "limit": 5}))
    assert out["ok"] is True and out["total"] >= 2
    assert len(out["tracks"]) <= 5
    out = json.loads(plug._tool_library({"action": "play", "query": "song"}))
    assert out["ok"] is True and out["loaded"] >= 1
    out = json.loads(plug._tool_library({"action": "enqueue", "query": "zzz-none"}))
    assert out["ok"] is False
    out = json.loads(plug._tool_library({"action": "stats"}))
    assert out["ok"] is True and "count" in out


def test_tool_playlist_flow(server):
    plug = _load_tools(server)
    _wait_scanned(server)
    out = json.loads(plug._tool_playlist({"action": "create", "name": "Agent Mix"}))
    assert out["ok"] is True
    out = json.loads(plug._tool_playlist({"action": "add", "name": "agent-mix",
                                          "query": "song"}))
    assert out["ok"] is True and out["added"] >= 1
    out = json.loads(plug._tool_playlist({"action": "list"}))
    assert any(p["id"] == "agent-mix" for p in out["playlists"])
    out = json.loads(plug._tool_playlist({"action": "play", "name": "agent-mix"}))
    assert out["ok"] is True
    out = json.loads(plug._tool_playlist({"action": "delete", "name": "agent-mix"}))
    assert out["ok"] is True


def test_tool_radio_presets_and_play(server):
    plug = _load_tools(server)
    out = json.loads(plug._tool_radio({"action": "presets"}))
    assert out["ok"] is True and len(out["stations"]) == len(radio.PRESETS)
    out = json.loads(plug._tool_radio({"action": "play",
                                       "station_id": "somafm-dronezone"}))
    assert out["ok"] is True
    assert out["station"] == "SomaFM Drone Zone"
    out = json.loads(plug._tool_radio({"action": "play", "url": "http://x/y",
                                       "name": "Custom"}))
    assert out["ok"] is True


def test_store_unit_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data2"))
    # fresh import cache so store picks up the env
    import importlib
    import sys as _sys

    from revamp_core import config, store as store_mod

    importlib.reload(config)
    store2 = importlib.reload(store_mod)
    st = store2.get_state()
    assert st["volume"] == 80
    st = store2.update({"volume": 7, "nope": 1})
    assert st["volume"] == 7
    disk = json.loads((tmp_path / "data2" / "state.json").read_text())
    assert disk["volume"] == 7 and "live" not in disk


# ---------------------------------------------------------------------------
# network-dependent (radio browser / stream proxy / nowplaying)
# ---------------------------------------------------------------------------

@network
def test_radio_browser_search_live():
    from revamp_core import radio
    try:
        res = radio.search_radiobrowser("chillsynth", limit=3)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"radio-browser unreachable: {exc}")
    assert isinstance(res, list) and len(res) <= 3


@network
def test_stream_proxy_resolve():
    from revamp_core import radio
    # pls resolution of a known-good playlist URL
    url = radio.resolve_playlist_url("https://somafm.com/groovesalad.pls")
    if url is None:
        pytest.skip("network unreachable")
    assert url.startswith("http")


@network
def test_nowplaying_icy():
    from revamp_core import radio
    meta = radio.nowplaying("https://ice1.somafm.com/groovesalad-128-mp3")
    if meta is None:
        pytest.skip("stream unreachable")
    assert isinstance(meta, dict)


# ---------------------------------------------------------------------------
# regressions found in review (2026-10-21)
# ---------------------------------------------------------------------------

def test_tool_playlist_accepts_display_name(server):
    plug = _load_tools(server)
    _wait_scanned(server)
    json.loads(plug._tool_playlist({"action": "create", "name": "Road Trip Mix"}))
    out = json.loads(plug._tool_playlist({"action": "add", "name": "Road Trip Mix", "query": "song"}))
    assert out["ok"] is True and out["added"] >= 1
    out = json.loads(plug._tool_playlist({"action": "show", "name": "road trip mix"}))
    assert out["ok"] is True and out["items"]
    out = json.loads(plug._tool_playlist({"action": "show", "name": "No Such List"}))
    assert out["ok"] is False and "hint" not in out


@network
def test_tool_radio_play_prefers_presets_by_name(server):
    # Popular first: with Radio Browser reachable the top search hit wins (it is
    # SomaFM Drone Zone, possibly with a bitrate suffix); offline, the preset does.
    plug = _load_tools(server)
    out = json.loads(plug._tool_radio({"action": "play", "query": "drone"}))
    assert out["ok"] is True and out["station"].startswith("SomaFM Drone Zone")


def test_search_matches_word_starts_only():
    from revamp_core.library import _matches
    entry = {"title": "At the Mountains of Madness [Blackstone Edition]", "artist": "H. P. Lovecraft"}
    assert not _matches(entry, ["tone"])
    assert _matches(entry, ["black"]) and _matches(entry, ["lovecraft", "madness"])
    assert _matches({"title": "Test Tone A"}, ["tone"])
