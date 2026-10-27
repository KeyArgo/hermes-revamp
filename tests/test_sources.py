"""Core seams: canonical items, the source registry, resume keys, the playback
controller and canonical playlists."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from revamp_core import items, playback, sources
from source_contract import check_source

REPO = Path(__file__).resolve().parent.parent
TID = "0123456789abcdef"

LEGACY = [
    {"kind": "track", "title": "Book", "artist": "A", "url": f"/api/media/{TID}",
     "duration": 100, "track_id": TID, "book": True},
    {"kind": "track", "title": "Song", "url": f"/api/media/{TID}", "duration": 200, "track_id": TID,
     "book": False},
    {"kind": "track", "title": "Long", "url": f"/api/media/{TID}", "duration": 5000, "track_id": TID},
    {"kind": "track", "title": "Short", "url": f"/api/media/{TID}", "duration": 60, "track_id": None},
    {"kind": "stream", "title": "FM", "artist": "Radio", "url": "https://x.example/s?a=1",
     "station_id": "somafm-x", "duration": None, "track_id": None},
    {"kind": "stream", "title": "Bare", "url": "https://y.example/live"},
    {"kind": "track", "title": "Web", "url": "https://h.example/z.mp3"},
    {"kind": "track"},           # no url: dropped
    "junk",                      # not an item: dropped
]


# ---------------------------------------------------------------------------
# normaliser
# ---------------------------------------------------------------------------

def test_normalize_legacy_items():
    out = items.normalize_list(LEGACY)
    assert len(out) == 7
    book, song, long_, short, fm, bare, web = out
    assert book["id"] == f"local:track:{TID}" and book["source"] == "local"
    assert book["kind"] == "audiobook" and book["capabilities"] == ["seek", "resume"]
    assert book["resumeKey"] == book["id"]
    assert book["playback"] == {"backend": "webamp", "url": f"/api/media/{TID}"}
    assert book["art"] == f"/api/art/{TID}"
    assert song["kind"] == "music" and "resumeKey" not in song
    assert long_["kind"] == "audiobook"          # the old 20-minute rule, only for legacy items
    assert short["source"] == "local" and short["kind"] == "music"  # id taken from the media URL
    assert fm["id"] == "radio:station:somafm-x" and fm["kind"] == "stream"
    assert fm["capabilities"] == []
    assert fm["playback"]["stream"] == "https://x.example/s?a=1"
    assert fm["playback"]["url"] == "/api/radio/stream?url=https%3A%2F%2Fx.example%2Fs%3Fa%3D1"
    assert bare["id"].startswith("radio:url:")
    assert web["source"] == "url" and web["playback"]["url"] == "https://h.example/z.mp3"
    for it in out:  # no legacy keys left
        assert not {"url", "track_id", "station_id", "book"} & set(it)
    assert items.legacy_kind(fm) == "stream" and items.legacy_kind(book) == "track"
    assert items.legacy_url(fm) == "https://x.example/s?a=1"


def test_normalize_canonical_is_stable_and_keeps_extras():
    once = items.normalize_list(LEGACY)
    assert items.normalize_list(once) == once
    plex = {"id": "plex@abc:track/5531", "kind": "audiobook", "title": "Ch 1",
            "playback": {"backend": "webamp", "url": "/api/sources/plex@abc/stream/track%2F5531"},
            "resumeKey": "plex@abc:book/77", "capabilities": ["seek", "resume"],
            "parent": "plex@abc:book/77", "offset": 0, "chapters": [{"start": 0, "title": "One"}]}
    n = items.normalize(plex)
    assert n["source"] == "plex@abc" and n["resumeKey"] == "plex@abc:book/77"
    assert n["chapters"] == plex["chapters"] and n["parent"] == plex["parent"]
    focus = items.normalize({"id": "focus:rain", "kind": "focus", "playback": {"backend": "focus"}})
    assert focus and focus["capabilities"] == [] and items.legacy_kind(focus) == "stream"
    assert items.normalize({"id": "x:1", "playback": {"backend": "webamp"}}) is None  # nothing to fetch


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_normaliser_matches_python():
    script = (
        "import(process.argv[1]).then(m => {"
        " const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        " process.stdout.write(JSON.stringify(m.normalizeItems(input))); })"
    )
    res = subprocess.run(["node", "-e", script, (REPO / "web" / "items.js").as_uri()],
                         input=json.dumps(LEGACY), capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    py = items.normalize_list(LEGACY)
    assert js == py


def test_resume_migration():
    old = {TID: 120, "abc": 5, f"local:track:{'f' * 16}": 9}
    assert items.migrate_resume(old) == {f"local:track:{TID}": 120, "abc": 5,
                                         f"local:track:{'f' * 16}": 9}
    # a value already under the new key wins over a stale bare one
    both = {f"local:track:{TID}": 50, TID: 10}
    assert items.migrate_resume(both) == {f"local:track:{TID}": 50}


def test_old_state_file_upgrades_on_load(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "state.json").write_text(json.dumps({
        "resume": {TID: 77}, "playlist": LEGACY[:2] + [LEGACY[4]], "volume": 33}))
    code = (
        "import json; from revamp_core import store; st = store.get_state();"
        "print(json.dumps({'resume': st['resume'], 'ids': [i['id'] for i in st['playlist']],"
        " 'kinds': [i['kind'] for i in st['playlist']], 'r': store.get_resume('local:track:%s')}))" % TID
    )
    env = dict(os.environ, REVAMP_HOME=str(data))
    res = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["resume"] == {f"local:track:{TID}": 77}
    assert out["ids"] == [f"local:track:{TID}", f"local:track:{TID}", "radio:station:somafm-x"]
    assert out["kinds"] == ["audiobook", "music", "stream"]
    assert out["r"] == 77


def test_remote_items_survive_load_without_source(tmp_path):
    """A saved canonical remote item remains usable before its source registers."""
    data = tmp_path / "data"
    data.mkdir()
    remote = {"id": "plex@abc:track/5531", "kind": "audiobook", "title": "Ch 1",
              "source": "plex@abc", "resumeKey": "plex@abc:book/77",
              "capabilities": ["seek", "resume"],
              "playback": {"backend": "webamp",
                           "url": "/api/sources/plex@abc/stream/track%2F5531"}}
    (data / "state.json").write_text(json.dumps({"playlist": [remote]}))
    code = (
        "import json; from revamp_core import store; st = store.get_state();"
        "print(json.dumps({'ids': [i['id'] for i in st['playlist']],"
        " 'urls': [i['playback']['url'] for i in st['playlist']]}))"
    )
    env = dict(os.environ, REVAMP_HOME=str(data))
    res = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["ids"] == ["plex@abc:track/5531"]
    assert out["urls"] == ["/api/sources/plex@abc/stream/track%2F5531"]


# ---------------------------------------------------------------------------
# registry + generic routes (the shared source contract)
# ---------------------------------------------------------------------------

def _wait_tracks(client, n=1):
    deadline = time.time() + 15
    while time.time() < deadline:
        _, _, res = client.get("/api/sources/local/browse")
        if res.get("total", 0) >= n:
            return res
        time.sleep(0.2)
    raise AssertionError("library never scanned")


def test_sources_listed(server):
    code, _, out = server.client.get("/api/sources")
    assert code == 200
    by = {s["id"]: s for s in out}
    assert {"local", "radio", "url"} <= set(by)
    assert {"browse", "search", "stream"} <= set(by["local"]["features"])
    assert by["radio"]["kinds"] == ["stream"]
    code, _, _ = server.client.get("/api/sources/nope/browse")
    assert code == 404


def test_local_source_contract(server):
    _wait_tracks(server.client)
    item = check_source(server.client, "local")
    assert item["playback"]["url"].startswith("/api/media/")
    # the generic stream route serves the same file
    tid = item["id"].rsplit(":", 1)[1]
    code, headers, body = server.client.get(f"/api/sources/local/stream/{tid}",
                                            headers={"Range": "bytes=2-5"}, raw=True)
    assert code == 206 and len(body) == 4
    _, _, hits = server.client.get("/api/sources/local/search?q=" + item["title"].split()[0])
    assert any(h["id"] == item["id"] for h in hits["items"])


def test_radio_source_browse(server):
    code, _, out = server.client.get("/api/sources/radio/browse/presets")
    assert code == 200 and out["items"]
    st = out["items"][0]
    assert st["id"].startswith("radio:station:") and st["kind"] == "stream"
    assert st["playback"]["url"].startswith("/api/radio/stream?url=")
    code, _, again = server.client.get("/api/sources/radio/item/" + st["id"].split(":", 1)[1])
    assert code == 200 and again["id"] == st["id"]


class _Upstream(BaseHTTPRequestHandler):
    """A fake media server: needs its token, honours Range."""
    BODY = bytes(range(256)) * 8

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("X-Fake-Token") != "sekrit":
            self.send_response(401)
            self.end_headers()
            return
        body, code = self.BODY, 200
        rng = self.headers.get("Range")
        extra = {}
        if rng:
            a, b = rng.split("=", 1)[1].split("-")
            a, b = int(a), int(b or len(body) - 1)
            extra["Content-Range"] = f"bytes {a}-{b}/{len(body)}"
            body, code = body[a:b + 1], 206
        self.send_response(code)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


class FakeSource(sources.Source):
    """A proxied remote source owning its progress, like Plex will."""
    type = "fake"
    kinds = ("audiobook",)
    owns_progress = True

    def __init__(self, instance, upstream):
        self.id = instance
        self.upstream = upstream
        self.pushed = []
        self.remote = {}

    def _item(self, key):
        return {"id": f"{self.id}:track/{key}", "source": self.id, "kind": "audiobook",
                "title": f"Chapter {key}", "duration": 3000,
                "playback": {"backend": "webamp", "url": sources.stream_url(self.id, f"track/{key}")},
                "resumeKey": f"{self.id}:book/1", "capabilities": ["seek", "resume"],
                "parent": f"{self.id}:book/1"}

    def browse(self, path, params):
        return {"total": 2, "items": [self._item("1"), self._item("2")]}

    def item(self, media_id):
        return self._item(media_id.split("/", 1)[1]) if media_id.startswith("track/") else None

    def stream(self, key, request):
        request.pipe(self.upstream + "/" + key, headers={"X-Fake-Token": "sekrit"})

    def get_progress(self, key):
        return self.remote.get(key)

    def set_progress(self, key, position):
        self.pushed.append((key, position))


@pytest.fixture()
def fake_source(server):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    src = sources.register(FakeSource("fake@srv1", f"http://127.0.0.1:{up.server_address[1]}"))
    yield src
    sources.unregister(src.id)
    up.shutdown()


def test_proxied_source_contract(server, fake_source):
    item = check_source(server.client, "fake@srv1")
    assert item["resumeKey"] == "fake@srv1:book/1"  # a book resumes by the book, not the file
    # progress went local first, then to the source
    assert ("fake@srv1:book/1", 321) in fake_source.pushed
    # full GET through the proxy; the upstream token never shows up in the reply
    code, headers, body = server.client.get(item["playback"]["url"], raw=True)
    assert code == 200 and body == _Upstream.BODY
    assert "sekrit" not in json.dumps(headers)
    # newer remote progress wins over the local place
    server.client.post("/api/resume", {"key": "fake@srv1:book/1", "position": 100})
    fake_source.remote["fake@srv1:book/1"] = (900, time.time() + 5)
    _, _, got = server.client.get("/api/resume?key=fake@srv1:book/1")
    assert got["position"] == 900
    fake_source.remote["fake@srv1:book/1"] = (5, 1.0)  # older: local wins
    _, _, got = server.client.get("/api/resume?key=fake@srv1:book/1")
    assert got["position"] == 100


def test_bare_ref_resolves_at_load(server, fake_source):
    n = sources.normalize({"id": "fake@srv1:track/9", "title": "x"})
    assert n["playback"] == {"backend": "webamp", "url": "/api/sources/fake@srv1/stream/track%2F9"}
    assert n["source"] == "fake@srv1"
    assert sources.normalize({"id": "nosuch:1"}) is None  # no source to resolve it


def test_resume_routes(server):
    c = server.client
    code, _, _ = c.post("/api/resume", {"key": "nosuch:thing", "position": 5})
    assert code == 404
    code, _, _ = c.post("/api/resume", {"position": 5})
    assert code == 400
    code, _, _ = c.post("/api/resume", {"key": "url:abcd1234", "position": "x"})
    assert code == 400
    # the legacy library route writes under the item id
    res = _wait_tracks(c)
    tid = res["items"][0]["id"].rsplit(":", 1)[1]
    assert c.post("/api/library/resume", {"track_id": tid, "position": 61})[0] == 200
    _, _, st = c.get("/api/state")
    assert st["resume"][f"local:track:{tid}"] == 61 and tid not in st["resume"]
    _, _, got = c.get(f"/api/resume?key=local:track:{tid}")
    assert got["position"] == 61


# ---------------------------------------------------------------------------
# playback controller
# ---------------------------------------------------------------------------

def _beat(client, **np):
    base = {"title": "T", "status": "PLAYING", "position": 1, "duration": 10}
    return client.post("/api/heartbeat", {"now_playing": dict(base, **np)})


def test_controller_without_player_broadcasts(server):
    code, _, res = server.client.post("/api/command", {"action": "seek", "seconds": 5})
    assert code == 200 and res["ok"] is True and res["backend"] == "webamp"
    assert res["accepted"] is (res["delivered"] > 0) and res["applied"] is False


def test_controller_reports_unsupported(server):
    c = server.client
    _beat(c, kind="stream", url="https://x/s", item_id="radio:station:x", source="radio",
          media_kind="stream", backend="webamp",
          capabilities=playback.effective_capabilities(playback.WEBAMP_CAPS, []))
    _, _, st = c.get("/api/state")
    pb = st["live"]["playback"]
    assert pb["item_id"] == "radio:station:x" and pb["kind"] == "stream" and pb["status"] == "playing"
    assert "seek" not in pb["capabilities"] and "next" in pb["capabilities"]
    assert st["live"]["now_playing"]["kind"] == "stream"  # the old fields are kept
    code, _, res = c.post("/api/command", {"action": "seek", "seconds": 5})
    assert code == 200 and res["ok"] is False and res["unsupported"] is True
    assert "seek" not in res["capabilities"]
    code, _, res = c.post("/api/command", {"action": "next"})
    assert res["ok"] is True
    code, _, res = c.post("/api/command", {"action": "toast", "text": "hi"})
    assert res["ok"] is True  # app actions are never gated
    _, _, cur = c.get("/api/playback")
    assert cur["active"] == "webamp" and "seek" not in cur["capabilities"]
    assert any(b["id"] == "webamp" for b in cur["backends"])


def test_controller_old_page_heartbeat_assumes_webamp(server):
    _beat(server.client, kind="track", url="/api/media/x")  # no new fields at all
    _, _, cur = server.client.get("/api/playback")
    assert cur["active"] == "webamp" and "seek" in cur["capabilities"]
    _, _, res = server.client.post("/api/command", {"action": "seek", "seconds": 3})
    assert res["ok"] is True


def test_controller_routes_by_backend(server):
    c = server.client
    # Focus is a built-in browser backend and is delivered over SSE like Webamp.
    _, _, res = c.post("/api/command", {"action": "load", "items": [
        {"id": "focus:rain", "kind": "focus", "playback": {"backend": "focus"}}]})
    assert res["ok"] is True and res["backend"] == "focus"

    class Remote(playback.Backend):
        remote = True

        def __init__(self):
            super().__init__("spotify", ["play", "pause", "toggle", "next", "prev", "volume"])
            self.got = []

        def command(self, cmd):
            self.got.append(cmd["action"])
            return {"applied": True}

    remote = playback.register_backend(Remote())
    try:
        events = []

        def watch():
            events.extend(server.sse_lines(seconds=2.0, want=1))
        t = threading.Thread(target=watch)
        t.start()
        time.sleep(0.4)
        c.post("/api/heartbeat", {"now_playing": {"backend": "spotify", "item_id": "spotify:track:1",
                                                   "status": "PLAYING", "media_kind": "music",
                                                   "capabilities": remote.capabilities}})
        _, _, res = c.post("/api/command", {"action": "next"})
        assert res["ok"] is True and res["backend"] == "spotify" and res["applied"] is True
        _, _, res = c.post("/api/command", {"action": "seek", "seconds": 4})
        assert res["unsupported"] is True
        t.join()
        assert remote.got == ["next"]
        assert not [d for e, d in events if e == "command"]  # the remote ran it, the page didn't
    finally:
        with playback._LOCK:
            playback._BACKENDS.pop("spotify", None)


def test_load_command_sends_canonical_items(server):
    events = []

    def watch():
        events.extend(server.sse_lines(seconds=2.5, want=1))
    t = threading.Thread(target=watch)
    t.start()
    time.sleep(0.4)
    code, _, res = server.client.post("/api/command", {"action": "load", "items": LEGACY[4:6]})
    t.join()
    assert res["ok"] is True
    cmd = next(d for e, d in events if e == "command")
    assert [i["id"] for i in cmd["items"]][0] == "radio:station:somafm-x"
    assert cmd["backend"] == "webamp" and all("playback" in i for i in cmd["items"])


# ---------------------------------------------------------------------------
# playlists keep canonical items
# ---------------------------------------------------------------------------

def test_playlists_store_canonical_items(server, fake_source):
    c = server.client
    res = _wait_tracks(c)
    local = res["items"][0]
    plex = fake_source._item("1")
    legacy_station = LEGACY[4]
    body = {"name": "Mixed", "items": [local, local, plex, legacy_station]}
    code, _, pl = c.post("/api/playlists", body)
    assert code == 201
    _, _, got = c.get(f"/api/playlists/{pl['id']}")
    ids = [i["id"] for i in got["items"]]
    assert ids == [local["id"], local["id"], plex["id"], "radio:station:somafm-x"]
    assert got["items"][2]["resumeKey"] == "fake@srv1:book/1"  # nothing lost on the way
    assert got["items"][2]["parent"] == "fake@srv1:book/1"
    # the file is still a plain M3U for other players; exports leave our lines out
    raw = (server.data_dir / "playlists" / f"{pl['id']}.m3u8").read_text()
    assert raw.startswith("#EXTM3U") and "#EXTREVAMP:" in raw
    code, _, text = c.get(f"/api/playlists/{pl['id']}/export?format=m3u8", raw=True)
    text = text.decode()
    assert "#EXTREVAMP" not in text
    lines = [x for x in text.splitlines() if x and not x.startswith("#")]
    assert lines[0].startswith(str(server.lib)) and lines[3] == "https://x.example/s?a=1"
    code, _, pls = c.get(f"/api/playlists/{pl['id']}/export?format=pls", raw=True)
    assert "File4=https://x.example/s?a=1" in pls.decode()
    # an old playlist (plain M3U) still loads, as canonical items
    (server.data_dir / "playlists" / "old.m3u8").write_text(
        f"#EXTM3U\n#EXTINF:3,A - B\n{local['playback']['url']}\nhttps://z.example/radio\n")
    _, _, old = c.get("/api/playlists/old")
    assert [i["source"] for i in old["items"]] == ["local", "radio"]
    assert old["items"][0]["id"] == local["id"]


def test_playlist_keeps_exact_name_order_and_duplicate_items(server):
    """A saved playlist is a user-owned ordered list, not a filename-derived view."""
    res = _wait_tracks(server.client, 2)
    first, second = res["items"][:2]
    name = "Road Trip: 1998!"
    code, _, created = server.client.post("/api/playlists", {
        "name": name, "items": [first, second, first],
    })
    assert code == 201
    pid = created["id"]
    assert created["name"] == name

    # Names, order, and repeated entries survive a real write + reload.
    code, _, listed = server.client.get("/api/playlists")
    assert code == 200
    assert next(row for row in listed if row["id"] == pid)["name"] == name
    code, _, loaded = server.client.get(f"/api/playlists/{pid}")
    assert code == 200 and loaded["name"] == name
    assert [item["id"] for item in loaded["items"]] == [first["id"], second["id"], first["id"]]

    # Editing queue order alone must retain the human-entered title.
    code, _, updated = server.client.put(f"/api/playlists/{pid}", {
        "items": [second, first, first],
    })
    assert code == 200 and updated["id"] == pid and updated["name"] == name
    code, _, reloaded = server.client.get(f"/api/playlists/{pid}")
    assert reloaded["name"] == name
    assert [item["id"] for item in reloaded["items"]] == [second["id"], first["id"], first["id"]]


def test_playlist_rename_keeps_stable_id_when_slug_does_not_change(server):
    created = server.client.post("/api/playlists", {"name": "My Mix", "items": []})[2]
    code, _, renamed = server.client.put(f"/api/playlists/{created['id']}", {
        "name": "MY mix!",
    })
    assert code == 200
    assert renamed["id"] == created["id"]
    assert renamed["name"] == "MY mix!"
    code, _, loaded = server.client.get(f"/api/playlists/{created['id']}")
    assert code == 200 and loaded["name"] == "MY mix!"


def test_playlist_rename_keeps_id_and_exact_name(server):
    """A rename keeps the id and stores the user's exact display name."""
    c = server.client
    _, _, created = c.post("/api/playlists", {"name": "Road Trip: 1998!", "items": []})
    code, _, renamed = c.put(f"/api/playlists/{created['id']}", {
        "name": "Summer Mix!!",
    })
    assert code == 200
    assert renamed["id"] == created["id"]  # renames keep the id
    assert renamed["name"] == "Summer Mix!!"
    code, _, loaded = c.get(f"/api/playlists/{renamed['id']}")
    assert code == 200
    assert loaded["id"] == renamed["id"]
    assert loaded["name"] == "Summer Mix!!"
