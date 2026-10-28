"""Branch coverage for the Hermes plugin glue in the repo-root __init__.py.

Tools are driven through a FakeClient (offline, deterministic); hooks are
captured by replacing _post_agent_event; register() gets a recording ctx.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from revamp_core.client import RevampUnavailable


def _plugin():
    repo = str(Path(__file__).resolve().parent.parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import __init__ as plug
    return plug


class FakeClient:
    """Dict-routed stand-in for RevampClient: get/post/put/delete by path."""

    def __init__(self, routes=None, raises=None):
        self.routes = routes or {}
        self.raises = raises
        self.calls = []

    def _go(self, method, path, body=None, **params):
        self.calls.append((method, path, body, params))
        if self.raises:
            raise self.raises
        key = f"{method} {path}" if f"{method} {path}" in self.routes else path
        try:
            return self.routes[key]
        except KeyError:
            raise AssertionError(f"unrouted {method} {path}")

    def get(self, path, **params):
        return self._go("GET", path, None, **params)

    def post(self, path, body=None, **params):
        return self._go("POST", path, body, **params)

    def put(self, path, body=None, **params):
        return self._go("PUT", path, body, **params)

    def delete(self, path, **params):
        return self._go("DELETE", path, None, **params)


def _item(i):
    return {"id": f"t{i}", "title": f"Track {i}", "artist": "Art",
            "url": f"/api/media/t{i}", "duration": 2.0}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_hint_payload_attaches_only_when_no_player():
    plug = _plugin()
    assert "hint" not in plug._hint_payload({})
    assert "hint" not in plug._hint_payload({"live": {"player_connected": True}})
    out = plug._hint_payload({"player_connected": False})
    assert "hint" in out and "http://127.0.0.1:" in out["hint"]
    out = plug._hint_payload({"live": {"player_connected": False}})
    assert "hint" in out


def test_hint_payload_swallows_exceptions():
    plug = _plugin()

    class Weird(dict):
        def get(self, *a, **k):
            raise RuntimeError("nope")
    w = Weird()
    assert plug._hint_payload(w) is w and "hint" not in w


def test_ensure_and_boot_swallow_failures(monkeypatch):
    plug = _plugin()
    calls = []
    monkeypatch.setattr(plug._core_server, "ensure_server",
                        lambda: calls.append(1))
    plug._ensure()
    assert calls == [1]

    def boom():
        raise OSError("no sockets here")
    monkeypatch.setattr(plug._core_server, "ensure_server", boom)
    plug._boot()  # must not raise


def test_client_helper_ensures_then_builds_client(server, monkeypatch):
    plug = _plugin()
    calls = []
    monkeypatch.setattr(plug._core_server, "ensure_server",
                        lambda: calls.append(1))
    c = plug._client()
    assert calls == [1]
    assert "volume" in c.get("/api/state")  # talks to the fixture server


def test_tool_error_shapes():
    plug = _plugin()
    out = json.loads(plug._tool_error(RevampUnavailable("server down")))
    assert out["ok"] is False and "start the player" in out["hint"]
    out = json.loads(plug._tool_error(RevampUnavailable("bad control characters")))
    assert out["error"] == "RevampUnavailable: bad control characters" and "hint" not in out
    out = json.loads(plug._tool_error(ValueError("boom")))
    assert out["error"] == "ValueError: boom"


# ---------------------------------------------------------------------------
# tools driven by FakeClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake(monkeypatch):
    plug = _plugin()
    holder = {}

    def make(routes=None, raises=None):
        f = FakeClient(routes, raises)
        holder["f"] = f
        monkeypatch.setattr(plug, "_client", lambda: f)
        return f
    return make


def test_player_status_hints_when_no_player(fake):
    plug = _plugin()
    fake({"/api/state": {"live": {"player_connected": False,
                                  "now_playing": {"title": "X"}},
                         "volume": 40, "shuffle": False}})
    out = json.loads(plug._tool_player({"action": "status"}))
    assert out["ok"] and out["now_playing"]["title"] == "X"
    assert out["state"] == {"volume": 40, "shuffle": False}


@pytest.mark.parametrize("action,body_action", [
    ("play", "play"), ("pause", "pause"), ("toggle", "toggle"),
    ("stop", "stop"), ("next", "next"), ("previous", "prev"),
])
def test_player_simple_actions_post_command(fake, action, body_action):
    plug = _plugin()
    f = fake({"/api/command": {"delivered": 1}})
    out = json.loads(plug._tool_player({"action": action}))
    assert out == {"ok": True, "delivered": 1}
    assert f.calls[-1][2] == {"action": body_action}


def test_player_seek_volume_shuffle_repeat(fake):
    plug = _plugin()
    f = fake({"/api/command": {"delivered": 1}})
    plug._tool_player({"action": "seek", "seconds": 12.5})
    assert f.calls[-1][2] == {"action": "seek", "seconds": 12.5}
    plug._tool_player({"action": "volume", "percent": 55})
    assert f.calls[-1][2] == {"action": "volume", "percent": 55}
    plug._tool_player({"action": "shuffle", "on": True})
    assert f.calls[-1][2] == {"action": "shuffle", "on": True}
    plug._tool_player({"action": "repeat"})
    assert f.calls[-1][2] == {"action": "repeat", "on": False}


def test_player_seek_without_seconds_is_unknown(fake):
    plug = _plugin()
    fake({})
    out = json.loads(plug._tool_player({"action": "seek"}))
    assert out["ok"] is False and "unknown player action" in out["error"]


def test_player_location_branches(fake):
    plug = _plugin()
    f = fake({"/api/command": {"delivered": 1}})
    out = json.loads(plug._tool_player({"action": "location", "location": "FLOATING"}))
    assert out["ok"] and f.calls[-1][2] == {"action": "player_location",
                                            "location": "floating"}
    out = json.loads(plug._tool_player({"action": "location", "location": "attic"}))
    assert out["ok"] is False and "docked, floating or mini" in out["error"]


def test_player_bad_args_and_errors(fake):
    plug = _plugin()
    out = json.loads(plug._tool_player("not a dict"))
    assert out == {"ok": False, "error": "arguments must be an object"}
    assert json.loads(plug._tool_player(None))["error"] == "arguments must be an object"
    fake({}, raises=RevampUnavailable("no player"))
    out = json.loads(plug._tool_player({"action": "play"}))
    assert out["ok"] is False and "start the player" in out["hint"]
    fake({}, raises=RuntimeError("kaboom"))
    out = json.loads(plug._tool_player({"action": "play"}))
    assert out["error"] == "RuntimeError: kaboom"
    out = json.loads(plug._tool_player({"action": "nope"}))
    assert out["ok"] is False and "unknown player action" in out["error"]


LIB_ROUTES = {
    "/api/library/tracks": {"total": 3, "tracks": [_item(i) for i in range(3)]},
    "/api/library/rescan": {"started": True},
    "/api/library/status": {"count": 3, "scanning": False},
    "/api/command": {"delivered": 1},
}


def test_library_search_stats_rescan(fake):
    plug = _plugin()
    f = fake(dict(LIB_ROUTES))
    out = json.loads(plug._tool_library({"action": "search", "query": "art",
                                         "artist": "a", "album": "b", "limit": 100}))
    assert out["ok"] and out["total"] == 3 and len(out["tracks"]) == 3
    get_call = [c for c in f.calls if c[0] == "GET"][-1]
    assert get_call[3]["limit"] == 200  # min(200, 50 * 4)
    assert json.loads(plug._tool_library({"action": "stats"}))["count"] == 3
    assert json.loads(plug._tool_library({"action": "rescan"}))["started"] is True


def test_library_play_and_enqueue_items(fake):
    plug = _plugin()
    fake(dict(LIB_ROUTES))
    out = json.loads(plug._tool_library({"action": "play", "query": "t"}))
    assert out["ok"] and out["loaded"] == 3
    out2 = json.loads(plug._tool_library({"action": "enqueue", "query": "t"}))
    assert out2["ok"] and out["delivered"] == 1


def test_library_modes_and_empty(fake):
    plug = _plugin()
    f = fake(dict(LIB_ROUTES))
    plug._tool_library({"action": "play", "query": "t"})
    assert f.calls[-1][2]["mode"] == "replace" and f.calls[-1][2]["play"] is True
    item = f.calls[-1][2]["items"][0]
    assert item["kind"] == "track" and item["track_id"] == "t0"
    plug._tool_library({"action": "enqueue", "query": "t"})
    assert f.calls[-1][2]["mode"] == "append" and f.calls[-1][2]["play"] is False
    f.routes["/api/library/tracks"] = {"total": 0, "tracks": []}
    out = json.loads(plug._tool_library({"action": "play", "query": "zz"}))
    assert out == {"ok": False, "error": "no matching tracks"}


@pytest.mark.parametrize("tool,args", [
    ("_tool_player", {"action": "totally-bogus"}),
    ("_tool_library", {"action": "totally-bogus"}),
    ("_tool_playlist", {"action": "totally-bogus", "name": "mix one"}),
    ("_tool_radio", {"action": "totally-bogus"}),
])
def test_tools_unknown_actions_and_bad_types(tool, args, fake):
    plug = _plugin()
    fake({"/api/playlists": [{"id": "mix-one", "name": "mix one"}],
          "/api/playlists/mix-one": {"id": "mix-one", "items": []},
          "/api/radio/presets": [],
          "/api/library/tracks": {"total": 0, "tracks": []},
          "/api/command": {"delivered": 0}})
    fn = getattr(plug, tool)
    assert json.loads(fn(42)) == {"ok": False, "error": "arguments must be an object"}
    weird = json.loads(fn(args))
    assert weird["ok"] is False and "unknown" in weird["error"]


def test_library_bad_limit_gives_generic_error(fake):
    plug = _plugin()
    fake({})
    out = json.loads(plug._tool_library({"action": "search", "limit": "abc"}))
    assert out["ok"] is False and out["error"].startswith("ValueError:")


PL_ROUTES = {
    "/api/playlists": [{"id": "Weird Name!", "name": "mix one"},
                       {"id": "gone", "name": "gone"}],
    "/api/playlists/Weird%20Name%21": {"id": "Weird Name!", "name": "mix one",
                                       "items": [{"kind": "track"}]},
    "/api/playlists/gone": {"error": "no such playlist"},
    "/api/library/tracks": {"total": 1, "tracks": [_item(9)]},
    "/api/command": {"delivered": 1},
}


def _pl_routes():
    import copy
    return copy.deepcopy(PL_ROUTES)


def test_playlist_list_show_delete_by_name(fake):
    plug = _plugin()
    f = fake(_pl_routes())
    out = json.loads(plug._tool_playlist({"action": "list"}))
    assert out["ok"] and out["playlists"][0]["id"] == "Weird Name!"
    out = json.loads(plug._tool_playlist({"action": "show", "name": " MIX ONE "}))
    assert out["ok"] and out["name"] == "mix one"
    out = json.loads(plug._tool_playlist({"action": "delete", "name": "weird name!"}))
    assert out["ok"] is True
    assert ("DELETE", "/api/playlists/Weird%20Name%21", None, {}) in f.calls


def test_playlist_create_and_add_update(fake):
    plug = _plugin()
    f = fake(_pl_routes())
    plug._tool_playlist({"action": "create", "name": "New",
                         "items": [{"kind": "track"}]})
    assert f.calls[-1][0] == "POST" and f.calls[-1][1] == "/api/playlists"
    assert f.calls[-1][2] == {"name": "New", "items": [{"kind": "track"}]}
    f.routes["/api/playlists"] = [{"id": "Weird Name!", "name": "mix one"}]
    f.routes["POST /api/playlists"] = {"id": "new", "name": "New", "items": []}
    # add PUTs the raw pl["id"], not the quoted pid (ids are always slugs in practice)
    f.routes["PUT /api/playlists/Weird Name!"] = {"ok": True}
    out = json.loads(plug._tool_playlist({"action": "add", "name": "weird name!",
                                          "query": "t"}))
    assert out["ok"] and out["added"] == 1
    put = [c for c in f.calls if c[0] == "PUT"][-1]
    assert len(put[2]["items"]) == 2


def test_playlist_play_and_missing_branches(fake):
    plug = _plugin()
    f = fake(_pl_routes())
    out = json.loads(plug._tool_playlist({"action": "play", "name": "mix one"}))
    assert out["ok"] is True
    load = f.calls[-1][2]
    assert load["action"] == "load" and load["play"] is True
    assert len(load["items"]) == 1
    out = json.loads(plug._tool_playlist({"action": "add", "name": "gone",
                                          "query": "t"}))
    assert out == {"ok": False, "error": "no such playlist"}
    out = json.loads(plug._tool_playlist({"action": "play", "name": "gone"}))
    assert out == {"ok": False, "error": "no such playlist"}
    out = json.loads(plug._tool_playlist({"action": "show", "name": "nope"}))
    assert out["ok"] is False and "no playlist named" in out["error"]
    assert out["playlists"] == ["mix one", "gone"]


RAD_ROUTES = {
    "/api/radio/presets": [{"id": "somafm-dronezone", "name": "SomaFM Drone Zone",
                            "url": "http://p/1"}],
    "/api/radio/favorites": [{"id": "fav-jazz", "name": "My Jazz", "url": "http://f/1"}],
    "/api/command": {"delivered": 2},
}


def test_radio_presets_favorites_search(fake):
    plug = _plugin()
    f = fake(dict(RAD_ROUTES, **{"/api/radio/search": [{"id": "s1", "name": "Hit",
                                                        "url": "http://s/1"}]}))
    assert json.loads(plug._tool_radio({"action": "presets"}))["stations"] == \
        RAD_ROUTES["/api/radio/presets"]
    assert json.loads(plug._tool_radio({"action": "favorites"}))["stations"] == \
        RAD_ROUTES["/api/radio/favorites"]
    out = json.loads(plug._tool_radio({"action": "search", "query": "jazz",
                                       "limit": 400}))
    assert out["stations"][0]["name"] == "Hit"
    get = [c for c in f.calls if c[0] == "GET" and c[1] == "/api/radio/search"][-1]
    assert get[3] == {"q": "jazz", "limit": 50}


def test_radio_play_resolution_orders(fake):
    plug = _plugin()
    f = fake(dict(RAD_ROUTES))
    out = json.loads(plug._tool_radio({"action": "play", "station_id": "fav-jazz"}))
    assert out["ok"] and out["station"] == "My Jazz"  # id found via favorites
    item = f.calls[-1][2]["items"][0]
    assert item["kind"] == "stream" and item["station_id"] == "fav-jazz"
    out = json.loads(plug._tool_radio({"action": "play", "query": "Jazz"}))
    assert out["station"] == "My Jazz"  # query prefers favorites over presets
    f.routes["/api/radio/favorites"] = []
    out = json.loads(plug._tool_radio({"action": "play", "query": "drone"}))
    assert out["station"] == "SomaFM Drone Zone"  # then presets
    out = json.loads(plug._tool_radio({"action": "play", "url": "http://x/y",
                                       "name": "Custom"}))
    assert out["station"] == "Custom"
    out = json.loads(plug._tool_radio({"action": "play"}))
    assert out == {"ok": False, "error": "no station found"}
    f.routes["/api/radio/search"] = []
    out = json.loads(plug._tool_radio({"action": "play", "query": "zzz-none"}))
    assert out["ok"] is False


def test_radio_search_fallback_and_add_favorite(fake):
    plug = _plugin()
    f = fake(dict(RAD_ROUTES, **{"/api/radio/search": [{"id": "rb", "name": "Live Hit",
                                                        "url": "http://r"}]}))
    out = json.loads(plug._tool_radio({"action": "play", "query": "unseen band"}))
    assert out["ok"] and out["station"] == "Live Hit"
    f.routes["/api/radio/search"] = "not-a-list"
    out = json.loads(plug._tool_radio({"action": "play", "query": "unseen band 2"}))
    assert out == {"ok": False, "error": "no station found"}
    f.routes["POST /api/radio/favorites"] = {"stations": []}
    out = json.loads(plug._tool_radio({"action": "add_favorite", "station_id": "x",
                                       "name": "S", "url": "http://u"}))
    assert out["ok"] is True
    post = f.calls[-1]
    assert post[0] == "POST" and post[1] == "/api/radio/favorites"
    assert post[2] == {"id": "x", "name": "S", "url": "http://u"}


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------

@pytest.fixture()
def captured(monkeypatch):
    plug = _plugin()
    events = []
    monkeypatch.setattr(plug, "_post_agent_event",
                        lambda kind, fields: events.append((kind, fields)))
    return plug, events


def test_pre_tool_call_extracts_previews(captured):
    plug, events = captured
    plug._hook_pre_tool_call(tool_name="bash", session_id="s1",
                             args={"command": "ls\ntwo"})
    plug._hook_pre_tool_call(tool={"name": "read"}, args={"path": "  a\nb "})
    for k, v in (("query", "q"), ("url", "u"), ("pattern", "p"),
                 ("prompt", "pr"), ("goal", "g")):
        plug._hook_pre_tool_call(tool_name="x", args={k: v})
    plug._hook_pre_tool_call(tool_name="x", args="not a dict")
    plug._hook_pre_tool_call(tool_name="x", args={"nothing": 1})
    assert events[0] == ("tool_start", {"tool": "bash", "preview": "ls two",
                                        "session_id": "s1"})
    assert events[1][1]["tool"] == "read" and events[1][1]["preview"] == "a b"
    assert [e[1]["preview"] for e in events[2:7]] == ["q", "u", "p", "pr", "g"]
    assert events[7][1]["preview"] == "" and events[8][1]["preview"] == ""


def test_preview_long_and_prefers_command(captured):
    plug, events = captured
    plug._hook_pre_tool_call(tool_name="x", args={"command": "c" * 200, "path": "p"})
    assert events[-1][1]["preview"] == "c" * 80


def test_post_tool_call_ok_flag(captured):
    plug, events = captured
    plug._hook_post_tool_call(tool_name="bash", session_id="s", status="done")
    assert events[-1] == ("tool_end", {"tool": "bash", "ok": True,
                                       "session_id": "s"})
    plug._hook_post_tool_call(tool_name="bash", error_message="fail")
    assert events[-1][1]["ok"] is False
    plug._hook_post_tool_call(tool_name="bash", status="error")
    assert events[-1][1]["ok"] is False


def test_session_and_approval_hooks(captured):
    plug, events = captured
    plug._hook_session_start(session_id="a")
    plug._hook_session_end(session_id="b")
    assert events[0] == ("session_start", {"session_id": "a"})
    assert events[1] == ("session_end", {"session_id": "b"})
    plug._hook_pre_approval_request(command="rm -rf /", session_id="c", session_key="key-c", surface="cli")
    assert events[2] == ("approval_request", {"preview": "rm -rf /",
                                              "session_id": "c", "session_key": "key-c",
                                              "approval_surface": "cli"})
    plug._hook_pre_approval_request(description="y" * 200)
    assert events[3][1]["preview"] == "y" * 80
    plug._hook_post_approval_response(choice="Allow once", session_id="d", session_key="key-d", surface="cli")
    assert events[4] == ("approval_response", {"ok": True, "session_id": "d",
                                                  "session_key": "key-d", "approval_surface": "cli"})
    for choice in ("deny", "timeout", "", None, "DENY"):
        plug._hook_post_approval_response(choice=choice)
        assert events[-1][1]["ok"] is False


def test_post_agent_event_reaches_server(server):
    plug = _plugin()
    plug._post_agent_event("tool_start", {"tool": "cov", "preview": "p",
                                          "session_id": "sess"})
    deadline = time.time() + 5
    while time.time() < deadline:
        _, _, events = server.client.get("/api/agent_events")
        if any(e.get("kind") == "tool_start" and e.get("tool") == "cov"
               and e.get("source") == "hermes" for e in events):
            break
        time.sleep(0.1)
    else:
        pytest.fail("agent event never landed")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

class RecordingCtx:
    def __init__(self, bad_toolset=False):
        self.bad_toolset = bad_toolset
        self.tools, self.hooks, self.skills_ = [], [], []

    def register_tool(self, **kwargs):
        if self.bad_toolset and "toolset" in kwargs:
            raise TypeError("no such kwarg")
        self.tools.append(kwargs)

    def register_hook(self, name, fn):
        self.hooks.append((name, fn))

    def register_skill(self, name, path):
        self.skills_.append((name, Path(path)))


class BareCtx(RecordingCtx):
    """No register_skill at all: exercises the hasattr() guard in register()."""

    def __getattribute__(self, name):
        if name == "register_skill":
            raise AttributeError(name)
        return super().__getattribute__(name)


def test_register_tools_hooks_skill_and_boot(monkeypatch, tmp_path):
    plug = _plugin()
    pins = []
    monkeypatch.setattr(plug._core_config, "current_hermes_home",
                        lambda: "/fake/home")
    monkeypatch.setattr(plug._core_config, "pin_hermes_home",
                        lambda h: pins.append(h))
    boots = []
    monkeypatch.setattr(plug, "_boot", lambda: boots.append(1))
    (tmp_path / "skills" / "dj").mkdir(parents=True)
    (tmp_path / "skills" / "dj" / "SKILL.md").write_text("# dj")
    (tmp_path / "skills" / "broken").mkdir()  # no SKILL.md -> skipped
    monkeypatch.setattr(plug, "__file__", str(tmp_path / "plugin.py"))
    ctx = RecordingCtx()
    plug.register(ctx)
    assert pins == ["/fake/home"]
    assert [t["name"] for t in ctx.tools] == ["revamp_player", "revamp_library",
                                              "revamp_playlist", "revamp_radio"]
    assert all(t["toolset"] == "revamp" for t in ctx.tools)
    assert [h[0] for h in ctx.hooks] == [name for name, _ in plug.HOOKS]
    assert ctx.skills_ == [("dj", tmp_path / "skills" / "dj" / "SKILL.md")]
    deadline = time.time() + 2
    while time.time() < deadline and not boots:
        time.sleep(0.05)
    assert boots == [1]


def test_register_falls_back_and_survives_pin_error(monkeypatch):
    plug = _plugin()
    monkeypatch.setattr(plug._core_config, "pin_hermes_home",
                        lambda h: (_ for _ in ()).throw(OSError("ro")))
    monkeypatch.setattr(plug._core_config, "current_hermes_home", lambda: "/x")
    monkeypatch.setattr(plug, "_boot", lambda: None)
    ctx = BareCtx(bad_toolset=True)
    plug.register(ctx)  # no skills attr on ctx -> hasattr branch
    assert len(ctx.tools) == 4
    assert all("toolset" not in t for t in ctx.tools)
    assert len(ctx.hooks) == len(plug.HOOKS)


def test_schemas_and_tools_table():
    plug = _plugin()
    by_name = {t[0]: t[1] for t in plug.TOOLS}
    assert set(by_name) == {"revamp_player", "revamp_library",
                            "revamp_playlist", "revamp_radio"}
    assert by_name["revamp_player"]["parameters"]["properties"]["action"]["enum"] \
        == plug.PLAYER_ACTIONS
    for _, schema, _ in plug.TOOLS:
        assert schema["parameters"]["type"] == "object"
        assert isinstance(schema["parameters"]["required"], list)
