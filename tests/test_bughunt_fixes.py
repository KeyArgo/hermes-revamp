"""Regression tests for the release bug hunt (Codex findings)."""

from __future__ import annotations

import http.client
import json
import threading
import time


def test_focus_settings_persist(server):
    code, _, _ = server.client.put("/api/state", {"focus": {"warmth": 0.2, "noise": 0.5}})
    assert code == 200
    _, _, st = server.client.get("/api/state")
    assert st.get("focus", {}).get("warmth") == 0.2


def test_playlist_put_rejects_bad_items_and_keeps_the_list(server):
    code, _, pl = server.client.post("/api/playlists", {"name": "Keep Me", "items": [
        {"kind": "stream", "title": "S", "url": "https://example.com/s.mp3"}]})
    assert code in (200, 201), pl
    pid = pl["id"]
    for bad in ({"items": {}}, {"items": "bad"}, {"name": 5}):
        code, _, _ = server.client.put(f"/api/playlists/{pid}", bad)
        assert code == 400, bad
    _, _, got = server.client.get(f"/api/playlists/{pid}")
    assert len(got["items"]) == 1


def test_playlist_names_keep_case_and_punctuation(server):
    code, _, pl = server.client.post("/api/playlists", {"name": "My MIX: Vol. 2!", "items": []})
    assert code in (200, 201)
    _, _, got = server.client.get(f"/api/playlists/{pl['id']}")
    assert got["name"] == "My MIX: Vol. 2!"
    _, _, lists = server.client.get("/api/playlists")
    assert any(p["name"] == "My MIX: Vol. 2!" for p in lists)


def test_huge_request_body_is_refused(server):
    host, port = server.base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("PUT", "/api/state")
    conn.putheader("X-Revamp-Token", server.token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(50 * 1024 * 1024))
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413


def test_rename_keeps_the_playlist_id(server):
    _, _, pl = server.client.post("/api/playlists", {"name": "Rename Me", "items": []})
    code, _, out = server.client.put(f"/api/playlists/{pl['id']}", {"name": "Road Trip — Vol. 2"})
    assert code == 200 and out["id"] == pl["id"] and out["name"] == "Road Trip — Vol. 2"
    code, _, got = server.client.get(f"/api/playlists/{pl['id']}")
    assert code == 200 and got["name"] == "Road Trip — Vol. 2"
    _, _, lists = server.client.get("/api/playlists")
    assert sum(1 for p in lists if p["id"] == pl["id"]) == 1


def test_playlist_lookup_uses_renamed_display_name(server):
    from revamp_core import playlists

    _, _, playlist = server.client.post("/api/playlists", {"name": "Road Trip", "items": []})
    code, _, _ = server.client.put(f"/api/playlists/{playlist['id']}", {"name": "Summer Mix"})
    assert code == 200
    found = playlists.get_playlist("Summer Mix")
    assert found is not None
    assert found["id"] == playlist["id"]


def test_playlist_creates_unique_ids_concurrently(server, monkeypatch):
    from revamp_core import playlists

    original_write = playlists._write_and_return

    def delayed_write(*args, **kwargs):
        time.sleep(0.02)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(playlists, "_write_and_return", delayed_write)
    barrier = threading.Barrier(8)
    created = []

    def create():
        barrier.wait()
        created.append(playlists.create_playlist("Mix"))

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 8
    assert len({playlist["id"] for playlist in created}) == 8


def test_pls_export_uses_local_track_path(server):
    _, _, library = server.client.get("/api/library/tracks")
    track = library["tracks"][0]
    item = {
        "kind": "track",
        "title": track["title"],
        "artist": track["artist"],
        "url": track["url"],
        "track_id": track["id"],
    }
    _, _, playlist = server.client.post("/api/playlists", {"name": "Local", "items": [item]})
    code, _, body = server.client.get(f"/api/playlists/{playlist['id']}/export?format=pls", raw=True)
    assert code == 200
    assert f"File1={track['path']}" in body.decode()


def test_filename_fallback_strips_track_separator(server):
    from revamp_core import tags

    assert tags._humanise("Artist - 07 - Title") == ("Title", "Artist", 7)


def test_mutagen_bitrate_is_reported_in_kbps(server, monkeypatch):
    from revamp_core import tags

    class Info:
        length = 1
        bitrate = 128000
        sample_rate = 44100
        channels = 2

    class AudioFile:
        info = Info()
        tags = {}

    class Mutagen:
        @staticmethod
        def File(path, easy=True):
            return AudioFile()

    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", True)
    monkeypatch.setattr(tags, "mutagen", Mutagen, raising=False)
    assert tags._tags_mutagen("song.mp3")["bitrate_kbps"] == 128


def test_relative_radio_playlist_entry_is_resolved(server, monkeypatch):
    from revamp_core import radio

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def read(size):
            return b"#EXTM3U\nstream.mp3\n"

    monkeypatch.setattr(radio, "_open", lambda *args: Response())
    assert radio.resolve_playlist_url("https://example.com/radio/list.m3u") == "https://example.com/radio/stream.mp3"


def test_custom_radio_favorite_ids_use_the_full_url(server):
    prefix = "https://example.com/" + "a" * 40
    first = server.client.post("/api/radio/favorites", {"name": "One", "url": prefix + "one"})[2]
    second = server.client.post("/api/radio/favorites", {"name": "Two", "url": prefix + "two"})[2]
    assert first["id"] != second["id"]
    _, _, favorites = server.client.get("/api/radio/favorites")
    assert {favorite["id"] for favorite in favorites} >= {first["id"], second["id"]}


def test_overlong_playlist_name_is_a_400_not_a_crash(server):
    code, _, _ = server.client.post("/api/playlists", {"name": "x" * 5000, "items": []})
    assert code == 400
    code, _, _ = server.client.post("/api/playlists", {"name": "ok", "items": "bad"})
    assert code == 400


def test_state_rejects_wrong_types_and_keeps_good_state(server):
    server.client.put("/api/state", {"volume": 40})
    for bad in ({"volume": "loud"}, {"playlist": "oops"}, {"shuffle": "yes"}, {"eq": [1, 2]},
                {"current_index": 1.5}, {"volume": True}, {"focus": 7}):
        code, _, _ = server.client.put("/api/state", bad)
        assert code == 400, bad
    _, _, st = server.client.get("/api/state")
    assert st["volume"] == 40 and isinstance(st["playlist"], list)
    code, _, _ = server.client.put("/api/state", {"volume": 55, "shuffle": True, "handoff": None})
    assert code == 200


def test_gateway_takes_over_when_the_short_lived_owner_exits(tmp_path, monkeypatch):
    """A cron run or CLI call can grab the port first and then exit, taking the
    server with it. The process that found it 'already healthy' must take over."""
    import socket
    import urllib.request
    from revamp_core import backup, library, server as srv

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("REVAMP_LIBRARY_DIRS", str(tmp_path))
    for name, val in (("_started", False), ("_watching", False), ("_owned", False), ("WATCH_INTERVAL", 0.2)):
        monkeypatch.setattr(srv, name, val)
    # The other owner looks healthy at first, then its process is gone.
    answers = iter([{"app": "revamp"}, {"app": "revamp"}])
    monkeypatch.setattr(srv, "_probe_health", lambda p: next(answers, None))
    monkeypatch.setattr(srv, "_foreign_holder", lambda p: False)
    # This exercises ownership handoff, not the background maintenance jobs.
    # Prevent them from outliving this test and changing the global library
    # scan generation during later tests.
    monkeypatch.setattr(backup, "start_thread", lambda _data: None)
    monkeypatch.setattr(library, "scan_async", lambda _roots: None)
    made = []
    real_server = srv.ThreadingHTTPServer

    def make_server(*args, **kwargs):
        httpd = real_server(*args, **kwargs)
        made.append(httpd)
        return httpd

    monkeypatch.setattr(srv, "ThreadingHTTPServer", make_server)

    assert srv.ensure_server(port) == port
    assert srv._owned is False  # trusted the other owner, but kept watching
    deadline = time.time() + 5
    while time.time() < deadline and not srv._owned:
        time.sleep(0.05)
    assert srv._owned is True
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
        assert json.loads(r.read())["app"] == "revamp"
    made[0].shutdown()
    made[0].server_close()
