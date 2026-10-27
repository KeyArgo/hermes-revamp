"""Focus is a first-class browser playback backend."""

from revamp_core import playback


def test_focus_backend_and_capabilities(server):
    st, _, body = server.client.get("/api/playback")
    assert st == 200
    focus = next(b for b in body["backends"] if b["id"] == "focus")
    assert focus["capabilities"] == ["play", "pause", "toggle", "stop", "volume"]


def test_focus_actions_are_accepted(server):
    for action, extra in (("focus_start", {}), ("focus_stop", {}), ("focus_toggle", {}),
                          ("focus_set", {"params": {"depth": .2}})):
        st, _, body = server.client.post("/api/command", {"action": action, **extra})
        assert st == 200 and body["ok"] and body["backend"] == "focus"


def test_focus_gates_seek_and_next(server):
    item = {"id": "focus:session", "source": "focus", "kind": "focus", "title": "Focus · deep drone",
            "playback": {"backend": "focus"}, "capabilities": ["play", "pause", "toggle", "stop", "volume"]}
    server.client.post("/api/heartbeat", {"now_playing": {"item_id": item["id"], "source": "focus", "backend": "focus", "media_kind": "focus", "capabilities": item["capabilities"], "status": "PLAYING", "title": item["title"]}})
    for action in ("seek", "next", "prev"):
        st, _, body = server.client.post("/api/command", {"action": action, "seconds": 1})
        assert st == 200 and body["unsupported"] is True
