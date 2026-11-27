"""Sibling-plugin hints: the /api/siblings endpoint, the desktop navigate
bridge's contract, and where the web views are allowed to mention them."""

from __future__ import annotations

import json
from pathlib import Path

from revamp_core import server as srv

PLUGIN_JS = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"
WEB = Path(__file__).resolve().parents[1] / "web"


# -- /api/siblings -----------------------------------------------------------

def test_studio_songs_dir_needs_real_wavs(tmp_path, monkeypatch):
    monkeypatch.delenv("HEWN_STUDIO_DATA_DIR", raising=False)
    home = tmp_path / "hermes"
    studio = home / "plugin-data" / "hermes-music-studio"
    assert srv._studio_songs_dir(str(home)) is None  # no dir at all

    studio.mkdir(parents=True)
    assert srv._studio_songs_dir(str(home)) is None  # dir but no takes

    (studio / "take-1.wav").write_bytes(b"x")
    assert srv._studio_songs_dir(str(home)) == str(studio)


def test_studio_songs_dir_follows_the_library_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("HEWN_STUDIO_DATA_DIR", raising=False)
    home = tmp_path / "hermes"
    studio = home / "plugin-data" / "hermes-music-studio"
    studio.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "song.wav").write_bytes(b"x")
    (studio / "library.json").write_text(json.dumps({"path": str(elsewhere)}), encoding="utf-8")
    assert srv._studio_songs_dir(str(home)) == str(elsewhere)
    # a pointer at a missing folder falls back to the data dir itself
    (studio / "take.wav").write_bytes(b"x")
    (studio / "library.json").write_text(json.dumps({"path": str(tmp_path / "gone")}), encoding="utf-8")
    assert srv._studio_songs_dir(str(home)) == str(studio)


def test_siblings_route_serves_the_dir_or_null(server, monkeypatch):
    code, _, body = server.client.get("/api/siblings")
    assert code == 200
    assert "studio_songs_dir" in body  # a real home may or may not have Studio
    monkeypatch.setattr(srv, "_studio_songs_dir", lambda: "/srv/studio")
    code, _, body = server.client.get("/api/siblings")
    assert (code, body["studio_songs_dir"]) == (200, "/srv/studio")


# -- the desktop bridge: the one door from the web UI to the host ------------

def _plugin_source() -> str:
    return PLUGIN_JS.read_text(encoding="utf-8")


def test_desktop_bridge_uses_the_public_host_route_and_nothing_else():
    src = _plugin_source()
    # the exact route, built once, from an allowlisted id
    assert "const SIBLING_PLUGIN_IDS = ['aura', 'hermes-music-studio']" in src
    assert "host.navigate(`/capabilities?tab=plugins&plugin=${encodeURIComponent(e.data.plugin)}`)" in src
    # only REVamp's own frames may ask; a host without a router is harmless
    assert "e.source === frame?.contentWindow || e.source === controlFrame?.contentWindow" in src
    assert "catch { /* no router, no move */ }" in src


def test_desktop_bridge_touches_no_host_dom():
    src = _plugin_source()
    start = src.index("revamp:open-plugin")
    block = src[start:src.index("revamp:toast", start)]
    for host_write in ("document.", "document.body", "window.open", "contentDocument"):
        assert host_write not in block, host_write


# -- where the web views may speak -------------------------------------------

def test_web_views_use_the_shared_hint_module():
    agent = (WEB / "views" / "agent.js").read_text(encoding="utf-8")
    # Aura only where the user turned the visualizer (MilkDrop) off, never otherwise
    assert 'import { AURA_LINE, mountHint } from "./siblings.js";' in agent
    assert "settings.milkdropOn === false" in agent

    library = (WEB / "views" / "library.js").read_text(encoding="utf-8")
    assert 'key: "hermes-music-studio"' in library  # empty library + the ... menu
    assert "showStudioHint(false)" in library and "showStudioHint(true)" in library

    playlists = (WEB / "views" / "playlists.js").read_text(encoding="utf-8")
    assert 'key: "hermes-music-studio"' in playlists  # no-playlists-yet state

    siblings = (WEB / "views" / "siblings.js").read_text(encoding="utf-8")
    assert "document.body" not in siblings  # the card lives in the view's own tree