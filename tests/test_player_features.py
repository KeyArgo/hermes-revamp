"""Likes, playlist rename/delete through the desktop proxy, and the new player commands."""

from __future__ import annotations

import json
import threading
import time

import pytest

from test_plugin_api import _json, _stub_fastapi  # noqa: F401  (fixture helpers)
from test_revamp import _wait_scanned


@pytest.fixture()
def api(server):
    import importlib
    import sys
    _stub_fastapi()
    sys.modules.pop("dashboard.plugin_api", None)
    return importlib.import_module("dashboard.plugin_api")


def _ids(server):
    _wait_scanned(server)
    st, _, body = server.client.get("/api/library/tracks?limit=50")
    assert st == 200
    return [t["id"] for t in body["tracks"]]


def test_likes_like_unlike_list_and_persist(server):
    a, b = _ids(server)[:2]
    st, _, out = server.client.get("/api/likes")
    assert st == 200 and out == {"ids": [], "tracks": []}
    assert server.client.post("/api/likes", {"track_id": a})[0] == 200
    assert server.client.post("/api/likes", {"track_id": b})[0] == 200
    assert server.client.post("/api/likes", {"track_id": a})[0] == 200  # twice: still once
    st, _, out = server.client.get("/api/likes")
    assert out["ids"] == [a, b]
    assert [t["id"] for t in out["tracks"]] == [a, b]
    assert out["tracks"][0]["url"] == f"/api/media/{a}" and out["tracks"][0]["liked"] is True
    # search results carry the flag, so the full page can draw hearts
    st, _, res = server.client.get("/api/library/tracks?limit=50")
    flags = {t["id"]: t["liked"] for t in res["tracks"]}
    assert flags[a] is True and flags[b] is True
    # unlike both ways
    st, _, out = server.client.post("/api/likes", {"track_id": a, "liked": False})
    assert st == 200 and out["ids"] == [b]
    st, _, out = server.client.delete(f"/api/likes?id={b}")
    assert st == 200 and out["ids"] == []
    server.client.post("/api/likes", {"track_id": b})
    # on disk, and a whole-state PUT from a player cannot undo it
    saved = json.loads((server.data_dir / "state.json").read_text())
    assert saved["likes"] == [b]
    server.client.put("/api/state", {"likes": [], "volume": 40})
    assert server.client.get("/api/likes")[2]["ids"] == [b]


def test_likes_validation(server):
    _wait_scanned(server)
    assert server.client.post("/api/likes", {})[0] == 400
    assert server.client.post("/api/likes", {"track_id": "nope"})[0] == 404
    # unliking an id that vanished from the library still works
    assert server.client.post("/api/likes", {"track_id": "nope", "liked": False})[0] == 200
    assert server.client.delete("/api/likes")[0] == 400


def test_likes_broadcast_on_sse(server):
    a = _ids(server)[0]
    events: list = []
    t = threading.Thread(target=lambda: events.extend(server.sse_lines(seconds=3.0, want=1)), daemon=True)
    t.start()
    time.sleep(0.6)
    server.client.post("/api/likes", {"track_id": a})
    t.join(timeout=5)
    assert ("likes", {"ids": [a]}) in events


@pytest.mark.parametrize("cmd", [
    {"action": "seek_by", "seconds": -10},
    {"action": "sleep", "minutes": 15},
    {"action": "sleep", "end_of_track": True},
    {"action": "queue_remove", "index": 1},
    {"action": "queue_move", "from": 0, "to": 2},
    {"action": "queue_clear"},
])
def test_new_commands_accepted(server, cmd):
    st, _, res = server.client.post("/api/command", cmd)
    assert st == 200 and res["ok"] is True


def test_proxy_likes_and_playlist_rename_delete(api, server):
    a = _ids(server)[0]
    assert _json(api.like({"track_id": a}))["ids"] == [a]
    assert [t["id"] for t in _json(api.likes())["tracks"]] == [a]
    assert _json(api.like({"track_id": a, "liked": False}))["ids"] == []
    st, _, pl = server.client.post("/api/playlists", {"name": "Road Trip", "items": []})
    renamed = _json(api.playlist_update(pl["id"], {"name": "Night Drive"}))
    assert renamed["name"] == "Night Drive" and renamed["id"] == "road-trip"  # renames keep the id
    assert [p["name"] for p in _json(api.playlists())] == ["Night Drive"]
    assert api.playlist_update("missing", {"name": "x"}).status_code == 404
    assert _json(api.playlist_delete("road-trip"))["ok"] is True
    assert _json(api.playlists()) == []
    assert api.playlist_delete("road-trip").status_code == 404


def test_liked_list_can_be_reordered_and_trimmed_but_never_grown(server):
    """The Liked editor saves with PUT /api/likes: it may remove or reorder hearts,
    never add one (an id that was not liked is ignored)."""
    ids = _ids(server)[:3]
    assert len(ids) == 3
    for t in ids:
        assert server.client.post("/api/likes", {"track_id": t})[0] == 200
    a, b, c = ids
    # the editor showed a, b, c; b was taken out (only what it showed can be removed)
    code, _, out = server.client.put("/api/likes", {"ids": [c, a, "not-liked", a], "shown": [a, b, c]})
    assert code == 200 and out["ids"] == [c, a]  # b removed, reordered, junk + dupes dropped
    _, _, got = server.client.get("/api/likes")
    assert got["ids"] == [c, a] and [t["id"] for t in got["tracks"]] == [c, a]
    assert server.client.put("/api/likes", {"ids": "oops"})[0] == 400


def test_empty_new_playlist_then_append(server):
    code, _, pl = server.client.post("/api/playlists", {"name": "Road Trip", "items": []})
    assert code == 201 and pl["items"] == []
    item = {"kind": "stream", "title": "S", "url": "https://example.com/s.mp3"}
    code, _, out = server.client.put(f"/api/playlists/{pl['id']}", {"items": [item]})
    assert code == 200 and len(out["items"]) == 1
