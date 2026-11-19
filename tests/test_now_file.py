"""now.json: harness plugins glance at it instead of starting a process per check (2026-11-18).

Written only while a player reports: on a change, else at most every NOW_FILE_REFRESH seconds,
so with no player the server writes nothing and the file's age says "disconnected"."""

from __future__ import annotations

import json

import pytest

from revamp_core import store


@pytest.fixture()
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(store, "_now_written", (None, 0.0))
    yield tmp_path
    monkeypatch.setattr(store, "_STATE", None)


def test_no_player_no_file(box):
    store.get_state()
    assert not (box / "now.json").exists()


def test_heartbeat_writes_what_the_band_needs(box):
    store.heartbeat({"title": "Song", "artist": "Band", "status": "PLAYING", "position": 3})
    now = json.loads((box / "now.json").read_text())
    assert now["connected"] is True and now["title"] == "Song" and now["artist"] == "Band"
    assert now["status"] == "playing" and isinstance(now["updated"], float)
    assert "position" not in now  # position ticks every heartbeat; it must not force writes


def test_same_content_is_not_rewritten_until_refresh_due(box, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    store.heartbeat({"title": "Song", "status": "playing", "position": 1})
    first = (box / "now.json").read_text()
    clock[0] += 1
    store.heartbeat({"title": "Song", "status": "playing", "position": 2})
    assert (box / "now.json").read_text() == first  # nothing changed, not due
    clock[0] += store.NOW_FILE_REFRESH
    store.heartbeat({"title": "Song", "status": "playing", "position": 7})
    assert json.loads((box / "now.json").read_text())["updated"] == clock[0]  # kept fresh


def test_a_change_is_written_at_once(box, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    store.heartbeat({"title": "Song", "status": "playing"})
    clock[0] += 0.5
    store.heartbeat({"title": "Song", "status": "paused"})
    assert json.loads((box / "now.json").read_text())["status"] == "paused"


def test_remote_backend_playback_also_writes(box):
    store.report_playback({"title": "Remote", "artist": "X", "status": "playing"})
    assert json.loads((box / "now.json").read_text())["title"] == "Remote"


def test_unwritable_dir_is_harmless(box, monkeypatch):
    monkeypatch.setattr(store, "_now_path", lambda: box / "missing" / "now.json")
    store.heartbeat({"title": "Song", "status": "playing"})  # no raise
