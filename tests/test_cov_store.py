"""Coverage for revamp_core/store.py units: loading defaults/legacy files,
atomic-write failures, resume/likes/live/ring paths."""

from __future__ import annotations

import json
import time

import pytest

from revamp_core import store


@pytest.fixture()
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_STATE", None)
    yield tmp_path
    monkeypatch.setattr(store, "_STATE", None)


def _write_state(tmp_path, payload):
    (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def test_load_defaults_and_legacy_file(box):
    st = store.get_state()
    assert st["volume"] == 80 and st["live"]["playback"] is None
    _write_state(box, {"volume": 30, "live": {"player_connected": True},
                       "eq": {"on": True, "bands": [1]},
                       "playlist": [{"title": "Old", "url": "/api/media/7"}],
                       "resume": {"local:track:9": "12"}})
    st2 = store.get_state()
    assert st2["volume"] == 30
    assert st2["live"]["player_connected"] is False            # file live ignored
    assert st2["eq"]["bands"] == [0.0] * 10                    # bad bands reset
    assert st2["playlist"][0]["kind"] == "music"               # queue normalized
    assert store.get_resume("local:track:9") == 12


def test_atomic_write_failure_cleans_tmp(box, monkeypatch):
    with pytest.raises(TypeError):
        store._atomic_write(box / "state.json", {"bad": object()})
    assert not list(box.glob(".state-*.tmp"))
    assert not (box / "state.json").exists()


def test_update_mergeable_and_persist_oserror(box, monkeypatch):
    store.update({"volume": 55, "likes": ["hack"], "live": {"x": 1},
                  "playlist": [{"url": "/api/media/3"}],
                  "resume": {"local:track:5": 7}})
    st = store.get_state()
    assert st["volume"] == 55 and st["likes"] == []             # client can't set
    assert st["playlist"][0]["playback"]["url"] == "/api/media/3"

    real = store._atomic_write

    def boom(path, payload):
        raise OSError("disk gone")
    monkeypatch.setattr(store, "_atomic_write", boom)
    with pytest.raises(store.StoreWriteError, match="could not save state"):
        store.update({"volume": 20})
    assert store.get_state()["volume"] == 55
    monkeypatch.setattr(store, "_atomic_write", real)


def test_heartbeat_staleness_and_report_playback(box):
    store.heartbeat({"title": "T", "kind": "stream", "media_kind": "stream",
                     "capabilities": ["seek", 7], "backend": None})
    st = store.get_state()
    assert st["live"]["player_connected"] is True
    assert st["live"]["playback"]["backend"] == "webamp"        # legacy default
    assert st["live"]["playback"]["kind"] == "stream"
    assert st["live"]["playback"]["capabilities"] == ["seek"]    # junk filtered

    store.report_playback({"item_id": "x", "status": "playing"})
    st = store.get_state()
    assert st["live"]["playback"]["item_id"] == "x" and st["live"]["playback"]["connected"]

    with store._LOCK:
        store._STATE["live"]["last_heartbeat"] = time.time() - 999
    assert store.get_state()["live"]["player_connected"] is False

    store.heartbeat(None)
    assert store._playback_from({}) is None                      # empty np -> None


def test_resume_entries(box):
    st = store.get_state()
    store.set_resume("local:track:1", 30)
    assert store.get_resume("local:track:1") == 30
    pos, when = store.get_resume_entry("local:track:1")
    assert pos == 30 and when > 0
    store.set_resume("local:track:1", None)                      # clears
    assert store.get_resume_entry("local:track:1") == (None, 0.0)

    _write_state(box, {"resume": {"rk": "unparseable"}, "resume_at": {"rk": "2.5"}})
    store.reload()
    pos, when = store.get_resume_entry("rk")
    assert pos is None and when == 2.5                           # except -> pos 0
    with store._LOCK:
        store._STATE = {"resume": {"rk": 5}, "resume_at": {"rk": 2.5},
                        "live": {}}
    assert store.get_resume_entry("rk:unknown") == (None, 0.0)


def test_likes_and_ordering(box):
    store.set_liked("a", True)
    store.set_liked("a", True)                                   # dedup
    store.set_liked("b", True)
    assert store.likes() == ["a", "b"]
    store.set_liked("a", False)
    assert store.likes() == ["b"]
    store.set_liked("a", True)

    out = store.set_likes_order(["b", "a", "a", "ghost", 5],
                                shown=["a", "b", "gone"])
    assert out == ["b", "a"]                                     # ghost/5/dup out
    out = store.set_likes_order(["a"], shown=["a", "b"])         # only shown-missing removed
    assert out == ["a"]
    store.set_liked("c", True)
    out = store.set_likes_order(["c"], shown=["c"])              # unsent likes kept
    assert out == ["c", "a"] or set(out) == {"c", "a"}


def test_agent_ring_and_snapshot(box, monkeypatch):
    for i in range(store.AGENT_RING_SIZE + 5):
        store.add_agent_event({"i": i})
    evs = store.agent_events()
    assert len(evs) == store.AGENT_RING_SIZE and evs[-1]["i"] == 204

    real = store._atomic_write

    def boom(path, payload):
        raise OSError("gone")
    monkeypatch.setattr(store, "_atomic_write", boom)
    store.add_agent_event({"i": 999})                            # persist error swallowed
    monkeypatch.setattr(store, "_atomic_write", real)

    snap = store.public_state_snapshot()
    assert "_agent_events" not in snap and "live" not in snap
    assert (box / "state.json").exists()                         # ring persisted


def test_resume_entry_bad_timestamp(box):
    _write_state(box, {"resume": {"rk": 1}, "resume_at": {"rk": "garbage"}})
    store.reload()
    store.get_resume_entry("rk")


def test_reload_and_run_locked(box):
    store.update({"volume": 10}, persist=False)
    store.reload()
    assert store.get_state()["volume"] == 80                     # reread from disk
    box_result = store.run_locked(lambda: "done")
    assert box_result == "done"
    assert store._STATE is None
