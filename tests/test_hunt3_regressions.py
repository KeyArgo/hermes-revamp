"""Bug hunt 3 (2026-10-30): data loss and crashes found by the council, each pinned here."""
import json
import os
import shutil
import struct

import pytest

from revamp_core import chapters, library, lyrics, playlists, store, tags


def test_unplugged_library_folder_keeps_its_tracks(tmp_path, monkeypatch):
    # A NAS that was asleep during a scan used to be wiped from the index.
    nas, local = tmp_path / "nas", tmp_path / "local"
    nas.mkdir(); local.mkdir()
    (nas / "Far.mp3").write_bytes(b"\x00" * 256)
    (local / "Near.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    library.scan_sync([str(nas), str(local)])
    assert {t["title"] for t in library.all_tracks()} >= {"Far", "Near"}
    shutil.rmtree(nas)  # unplugged
    library.scan_sync([str(nas), str(local)])
    assert {t["title"] for t in library.all_tracks()} >= {"Far", "Near"}
    (local / "Near.mp3").unlink()  # a file really deleted from a reachable folder still goes
    library.scan_sync([str(nas), str(local)])
    assert "Near" not in {t["title"] for t in library.all_tracks()}


def test_superscript_digit_filename_does_not_abort_tags():
    assert tags._humanise("² Song")[0] == "² Song"
    assert tags._humanise("07 Song") == ("Song", "", 7)


def test_id3v23_extended_header_is_skipped_by_its_own_size(tmp_path):
    # v2.3 counts the extended header without its 4-byte size field.
    frame = b"TIT2" + struct.pack(">I", 6) + b"\x00\x00" + b"\x00Hello"
    ext = struct.pack(">I", 6) + b"\x00" * 6
    body = ext + frame
    def synch(n):
        return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])
    f = tmp_path / "a.mp3"
    f.write_bytes(b"ID3\x03\x00\x40" + synch(len(body)) + body + b"\xff\xfb\x90\x00" + b"\x00" * 400)
    assert tags.probe(str(f)).get("title") == "Hello"


def test_huge_cover_file_is_not_loaded(tmp_path, monkeypatch):
    song = tmp_path / "s.mp3"; song.write_bytes(b"\x00" * 64)
    big = tmp_path / "cover.jpg"; big.write_bytes(b"\xff\xd8" + b"\x00" * 64)
    monkeypatch.setattr(tags, "_MAX_ART", 16)
    assert tags.read_cover(str(song)) is None


def test_null_eq_does_not_break_loading_state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_STATE", None)
    (tmp_path / "state.json").write_text(json.dumps({"eq": None}), encoding="utf-8")
    assert store.get_state()["eq"]["bands"] == [0.0] * 10


def test_failed_playlist_save_keeps_the_old_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(playlists, "data_dir", lambda: tmp_path)
    pl = playlists.create_playlist("Keep", [{"url": "http://s.example/a.mp3", "title": "A"}])
    path = playlists.playlists_dir() / f"{pl['id']}.m3u8"
    before = path.read_bytes()
    real = os.replace
    def disk_full(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", disk_full)
    with pytest.raises(OSError):
        playlists._write_m3u(path, "Keep", [])
    monkeypatch.setattr(os, "replace", real)
    assert path.read_bytes() == before and b"a.mp3" in before


def test_a_failed_chapter_probe_is_tried_again(tmp_path, monkeypatch):
    book = tmp_path / "b.m4b"; book.write_bytes(b"\x00" * 32)
    monkeypatch.setattr(chapters, "_CACHE", type(chapters._CACHE)())
    calls = []
    monkeypatch.setattr(chapters.shutil, "which", lambda name: "/usr/bin/ffprobe")
    def run(*a, **k):
        calls.append(1)
        raise chapters.subprocess.TimeoutExpired("ffprobe", 1)
    monkeypatch.setattr(chapters.subprocess, "run", run)
    chapters.chapters_for(str(book)); chapters.chapters_for(str(book))
    assert len(calls) == 2, "a timeout was cached as 'no chapters' for good"


def test_lyrics_cache_survives_bad_timestamps_and_keeps_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(lyrics, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(lyrics, "_CACHE", {})
    key = ("a", "t", "al", 100)
    lyrics._CACHE[json.dumps(key, ensure_ascii=False)] = {"hit": False, "ts": "bad", "data": {}}
    assert lyrics._cache_get(key) is None
    lyrics._cache_put(key, {"lines": ["la"]}, hit=True)
    lyrics._cache_put(key, {}, hit=False)  # a racing miss
    assert lyrics._cache_get(key) == {"lines": ["la"]}


def test_reinstalling_a_skin_never_deletes_it(tmp_path, monkeypatch):
    from revamp_core import museum
    md5 = "a" * 32
    monkeypatch.setattr(museum, "_dir", lambda: tmp_path)
    (tmp_path / f"{md5}.wsz").write_bytes(b"PK skin")
    monkeypatch.setattr(museum, "_cached_file", lambda m: tmp_path / f"{m}.wsz")
    monkeypatch.setattr(museum, "check_skin", lambda data: None)
    def full(index):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(museum, "_save_index", full)
    with pytest.raises(museum.MuseumError):
        museum.install(md5, name="Mine")
    assert (tmp_path / f"{md5}.wsz").exists()


def test_health_check_treats_an_error_reply_as_unhealthy(monkeypatch):
    from revamp_core import client
    c = client.RevampClient.__new__(client.RevampClient)
    def boom(path):
        raise RuntimeError("HTTP 503")
    monkeypatch.setattr(c, "get", boom, raising=False)
    assert c.healthy() is False


def test_a_restore_that_fails_half_way_puts_the_current_files_back(tmp_path, monkeypatch):
    from revamp_core import backup
    data, root = tmp_path / "data", tmp_path / "bk"
    (data / "playlists").mkdir(parents=True)
    (data / "settings.json").write_text('{"v": "old"}'); (data / "state.json").write_text('{"likes": ["old"]}')
    snap = backup.snapshot(data, root=root)
    (data / "settings.json").write_text('{"v": "new"}'); (data / "state.json").write_text('{"likes": ["new"]}')
    real, n = backup._atomic_copy, []
    def flaky(src, dst):
        n.append(1)
        if len(n) == 2:
            raise OSError(28, "No space left on device")
        real(src, dst)
    monkeypatch.setattr(backup, "_atomic_copy", flaky)
    with pytest.raises(OSError):
        backup.restore(snap["id"], data, root=root)
    assert json.loads((data / "settings.json").read_text())["v"] == "new"
    assert json.loads((data / "state.json").read_text())["likes"] == ["new"]


def test_a_failed_restore_still_drops_the_cached_state(monkeypatch):
    monkeypatch.setattr(store, "_STATE", {"cached": True})
    def boom():
        raise OSError("disk")
    with pytest.raises(OSError):
        store.run_locked(boom)
    assert store._STATE is None


def test_learning_a_duration_mid_scan_keeps_the_checkpoint_unfinished(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    monkeypatch.setattr(library, "_INDEX", {"t1": {"id": "t1", "duration": None}})
    library.save_index(complete=False)  # a scan's checkpoint
    library.set_learned_duration("t1", 200.0)
    saved = json.loads(library._index_path().read_text())
    assert saved["complete"] is False and saved["tracks"]["t1"]["duration"] == 200.0


def test_a_duration_learned_during_a_scan_survives_it(tmp_path, monkeypatch):
    # The scan copies each entry as it reads it; a duration the player learns for an
    # already-read song before the scan ends was thrown away by the final swap.
    lib = tmp_path / "lib"; lib.mkdir()
    (lib / "A.mp3").write_bytes(b"\x00" * 256)
    (lib / "B.mp3").write_bytes(b"\x00" * 256)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "SCAN_WORKERS", 1)  # A is read (and copied) before B
    library.scan_sync([str(lib)])
    a = library.track_id_for(str(lib / "A.mp3"))
    real = library._entry_for
    def learn_meanwhile(p, c):
        if p.endswith("B.mp3"):
            library.set_learned_duration(a, 123.0)
        return real(p, c)
    monkeypatch.setattr(library, "_entry_for", learn_meanwhile)
    library.scan_sync([str(lib)])
    assert library._INDEX[a]["duration"] == 123.0


def test_mutagen_track_numbers_written_as_n_of_total(monkeypatch):
    class Fake:
        info = None
        tags = {"tracknumber": ["3/12"], "discnumber": ["1/2"], "title": ["T"]}
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", True)
    monkeypatch.setattr(tags, "mutagen", type("M", (), {"File": staticmethod(lambda p, easy=True: Fake())}), raising=False)
    out = tags._tags_mutagen("x.flac")
    assert (out["track_no"], out["disc_no"]) == (3, 1)


def _box(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def test_mp4_box_flood_is_cut_short():
    import io
    boxes = _box(b"free", b"") * 30000
    meta = {}
    tags._mp4_walk(io.BytesIO(boxes), len(boxes), meta, False)
    assert meta["_mp4_boxes"] <= 20001


def test_oversized_mp4_cover_is_skipped_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(tags, "_MAX_ART", 64)
    data = _box(b"data", struct.pack(">I", 13) + b"\x00" * 4 + b"\xff\xd8" + b"\x01" * 200)
    ilst = _box(b"ilst", _box(b"covr", data))
    moov = _box(b"moov", _box(b"udta", _box(b"meta", b"\x00" * 4 + ilst)))
    f = tmp_path / "a.m4a"
    f.write_bytes(_box(b"ftyp", b"M4A \x00\x00\x00\x00") + moov)
    assert tags.read_cover(str(f)) is None


def test_page_actions_reach_the_page_while_a_remote_service_plays(monkeypatch):
    from revamp_core import playback
    sent, remote_got = [], []
    class Remote:
        id = "spot"; remote = True; capabilities = ["play", "pause"]
        def command(self, cmd): remote_got.append(cmd["action"]); return {}
    monkeypatch.setattr(playback, "current", lambda: {"active": "spot", "capabilities": ["play", "pause"]})
    monkeypatch.setattr(playback, "get_backend", lambda bid: Remote() if bid == "spot" else None)
    for action in ("sleep", "settings", "queue_clear"):
        code, reply = playback.dispatch({"action": action}, lambda kind, cmd: sent.append(cmd["action"]) or 1)
        assert code == 200 and reply["ok"]
    playback.dispatch({"action": "pause"}, lambda kind, cmd: sent.append(cmd["action"]) or 1)
    assert sent == ["sleep", "settings", "queue_clear"] and remote_got == ["pause"]


def test_an_old_window_exiting_does_not_hand_off_over_a_new_one(monkeypatch):
    from revamp_core import window
    handed = []
    monkeypatch.setattr(window, "_handoff_from_live", lambda: handed.append(1))
    monkeypatch.setattr(window, "_clear_lease", lambda: None)
    class P:
        def wait(self, *a): return 0
    old, new = P(), P()
    monkeypatch.setattr(window, "_proc", new)
    monkeypatch.setattr(window, "_closing", None)
    window._watch(old)
    assert handed == [] and window._proc is new


def test_a_garbled_desktop_entry_does_not_stop_the_window(tmp_path, monkeypatch):
    from revamp_core import window
    monkeypatch.setattr(window.sys, "platform", "linux")
    monkeypatch.setattr(window.Path, "home", classmethod(lambda cls: tmp_path))  # never the real home
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path / "data")
    apps = tmp_path / ".local" / "share" / "applications"; apps.mkdir(parents=True)
    for name in window.LINUX_DESKTOP_ENTRIES:
        (apps / name).write_bytes(b"\xff\xfe garbage")
    window.install_linux_desktop_identity()  # must not raise
    assert all((apps / n).read_text(encoding="utf-8").startswith("[Desktop Entry]") for n in window.LINUX_DESKTOP_ENTRIES)


def test_a_zip_with_a_duplicate_entry_is_refused(tmp_path):
    import io, warnings, zipfile
    from revamp_core import backup
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns about the duplicate name
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("settings.json", '{"a": 1}')
            z.writestr("settings.json", '{"a": 2}')
    with pytest.raises(ValueError, match="duplicate"):
        backup.import_zip(buf.getvalue(), tmp_path / "data", root=tmp_path / "bk")


def test_ffprobe_na_numbers_keep_the_other_tags(monkeypatch):
    out = json.dumps({"format": {"duration": "200.5", "bit_rate": "N/A", "tags": {"title": "Kept", "artist": "A"}},
                      "streams": [{"codec_type": "audio", "sample_rate": "N/A", "channels": "2", "codec_name": "opus"}]})
    class R:
        stdout = out.encode()
    monkeypatch.setattr(tags.subprocess, "run", lambda *a, **k: R())
    meta = tags._probe_ffprobe("x.opus")
    assert meta["title"] == "Kept" and meta["channels"] == 2 and meta["bitrate_kbps"] is None


def test_a_junk_heartbeat_is_not_a_server_error(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_STATE", None)
    for junk in ([1, 2], "x", 5):
        store.heartbeat(junk)
    assert store.get_state()["live"]["now_playing"] == {}
