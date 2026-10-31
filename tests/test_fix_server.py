"""Regression tests for confirmed server bug-hunt findings."""

from __future__ import annotations

import json
from io import BytesIO
import socket
import threading
import time

import pytest


def test_local_media_is_written_in_bounded_chunks(monkeypatch):
    """A full-file or open-ended range must not allocate the entire media file."""
    from revamp_core import server

    class File:
        def __init__(self):
            self.left = 200_000
            self.read_sizes = []

        def read(self, size=-1):
            self.read_sizes.append(size)
            take = self.left if size < 0 else min(self.left, size)
            self.left -= take
            return b"x" * take

        def seek(self, _offset):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    file = File()
    monkeypatch.setattr(server.os.path, "getsize", lambda _path: 200_000)
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: file)
    handler = object.__new__(server.Handler)
    handler.headers = {}
    handler.command = "GET"
    handler.wfile = BytesIO()
    handler.send_response = lambda *_args: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    handler.send_file("large.mp3")

    assert file.read_sizes
    assert all(0 < size <= 64 * 1024 for size in file.read_sizes)
    assert len(handler.wfile.getvalue()) == 200_000


def test_prune_keeps_all_fresh_snapshots(tmp_path):
    from revamp_core import backup

    now = time.time()
    for number in range(61):
        folder = tmp_path / f"snapshot-{number}"
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({
            "id": folder.name,
            "created": now - number * 600,
            "files": {},
        }))
    assert backup.prune(tmp_path) == 0
    assert len(backup.list_backups(tmp_path)) == 61


def test_snapshot_skips_unchanged_data(tmp_path):
    from revamp_core import backup

    data = tmp_path / "data"
    root = tmp_path / "backups"
    data.mkdir()
    (data / "state.json").write_text(json.dumps({"volume": 55}))
    assert backup.snapshot(data, root=root, now=1_700_000_000)
    assert backup.snapshot(data, root=root, now=1_700_000_600) is None
    assert len(backup.list_backups(root)) == 1


def test_failed_state_write_does_not_change_memory(tmp_path, monkeypatch):
    from revamp_core import store

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    monkeypatch.setattr(store, "_STATE", {"volume": 80, "live": {}})
    monkeypatch.setattr(store, "_atomic_write", lambda *_args: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(store.StoreWriteError):
        store.update({"volume": 33})
    assert store.get_state()["volume"] == 80


def test_resume_only_backup_is_not_empty(tmp_path):
    from revamp_core import backup

    (tmp_path / "state.json").write_text(json.dumps({"resume": {"local:track:abc": 12}}))
    assert backup.is_empty(tmp_path) is False


def test_corrupt_favorites_are_not_overwritten(tmp_path, monkeypatch):
    from revamp_core import radio

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    broken = tmp_path / "radio_favorites.json"
    broken.write_text("{not json")
    with pytest.raises(radio.RadioError):
        radio.add_favorite({"url": "https://example.test/live"})
    assert broken.read_text() == "{not json"


def test_bad_lyrics_cache_entry_is_ignored(tmp_path, monkeypatch):
    from revamp_core import lyrics

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    (tmp_path / "lyrics-cache.json").write_text(json.dumps({"bad": "record"}))
    lyrics._CACHE.clear()
    lyrics._loaded = False
    lyrics._load()
    assert lyrics._cache_get(("artist", "title", "", 0)) is None


def test_library_folder_limit_is_not_silently_truncated(tmp_path):
    from revamp_core import server

    folders = []
    for number in range(51):
        folder = tmp_path / str(number)
        folder.mkdir()
        folders.append(str(folder))
    assert len(server._clean_library_folders(folders)) == 51


def test_relative_import_uses_origin_directory(tmp_path, monkeypatch):
    from revamp_core import playlists

    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio")
    monkeypatch.setattr("revamp_core.library.get_track", lambda _id: None)
    item = playlists.item_from_line("song.mp3", tmp_path)
    assert item["path"] == str(song)


def test_export_uses_saved_offline_path():
    from revamp_core import playlists

    item = {"id": "local:track:aaaaaaaaaaaaaaaa", "source": "local", "kind": "music",
            "playback": {"backend": "webamp", "url": "/api/media/aaaaaaaaaaaaaaaa"},
            "path": "/music/offline.mp3"}
    assert playlists.item_path(item) == "/music/offline.mp3"


def test_startup_always_schedules_a_reconciliation_scan(tmp_path, monkeypatch):
    from revamp_core import backup, library, server

    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    scans = []

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("REVAMP_LIBRARY_DIRS", str(tmp_path / "music"))
    monkeypatch.setattr(server, "_started", False)
    monkeypatch.setattr(server, "_owned", False)
    monkeypatch.setattr(server, "_probe_health", lambda _port: None)
    monkeypatch.setattr(server, "_foreign_holder", lambda _port: False)
    monkeypatch.setattr(server, "get_token", lambda: "token")
    monkeypatch.setattr(server.window, "reap_orphan", lambda: False)
    monkeypatch.setattr(backup, "seed_index", lambda _data: False)
    monkeypatch.setattr(backup, "start_thread", lambda _data: None)
    monkeypatch.setattr(library, "load_index", lambda: None)
    monkeypatch.setattr(library, "all_tracks", lambda: [{"id": "saved-track"}])
    monkeypatch.setattr(library, "index_complete", lambda: True)
    monkeypatch.setattr(library, "scan_async", lambda roots: scans.append(roots))

    assert server.ensure_server(port) == port
    assert scans == [server.library_dirs()]


def test_playlist_id_export_opens(server):
    code, _, playlist = server.client.post("/api/playlists", {
        "name": "Export",
        "items": [{"kind": "stream", "title": "Station", "url": "https://example.test/live"}],
    })
    assert code == 201
    code, _, opened = server.client.get("/api/playlists/export")
    assert code == 200
    assert opened["id"] == playlist["id"] == "export"


def test_client_surfaces_server_http_error_body(server):
    from revamp_core.client import RevampClient, RevampUnavailable

    client = RevampClient(base=server.base, token=server.token)
    with pytest.raises(RuntimeError, match="HTTP 404: no such playlist") as exc_info:
        client.get("/api/playlists/not-a-playlist")
    assert not isinstance(exc_info.value, RevampUnavailable)


def test_korean_and_thai_substrings_match():
    from revamp_core import library

    assert library._matches({"title": "사랑한글"}, ["한글"])
    assert library._matches({"title": "เพลงไทยเพราะ"}, ["ไทย"])


def test_dashboard_port_is_clamped(monkeypatch):
    pytest.importorskip("fastapi")
    from dashboard import plugin_api

    monkeypatch.setenv("REVAMP_PORT", "99999")
    assert plugin_api._port() == 65535


def test_orphan_lease_survives_unverified_process(tmp_path, monkeypatch):
    from revamp_core import window

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    (tmp_path / window.LEASE).write_text(json.dumps({"pid": 999999, "ts": 0}))
    assert window.reap_orphan() is False
    assert (tmp_path / window.LEASE).exists()


def test_health_identifies_revamp_and_a_newer_server_can_take_over(tmp_path, monkeypatch):
    """Two REVamp servers on one free port hand over without mistaking either for foreign."""
    from revamp_core import server

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    # ensure_server() latches _started process-wide: an earlier test in the full
    # suite can leave it set, and the call then returns at once without handing over.
    monkeypatch.setattr(server, "_started", False)
    monkeypatch.setattr(server, "_owned", False)
    old = server.build_server(port=port, data=str(tmp_path), version="0.9.0", build="old", owner="plugin")
    old_thread = threading.Thread(target=old.serve_forever, daemon=True)
    old_thread.start()
    try:
        health = server._probe_health(port)
        assert health and health["app"] == "revamp"
        assert {"version", "build", "pid", "started_at"} <= set(health)
        assert server.ensure_server(port, owner="service", version="1.0.0", build="new") == port
        deadline = time.time() + 3
        while time.time() < deadline and server._probe_health(port).get("build") != "new":
            time.sleep(0.05)
        assert server._probe_health(port)["build"] == "new"
    finally:
        server._request_upgrade_shutdown(port, server.get_token())
        server._wait_for_port_release(port)
        server._started = False
        server._owned = False
        old.shutdown()
        old.server_close()
