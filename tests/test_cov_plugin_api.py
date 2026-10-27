"""Fill-in coverage for dashboard/plugin_api.py: data-dir/port/token
resolution, _request failure mapping, and the generic routes the base suite
does not exercise (sources PUT/DELETE, playback GET/POST, info, window)."""

from __future__ import annotations

import importlib
import io
import json
import sys
import types
import urllib.error
import urllib.request

import pytest

from tests.test_plugin_api import _Req, _stub_fastapi  # reuse the fastapi stub


@pytest.fixture()
def api_no_server():
    _stub_fastapi()
    saved = sys.modules.pop("dashboard.plugin_api", None)
    mod = importlib.import_module("dashboard.plugin_api")
    yield mod
    if saved is not None:
        sys.modules["dashboard.plugin_api"] = saved


@pytest.fixture()
def clean_env(monkeypatch):
    for var in ("REVAMP_HOME", "HERMES_HOME", "REVAMP_PORT"):
        monkeypatch.delenv(var, raising=False)
    for m in ("hermes_constants", "hermes_cli", "hermes_cli.config"):
        monkeypatch.delitem(sys.modules, m, raising=False)


# ---------------------------------------------------------------------------
# config re-derivation (mirror of revamp_core.config, env-only here)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths / window helper")
def test_data_dir_resolution(api_no_server, clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    assert api_no_server._data_dir() == tmp_path
    monkeypatch.delenv("REVAMP_HOME")

    hc = types.ModuleType("hermes_constants")
    hc.get_hermes_home = lambda: tmp_path / "prof"
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    assert api_no_server._data_dir() == tmp_path / "prof" / "revamp"      # 41-42

    def boom():
        raise RuntimeError("no multiplex")
    hc.get_hermes_home = boom
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    assert api_no_server._data_dir() == tmp_path / "hh" / "revamp"        # 44-45

    monkeypatch.delenv("HERMES_HOME")
    assert str(api_no_server._data_dir()).endswith(".hermes/revamp")      # 45 default


def test_port_resolution(api_no_server, clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_PORT", "9411")
    assert api_no_server._port() == 9411
    monkeypatch.setenv("REVAMP_PORT", "not-a-port")                       # 52 false

    cli = types.ModuleType("hermes_cli")
    conf = types.ModuleType("hermes_cli.config")
    box = {"val": 1500}
    conf.load_config = lambda: {}
    conf.cfg_get = lambda cfg, *path: box["val"]
    cli.config = conf
    monkeypatch.setitem(sys.modules, "hermes_cli", cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", conf)
    assert api_no_server._port() == 1500                                  # 57-59
    box["val"] = 0
    assert api_no_server._port() == api_no_server.DEFAULT_PORT            # 58 falsy

    def boom(cfg, *path):
        raise RuntimeError("no config")
    conf.cfg_get = boom
    assert api_no_server._port() == api_no_server.DEFAULT_PORT            # 60-62


def test_token(api_no_server, monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    assert api_no_server._token() == ""                                   # 68-69
    (tmp_path / "token").write_text("  abc123 \n")
    assert api_no_server._token() == "abc123"


# ---------------------------------------------------------------------------
# _request mapping
# ---------------------------------------------------------------------------

def test_request_status_mapping(api_no_server, monkeypatch):
    monkeypatch.setenv("REVAMP_HOME", "/nonexistent/revamp")              # no token hdr
    seen = {}

    class R:
        def __init__(self, body, status=200):
            self.body, self.status = body, status

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def ok(req, timeout=None):
        seen["hdr"] = req.get_header("X-revamp-token")
        seen["data"] = req.data
        return R(b'{"app":"revamp"}')
    monkeypatch.setattr(api_no_server.urllib.request, "urlopen", ok)
    assert api_no_server._request("GET", "/api/health") == (200, {"app": "revamp"})
    assert seen["hdr"] is None
    api_no_server._request("POST", "/api/command", body={"command": "play"})
    assert json.loads(seen["data"]) == {"command": "play"}

    def empty(req, timeout=None):
        return R(b"", status=204)
    monkeypatch.setattr(api_no_server.urllib.request, "urlopen", empty)
    assert api_no_server._request("GET", "/x") == (204, {})

    def http_json(req, timeout=None):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"no"}'))
    monkeypatch.setattr(api_no_server.urllib.request, "urlopen", http_json)
    assert api_no_server._request("GET", "/x") == (400, {"error": "no"})  # 87-90

    def http_broken(req, timeout=None):                                    # 91-92
        class Dead:
            def read(self):
                raise OSError("closed")

            def close(self):
                pass
        raise urllib.error.HTTPError("u", 500, "boom", {}, Dead())
    monkeypatch.setattr(api_no_server.urllib.request, "urlopen", http_broken)
    assert api_no_server._request("GET", "/x") == (500, {})

    def dead(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(api_no_server.urllib.request, "urlopen", dead)
    assert api_no_server._request("GET", "/x") == (0, {})                 # 93-94


def test_clamp_and_query_helpers(api_no_server):
    assert api_no_server._clamp(999, 1, 60) == 60
    assert api_no_server._clamp(-5, 1, 60) == 1
    assert api_no_server._query(_Req("a=1")) == "a=1"
    assert api_no_server._query(object()) == ""                           # 274-275
    assert api_no_server._pl_path("a b/c") == "/api/playlists/a%20b%2Fc"


# ---------------------------------------------------------------------------
# routes the base suite never calls (against the live fixture server)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api(api_no_server):  # server-bound variant
    return api_no_server


def test_info_route(api, server, monkeypatch):
    out = api.info()
    assert out["url"] == server.base
    assert out["ok"] is True and out["version"]
    assert len(out["bridge_key"]) == 64
    monkeypatch.setenv("REVAMP_PORT", "1")                                 # down
    assert api.info()["ok"] is False and api.info()["bridge_key"]          # token still read


def test_plain_proxy_routes(api, server):
    assert json.loads(api.state().body)["volume"] is not None
    resp = api.command({"action": "pause", "source": "pytest"})
    assert resp.status_code == 200
    import time as _t
    deadline = _t.time() + 10
    tracks = []
    while _t.time() < deadline:
        tracks = json.loads(api.library_tracks(q="", limit=5).body)["tracks"]
        if tracks:
            break
        _t.sleep(0.2)
    tid = tracks[0]["id"]
    assert api.library_chapters(id=tid).status_code == 200
    assert api.library_resume({"track_id": tid, "position": 3}).status_code == 200
    assert json.loads(api.resume_get(key="local:track:" + tid).body)["position"] == 3
    assert api.radio_popular().status_code == 200
    assert api.window_status().status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths / window helper")
def test_window_open_close_proxy(api, server, monkeypatch):
    opened = api.window_open({"position": 0})
    assert opened.status_code in (200, 501, 503)  # 501 = no browser found to open
    assert api.window_close().status_code == 200  # closing what isn't open -> False ok


def test_generic_put_delete_playback(api, monkeypatch):
    seen = []

    def spy(method, path, body=None, timeout=5.0):
        seen.append((method, path, body))
        return 200, {}
    monkeypatch.setattr(api, "_request", spy)
    api.sources_put("plex@1/config", _Req("a=1&t=leak"), {"k": 1})         # 295
    api.sources_delete("plex@1/fav", _Req())                                # 300
    api.playback_get("status", _Req("x=1"))                                 # 310
    api.playback_post("backend/select", _Req(), {"backend": "mpv"})         # 315
    assert seen == [
        ("PUT", "/api/sources/plex@1/config?a=1", {"k": 1}),
        ("DELETE", "/api/sources/plex@1/fav", None),
        ("GET", "/api/playback/status?x=1", None),
        ("POST", "/api/playback/backend/select", {"backend": "mpv"}),
    ]
