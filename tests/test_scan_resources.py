"""A library scan must never hog the machine, and a reinstall must not re-read every song."""

from __future__ import annotations

import sys
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from revamp_core import backup as bk
from revamp_core import library, store, tags


def test_tag_reads_run_at_background_priority(monkeypatch):
    seen = {}

    class R:
        stdout = b"{}"

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setattr(tags.subprocess, "run", fake_run)
    tags._probe_ffprobe("/x.mp3")
    cmd = seen["cmd"]
    assert "ffprobe" in cmd
    if shutil.which("nice"):
        assert cmd[:3] == ["nice", "-n", "19"]
    if shutil.which("ionice"):
        assert "ionice" in cmd and cmd[cmd.index("ionice") + 1:cmd.index("ionice") + 3] == ["-c", "3"]
    assert cmd.index("ffprobe") > 0 or not (shutil.which("nice") or shutil.which("ionice"))


def test_few_parallel_reads():
    assert 1 <= library.SCAN_WORKERS <= 4


def test_a_long_scan_saves_its_progress(tmp_path, monkeypatch):
    home = tmp_path / "revamp"; home.mkdir()
    music = tmp_path / "music"; music.mkdir()
    for i in range(200):
        (music / f"{i:03d}.mp3").write_bytes(b"x" * 10)
    monkeypatch.setenv("REVAMP_HOME", str(home))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "_LAST_SCAN", 0.0)
    monkeypatch.setattr(library, "CHECKPOINT_EVERY", 0.3)
    monkeypatch.setattr(library, "PUBLISH_EVERY", 0.1)

    def slow_entry(path, cached):
        time.sleep(0.05)
        return {"id": library.track_id_for(path), "path": path, "title": Path(path).stem, "mtime": 1.0, "size": 10}

    monkeypatch.setattr(library, "_entry_for", slow_entry)
    # Earlier server tests can leave an async scan winding down. This test is
    # about checkpointing, not latest-scan-wins arbitration, so make its
    # generation exclusive and let any old/new background scans yield.
    gen = library._reserve()
    monkeypatch.setattr(library, "_superseded", lambda candidate: candidate != gen)
    th = threading.Thread(target=lambda: library.scan_sync([str(music)], gen), daemon=True)
    th.start()
    time.sleep(1.2)  # well before the 60 slow reads finish
    assert th.is_alive(), "the scan finished too fast to test a checkpoint"
    saved = json.loads((home / "library.json").read_text())["tracks"]
    assert 0 < len(saved) < 200, "no progress saved mid-scan"
    th.join(30)
    assert len(json.loads((home / "library.json").read_text())["tracks"]) == 200


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS permission bits")
def test_index_copy_and_seed_on_a_fresh_install(tmp_path):
    root = tmp_path / "backups"
    old = tmp_path / "old" / "revamp"; old.mkdir(parents=True)
    idx = {"tracks": {"a": {"id": "a", "path": "/m/a.mp3"}}, "last_scan": 5.0}
    (old / "library.json").write_text(json.dumps(idx), encoding="utf-8")
    assert bk.save_index_copy(old, root=root) is True
    assert bk.save_index_copy(old, root=root) is False  # unchanged: no rewrite
    assert oct((root / bk.INDEX_COPY).stat().st_mode & 0o777) == "0o600"
    fresh = tmp_path / "new" / "revamp"; fresh.mkdir(parents=True)
    assert bk.seed_index(fresh, root=root) is True
    assert json.loads((fresh / "library.json").read_text()) == idx
    assert bk.seed_index(fresh, root=root) is False  # never overwrites an existing index
    assert bk.list_backups(root) == []  # the copy is not mistaken for a snapshot


def test_a_broken_index_never_replaces_the_copy(tmp_path):
    root = tmp_path / "backups"
    d = tmp_path / "d"; d.mkdir()
    (d / "library.json").write_text('{"tracks": {}}', encoding="utf-8")
    assert bk.save_index_copy(d, root=root)
    time.sleep(0.01)
    (d / "library.json").write_text('{"tracks": {"half', encoding="utf-8")
    os.utime(d / "library.json", None)
    assert bk.save_index_copy(d, root=root) is False
    assert json.loads((root / bk.INDEX_COPY).read_text()) == {"tracks": {}}


def test_a_checkpoint_is_marked_incomplete_until_the_scan_finishes(tmp_path, monkeypatch):
    home = tmp_path / "revamp"; home.mkdir()
    monkeypatch.setenv("REVAMP_HOME", str(home))
    monkeypatch.setattr(library, "_INDEX", {"a": {"id": "a", "path": "/m/a.mp3"}})
    library.save_index(complete=False)
    monkeypatch.setattr(library, "_INDEX", {})
    library.load_index()
    assert library.all_tracks() and library.index_complete() is False
    library.save_index()
    library.load_index()
    assert library.index_complete() is True
    (home / "library.json").write_text(json.dumps({"tracks": {}, "last_scan": 1.0}), encoding="utf-8")
    library.load_index()
    assert library.index_complete() is True  # older index files (no flag) count as complete
