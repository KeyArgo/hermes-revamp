"""dashboard/plugin_api.py read proxies, against the real fixture server.

The Desktop status-bar widget reaches the library, radio and playlists only through these
routes (the renderer never sees the token). FastAPI is stubbed when absent: the handlers are
plain functions and the stub decorators return them unchanged.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
import types

import pytest


def _stub_fastapi():
    if "fastapi" in sys.modules:
        return
    try:
        import fastapi  # noqa: F401
        return
    except ImportError:
        pass

    class Response:
        def __init__(self, content="", media_type=None, status_code=200):
            self.body = content.encode() if isinstance(content, str) else content
            self.media_type = media_type
            self.status_code = status_code

    class APIRouter:
        def _deco(self, *a, **k):
            return lambda fn: fn
        get = post = put = delete = _deco

    mod = types.ModuleType("fastapi")
    mod.APIRouter = APIRouter
    mod.Response = Response
    sys.modules["fastapi"] = mod


@pytest.fixture()
def api(server):
    _stub_fastapi()
    sys.modules.pop("dashboard.plugin_api", None)
    return importlib.import_module("dashboard.plugin_api")


def _json(resp):
    assert resp.status_code == 200, (resp.status_code, resp.body)
    return json.loads(resp.body)


def _wait_tracks(api, q="", n=1):
    deadline = time.time() + 10
    while time.time() < deadline:
        out = _json(api.library_tracks(q=q, limit=50))
        if out["total"] >= n:
            return out
        time.sleep(0.2)
    raise AssertionError("library never scanned")


def test_library_tracks_proxies_search(api):
    everything = _wait_tracks(api, n=2)
    title = everything["tracks"][0]["title"]
    word = title.split()[0]
    hits = _json(api.library_tracks(q=word, limit=50))
    assert hits["tracks"] and all(word.lower() in json.dumps(t).lower() for t in hits["tracks"])


def test_library_tracks_clamps_limit(api):
    _wait_tracks(api, n=2)
    assert len(_json(api.library_tracks(q="", limit=1))["tracks"]) == 1
    assert len(_json(api.library_tracks(q="", limit=100000))["tracks"]) <= 200


def test_state_update_proxies_a_settings_patch(api):
    assert _json(api.state_update({"settings": {"proxy_one": True}}))["settings"]["proxy_one"] is True
    state = _json(api.state_update({"settings": {"proxy_two": True}}))
    assert state["settings"]["proxy_one"] is True
    assert state["settings"]["proxy_two"] is True


def test_query_is_url_encoded(api):
    _wait_tracks(api, n=1)
    # '&' and '#' must not split or truncate the query string upstream
    out = _json(api.library_tracks(q="zz&limit=0#x", limit=50))
    assert out["total"] == 0


def test_radio_presets(api):
    presets = _json(api.radio_presets())
    assert any("nightride" in s["url"] for s in presets)


def test_radio_favorites_roundtrip(api):
    station = {"id": "t1", "name": "Test FM", "url": "https://example.com/s.mp3"}
    assert api.radio_favorite_add(station).status_code in (200, 201)
    assert any(s["id"] == "t1" for s in _json(api.radio_favorites()))
    assert api.radio_favorite_remove(id="t1").status_code == 200
    assert not any(s["id"] == "t1" for s in _json(api.radio_favorites()))


def test_playlists_list_and_get(api, server):
    import urllib.request
    req = urllib.request.Request(server.base + "/api/playlists", method="POST",
                                 data=json.dumps({"name": "Road Trip", "items": []}).encode(),
                                 headers={"X-Revamp-Token": server.token,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        created = json.loads(r.read())
    lists = _json(api.playlists())
    assert any(p["id"] == created["id"] for p in lists)
    one = _json(api.playlist(created["id"]))
    assert one["name"] == "Road Trip"
    assert api.playlist("../token").status_code == 404


def test_down_server_is_503(api, monkeypatch):
    monkeypatch.setenv("REVAMP_PORT", "1")
    assert api.radio_presets().status_code == 503
    assert api.library_tracks(q="", limit=5).status_code == 503


def test_library_kind_folders_and_browse(api, server, tmp_path):
    _wait_tracks(api, n=1)
    music = _json(api.library_tracks(q="", limit=50, kind="music"))
    assert music["tracks"] and all(t["kind"] == "music" for t in music["tracks"])
    assert _json(api.library_tracks(q="", limit=50, kind="audiobook"))["total"] == 0
    folders = _json(api.library_folders())
    assert [f["path"] for f in folders["folders"]] == [str(server.lib)]
    other = tmp_path / "picked"
    other.mkdir()
    saved = _json(api.library_folders_set({"folders": [str(other)]}))
    assert saved["custom"] is True
    assert api.library_folders_set({"folders": ["relative"]}).status_code == 400
    listing = _json(api.fs_dirs(path=str(tmp_path)))
    assert "picked" in [d["name"] for d in listing["dirs"]]
    status = _json(api.library_status())
    assert status["roots"] == [str(other)]


def test_radio_search_passes_tag(api, monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_request", lambda m, path, body=None, timeout=5.0: (seen.append(path), (200, []))[1])
    api.radio_search(q="", limit=40, tag="jazz")
    api.radio_search(q="kexp")
    assert "tag=jazz" in seen[0] and "tag" not in seen[1]


def test_presets_are_many_and_https():
    from revamp_core import radio
    presets = radio.presets()
    assert len(presets) >= 30
    assert all(s["url"].startswith("https://") for s in presets)
    assert len({s["url"] for s in presets}) == len(presets)


class _Req:
    """What the generic routes read from FastAPI's Request."""
    def __init__(self, query=""):
        self.url = types.SimpleNamespace(query=query)


def test_generic_sources_proxy(api):
    _wait_tracks(api, n=1)
    listed = _json(api.sources_list())
    assert {"local", "radio"} <= {s["id"] for s in listed}
    page = _json(api.sources_get("local/browse", _Req("limit=1&t=leak")))
    assert len(page["items"]) == 1 and page["items"][0]["id"].startswith("local:track:")
    one = page["items"][0]
    got = _json(api.sources_get("local/item/" + one["id"].split(":", 1)[1], _Req()))
    assert got["id"] == one["id"]
    assert _json(api.playback_state())["active"] == "webamp"
    assert api.resume_set({"key": one["id"], "position": 12}).status_code == 200
    assert _json(api.resume_get(key=one["id"]))["position"] == 12


def test_generic_proxy_is_allowlisted(api, monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_request", lambda m, path, body=None, timeout=5.0: (seen.append(path), (200, {}))[1])
    for bad in ("../state", "local/../../state", "local/%2e%2e/state", "x/%2Fapi", "local/stream/abc"):
        assert api.sources_get(bad, _Req()).status_code == 404, bad
    assert api.sources_post("../command", _Req(), {}).status_code == 404
    assert seen == []
    api.sources_get("plex@abc/browse/library", _Req("q=x&t=tok"))
    assert seen == ["/api/sources/plex@abc/browse/library?q=x"]


def test_gateway_library_tracks_passes_offset(api, monkeypatch):
    """Mini player paging also works through the Hermes gateway route (not only the direct bridge)."""
    seen = {}

    def fake_request(method, path, body=None, timeout=5.0):
        seen["path"] = path
        return 200, b"{}", "application/json"

    monkeypatch.setattr(api, "_request", fake_request)
    monkeypatch.setattr(api, "_proxied", lambda *a: a)
    api.library_tracks(q="x", limit=100, offset=200, kind="music")
    assert "offset=200" in seen["path"] and "limit=100" in seen["path"]
