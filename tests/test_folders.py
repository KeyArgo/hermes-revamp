"""Library folders chosen in the app, the folder browser, and the music/audiobook split."""

from __future__ import annotations

import json
import os
import time

import pytest

from revamp_core import library


def _wait(client, path, pred, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        code, _, body = client.get(path)
        if code == 200 and pred(body):
            return body
        time.sleep(0.2)
    raise AssertionError(f"{path} never satisfied the condition: {body}")


def test_folders_default_to_config(server):
    code, _, body = server.client.get("/api/library/folders")
    assert code == 200
    assert body["custom"] is False
    assert [f["path"] for f in body["folders"]] == [str(server.lib)]
    assert body["folders"][0]["exists"] is True


def test_put_folders_saves_and_rescans(server, tmp_path):
    other = tmp_path / "other"
    (other / "Album").mkdir(parents=True)
    (other / "Album" / "Fresh.mp3").write_bytes(b"\x00" * 256)
    code, _, body = server.client.put("/api/library/folders", {"folders": [str(other)]})
    assert code == 200, body
    assert body["custom"] is True
    assert [f["path"] for f in body["folders"]] == [str(other)]
    # persisted in the data dir, survives a re-read
    saved = json.loads((server.data_dir / "settings.json").read_text())
    assert saved["library_folders"] == [{"path": str(other), "kind": "music"}]
    assert "library_dirs" not in saved
    out = _wait(server.client, "/api/library/tracks?limit=50",
                lambda b: [t["title"] for t in b["tracks"]] == ["Fresh"])
    assert out["total"] == 1
    _, _, status = server.client.get("/api/library/status")
    assert status["roots"] == [str(other)]


def test_put_folders_expands_home_and_dedupes(server):
    home = os.path.expanduser("~")
    code, _, body = server.client.put("/api/library/folders",
                                      {"folders": ["~", home, home + "/"]})
    assert code == 200, body
    assert [f["path"] for f in body["folders"]] == [home]


def test_put_folders_rejects_bad_input(server, tmp_path):
    for bad in ({"folders": "nope"}, {"folders": ["relative/dir"]},
                {"folders": [str(tmp_path / "missing")]},
                {"folders": [str(server.lib / "cover.jpg")]}):
        code, _, _ = server.client.put("/api/library/folders", bad)
        assert code == 400, bad
    # nothing was saved
    assert not (server.data_dir / "settings.json").exists()


def test_empty_folder_list_means_no_folders(server, tmp_path):
    other = tmp_path / "x"
    other.mkdir()
    server.client.put("/api/library/folders", {"folders": [str(other)]})
    code, _, body = server.client.put("/api/library/folders", {"folders": []})
    assert code == 200
    assert body["custom"] is True
    assert body["folders"] == []
    _, _, got = server.client.get("/api/library/folders")
    assert got == {"custom": True, "folders": []}
    _, _, status = server.client.get("/api/library/status")
    assert status["roots"] == []
    saved = json.loads((server.data_dir / "settings.json").read_text())
    assert saved["library_folders"] == []
    assert "library_dirs" not in saved


def test_folder_kind_overrides_the_guess(server, tmp_path):
    music = tmp_path / "as-music"
    books = tmp_path / "as-books"
    for d in (music, books):
        d.mkdir()
    (music / "Book.m4b").write_bytes(b"\x00" * 256)
    (books / "Song.mp3").write_bytes(b"\x00" * 256)
    code, _, body = server.client.put("/api/library/folders", {"folders": [
        {"path": str(music), "kind": "music"},
        {"path": str(books), "kind": "books"},
    ]})
    assert code == 200, body
    assert [f["kind"] for f in body["folders"]] == ["music", "books"]
    out = _wait(server.client, "/api/library/tracks?limit=50",
                lambda b: {t["title"] for t in b["tracks"]} == {"Book", "Song"})
    kinds = {t["title"]: t["kind"] for t in out["tracks"]}
    assert kinds == {"Book": "music", "Song": "audiobook"}


def test_a_folder_cannot_be_both(server, tmp_path):
    d = tmp_path / "shared"
    d.mkdir()
    for folders in ([{"path": str(d), "kind": "music"}, {"path": str(d), "kind": "books"}],
                    [{"path": str(d), "kind": "both"}]):
        code, _, _ = server.client.put("/api/library/folders", {"folders": folders})
        assert code == 400, folders
    link = tmp_path / "shortcut"
    link.symlink_to(d)
    code, _, _ = server.client.put("/api/library/folders", {"folders": [
        {"path": str(d), "kind": "music"}, {"path": str(link), "kind": "books"}]})
    assert code == 400  # a shortcut to a Music folder is that Music folder


def test_old_library_dirs_key_reads_as_music(tmp_path, monkeypatch):
    from revamp_core import config

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"library_dirs": ["/some/path"]}))
    assert config.library_folders() == [{"path": "/some/path", "kind": "music"}]
    assert config.library_dirs() == ["/some/path"]
    assert config.library_dirs_custom() is True


def test_explicitly_empty_folders_does_not_fall_back(tmp_path, monkeypatch):
    from revamp_core import config

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"library_folders": []}))
    assert config.library_folders() == []
    assert config.library_dirs() == []
    assert config.library_dirs_custom() is True


def test_put_folders_rejects_bad_kind(server, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    code, _, _ = server.client.put("/api/library/folders",
                                   {"folders": [{"path": str(d), "kind": "video"}]})
    assert code == 400
    assert not (server.data_dir / "settings.json").exists()


def test_fs_dirs_lists_only_visible_directories(server, tmp_path):
    root = tmp_path / "browse"
    (root / "B Music").mkdir(parents=True)
    (root / "a books").mkdir()
    (root / ".hidden").mkdir()
    (root / "file.mp3").write_bytes(b"x")
    code, _, body = server.client.get(f"/api/fs/dirs?path={root}")
    assert code == 200, body
    assert body["path"] == str(root)
    assert body["parent"] == str(tmp_path)
    assert [d["name"] for d in body["dirs"]] == ["a books", "B Music"]
    assert body["dirs"][0]["path"] == str(root / "a books")


def test_fs_dirs_defaults_to_home_and_rejects_files(server):
    code, _, body = server.client.get("/api/fs/dirs")
    assert code == 200 and body["path"] == os.path.expanduser("~")
    code, _, _ = server.client.get(f"/api/fs/dirs?path={server.lib / 'cover.jpg'}")
    assert code == 400
    code, _, body = server.client.get("/api/fs/dirs?path=/")
    assert code == 200 and body["parent"] is None


def test_kind_classification():
    k = library.kind_of
    assert k({"path": "/x/Book.m4b"}) == "audiobook"
    assert k({"path": "/x/a.mp3", "genre": "Audiobook"}) == "audiobook"
    assert k({"path": "/x/a.mp3", "genre": "Spoken Word"}) == "audiobook"
    assert k({"path": "/x/a.mp3", "genre": "Podcast"}) == "audiobook"
    assert k({"path": "/x/a.mp3", "duration": 3600}) == "audiobook"
    assert k({"path": "/x/a.mp3", "duration": 240, "genre": "Rock"}) == "music"
    assert k({"path": "/x/a.flac"}) == "music"
    # a long classical/DJ set with a music genre stays music
    assert k({"path": "/x/set.mp3", "duration": 3600, "genre": "Electronic"}) == "music"


def _books_folder(server, tmp_path):
    """The default library as Music plus a separate Books folder."""
    books = tmp_path / "books"
    books.mkdir()
    code, _, body = server.client.put("/api/library/folders", {"folders": [
        {"path": str(server.lib), "kind": "music"}, {"path": str(books), "kind": "books"}]})
    assert code == 200, body
    return books


def test_tracks_filter_by_kind(server, tmp_path):
    books_dir = _books_folder(server, tmp_path)
    (books_dir / "Story.m4b").write_bytes(b"\x00" * 256)
    server.client.post("/api/library/rescan")
    _wait(server.client, "/api/library/tracks?kind=audiobook",
          lambda b: b["total"] == 1)
    _, _, books = server.client.get("/api/library/tracks?kind=audiobook")
    assert books["tracks"][0]["title"] == "Story"
    assert books["tracks"][0]["kind"] == "audiobook"
    _, _, music = server.client.get("/api/library/tracks?kind=music&limit=50")
    assert music["total"] >= 1
    assert all(t["kind"] == "music" for t in music["tracks"])
    _, _, everything = server.client.get("/api/library/tracks?limit=50")
    assert everything["total"] == music["total"] + 1


def test_book_resume_positions(server, tmp_path):
    books_dir = _books_folder(server, tmp_path)
    (books_dir / "Book One.m4b").write_bytes(b"\x00" * 256)
    (books_dir / "Book Two.m4b").write_bytes(b"\x00" * 256)
    server.client.post("/api/library/rescan")
    books = _wait(server.client, "/api/library/tracks?kind=audiobook",
                  lambda b: b["total"] == 2)
    ids = {t["title"]: t["id"] for t in books["tracks"]}

    code, _, body = server.client.post("/api/library/resume",
                                       {"track_id": ids["Book One"], "position": 42.7})
    assert code == 200 and body == {"ok": True}
    code, _, _ = server.client.post("/api/library/resume",
                                    {"track_id": ids["Book Two"], "position": 7})
    assert code == 200

    out = _wait(server.client, "/api/library/tracks?kind=audiobook",
                lambda b: b["total"] == 2 and all("resume" in t for t in b["tracks"]))
    assert {t["title"]: t["resume"] for t in out["tracks"]} == {
        "Book One": 42, "Book Two": 7}

    # Clearing one book leaves the other's position alone.
    code, _, _ = server.client.post("/api/library/resume",
                                    {"track_id": ids["Book One"], "position": None})
    assert code == 200
    _, _, out = server.client.get("/api/library/tracks?kind=audiobook")
    assert {t["title"]: t.get("resume") for t in out["tracks"]} == {
        "Book One": None, "Book Two": 7}

    code, _, _ = server.client.post("/api/library/resume",
                                    {"track_id": "nope", "position": 5})
    assert code == 404


def test_failed_save_is_an_error_not_a_scan(server, tmp_path, monkeypatch):
    from revamp_core import config

    def boom(data):
        raise OSError("disk full")
    monkeypatch.setattr(config, "_save_settings", boom)
    other = tmp_path / "o"
    other.mkdir()
    started = []
    monkeypatch.setattr(library, "scan_async", lambda roots=None: started.append(roots))
    code, _, body = server.client.put("/api/library/folders", {"folders": [str(other)]})
    assert code == 500 and "disk full" in json.dumps(body)
    assert started == []


def test_older_scan_cannot_overwrite_newer(tmp_path, monkeypatch):
    """Switch folders while a slow scan of the old ones runs: the old scan must not win."""
    import threading
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    (old / "Old Song.mp3").write_bytes(b"\x00" * 256)
    (new / "New Song.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    release = threading.Event()
    real_walk = library._walk

    def walk(root, visited, out):
        if root.name == "old":
            release.wait(10)  # the old scan is slow
        real_walk(root, visited, out)
    monkeypatch.setattr(library, "_walk", walk)

    library.scan_async([str(old)])
    library.scan_async([str(new)])
    deadline = time.time() + 10
    while library.is_scanning() and time.time() < deadline:
        time.sleep(0.05)
    assert not library.is_scanning()  # the newest scan finished; the stuck old one doesn't count
    assert [t["title"] for t in library.all_tracks()] == ["New Song"]
    release.set()
    time.sleep(0.5)
    assert [t["title"] for t in library.all_tracks()] == ["New Song"]


def test_direct_scan_is_numbered_too(tmp_path, monkeypatch):
    """A blocking scan started during a background one becomes the newest and is reported as scanning."""
    import threading
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "A Song.mp3").write_bytes(b"\x00" * 256)
    (b / "B Song.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    hold = {"a": threading.Event(), "b": threading.Event()}
    real_walk = library._walk

    def walk(root, visited, out):
        if root.name in hold:
            hold[root.name].wait(10)
        real_walk(root, visited, out)
    monkeypatch.setattr(library, "_walk", walk)
    library.scan_async([str(a)])
    direct = threading.Thread(target=library.scan_sync, args=([str(b)],))
    direct.start()
    time.sleep(0.2)
    hold["a"].set()  # the older background scan finishes first: superseded, not committed
    time.sleep(0.3)
    assert library.is_scanning()  # the direct scan is still going
    assert library.all_tracks() == []
    hold["b"].set()
    direct.join(5)
    assert not library.is_scanning()
    assert [t["title"] for t in library.all_tracks()] == ["B Song"]


def test_scan_that_cannot_start_does_not_stick(monkeypatch):
    import threading

    def no_threads(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", no_threads)
    with pytest.raises(RuntimeError):
        library.scan_async(["/nonexistent"])
    assert not library.is_scanning()


def test_scan_publishes_tracks_while_it_runs(tmp_path, monkeypatch):
    """Big network folders: songs show up (and progress counts) before the scan ends."""
    import threading
    lib = tmp_path / "nas"
    lib.mkdir()
    for i in range(40):
        (lib / f"Song {i:02d}.mp3").write_bytes(b"\\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "PUBLISH_EVERY", 0.1)
    gate = threading.Event()
    real = library._entry_for

    def slow(path, cached):
        if path.endswith("Song 30.mp3"):
            gate.wait(10)  # the scan stalls part-way, like a slow NAS file
        return real(path, cached)
    monkeypatch.setattr(library, "_entry_for", slow)
    library.scan_async([str(lib)])
    deadline = time.time() + 10
    while len(library.all_tracks()) < 20 and time.time() < deadline:
        time.sleep(0.05)
    assert library.is_scanning()
    assert 20 <= len(library.all_tracks()) < 40  # partial results are listed
    prog = library.scan_progress()
    assert prog["total"] == 40 and 0 < prog["done"] < 40
    gate.set()
    while library.is_scanning() and time.time() < deadline:
        time.sleep(0.05)
    assert len(library.all_tracks()) == 40
    assert library.scan_progress() == {"done": 40, "total": 40}


def test_slow_file_does_not_block_others_and_errors_are_isolated(tmp_path, monkeypatch):
    import threading
    lib = tmp_path / "nas2"
    lib.mkdir()
    for i in range(30):
        (lib / f"Song {i:02d}.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "PUBLISH_EVERY", 0.1)
    gate = threading.Event()
    real = library._entry_for

    def flaky(path, cached):
        if path.endswith("Song 00.mp3"):
            gate.wait(10)  # the FIRST file is the slow one
        if path.endswith("Song 05.mp3"):
            raise RuntimeError("tag reader crashed")
        return real(path, cached)
    monkeypatch.setattr(library, "_entry_for", flaky)
    library.scan_async([str(lib)])
    deadline = time.time() + 10
    while len(library.all_tracks()) < 28 and time.time() < deadline:
        time.sleep(0.05)
    assert len(library.all_tracks()) == 28  # everything but the slow one and the broken one
    gate.set()
    while library.is_scanning() and time.time() < deadline:
        time.sleep(0.05)
    assert len(library.all_tracks()) == 29  # the crash cost one file, not the scan


def test_superseded_scan_returns_without_waiting(tmp_path, monkeypatch):
    import threading
    old, new = tmp_path / "o", tmp_path / "n"
    old.mkdir(); new.mkdir()
    (old / "Stuck.mp3").write_bytes(b"\x00" * 256)
    (new / "Fresh.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    gate = threading.Event()
    real = library._entry_for
    monkeypatch.setattr(library, "_entry_for", lambda p, c: (gate.wait(10) if p.endswith("Stuck.mp3") else None) or real(p, c))
    returned = threading.Event()
    t = threading.Thread(target=lambda: (library.scan_sync([str(old)]), returned.set()))
    t.start()
    time.sleep(0.3)
    library.scan_sync([str(new)])
    assert returned.wait(4)  # the old scan gave up while its stuck read is still running
    assert [x["title"] for x in library.all_tracks()] == ["Fresh"]
    gate.set()
    t.join(5)


def test_books_folder_inside_a_music_folder(server, tmp_path):
    music = tmp_path / "nested"
    books = music / "Audiobooks"
    books.mkdir(parents=True)
    (music / "Song.mp3").write_bytes(b"\x00" * 256)
    (books / "Story.mp3").write_bytes(b"\x00" * 256)
    code, _, body = server.client.put("/api/library/folders", {"folders": [
        {"path": str(music), "kind": "music"}, {"path": str(books), "kind": "books"}]})
    assert code == 200, body
    out = _wait(server.client, "/api/library/tracks?limit=50",
                lambda b: b["total"] == 2)
    assert {t["title"]: t["kind"] for t in out["tracks"]} == {"Song": "music", "Story": "audiobook"}
    _, _, body = server.client.get("/api/library/folders")
    assert {f["kind"]: f["tracks"] for f in body["folders"]} == {"music": 1, "books": 1}


def test_artists_and_albums_filter_by_kind(server, tmp_path):
    books_dir = _books_folder(server, tmp_path)
    (books_dir / "Story.m4b").write_bytes(b"\x00" * 256)
    server.client.post("/api/library/rescan")
    _wait(server.client, "/api/library/tracks?kind=audiobook", lambda b: b["total"] == 1)
    _, _, all_artists = server.client.get("/api/library/artists")
    _, _, book_artists = server.client.get("/api/library/artists?kind=audiobook")
    _, _, music_artists = server.client.get("/api/library/artists?kind=music")
    assert sum(a["tracks"] for a in book_artists) == 1
    assert sum(a["tracks"] for a in music_artists) + 1 == sum(a["tracks"] for a in all_artists)
    _, _, book_albums = server.client.get("/api/library/albums?kind=audiobook")
    assert sum(a["tracks"] for a in book_albums) == 1
