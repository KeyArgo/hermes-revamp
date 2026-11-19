"""Bugs found in the review pass."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

from revamp_core import playlists as pl

HOOK = Path(__file__).resolve().parent.parent / "bin" / "revamp-hook"


def _hook_mod():
    loader = importlib.machinery.SourceFileLoader("revamp_hook_grok", str(HOOK))
    spec = importlib.util.spec_from_loader("revamp_hook_grok", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "data_dir", lambda: tmp_path)
    return tmp_path


def _symlink(src, dst):
    try:
        os.symlink(src, dst)
    except OSError:
        pytest.skip("symlinks are unavailable")


def test_playlist_save_does_not_follow_a_planted_temp_symlink(home, tmp_path):
    """A predictable ``name.m3u8.tmp`` that is a symlink must not be written through."""
    secret = tmp_path / "secret"
    secret.write_text("keep-me", encoding="utf-8")
    pdir = home / "playlists"
    pdir.mkdir()
    _symlink(secret, pdir / "mix.m3u8.tmp")
    pl._write_m3u(pdir / "mix.m3u8", "Mix", [])
    assert secret.read_text(encoding="utf-8") == "keep-me"
    body = (pdir / "mix.m3u8").read_text(encoding="utf-8")
    assert body.startswith("#EXTM3U")
    assert "keep-me" not in body


def test_playlist_import_does_not_follow_a_planted_temp_symlink(home, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("keep-me", encoding="utf-8")
    pdir = home / "playlists"
    pdir.mkdir()
    _symlink(secret, pdir / ".import-tmp")
    out = pl.import_playlist("Mix", "m3u", "#EXTM3U\nhttp://example.com/a.mp3\n")
    assert secret.read_text(encoding="utf-8") == "keep-me"
    assert out is not None and out["items"]


def test_hook_child_does_not_inherit_harness_secrets(monkeypatch):
    """revampctl only needs the data dir, port and source. API keys must not go with it."""
    mod = _hook_mod()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("REVAMP_HOME", "/tmp/revamp-home")
    monkeypatch.setenv("REVAMP_PORT", "8114")
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw.get("env")

    monkeypatch.setattr(mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(mod, "read_payload", lambda: {})
    assert mod.main(["codex", "tool_end"]) == 0
    env = seen["env"]
    assert env["REVAMP_SOURCE"] == "codex"
    assert env["REVAMP_HOME"] == "/tmp/revamp-home"
    assert env["REVAMP_PORT"] == "8114"
    assert "PATH" in env
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


class _HostReq:
    def __init__(self, host: str, port: int):
        self.headers = {"Host": host}
        self.server = type("S", (), {"server_address": ("127.0.0.1", port)})()


def test_browser_host_on_port_80_omits_the_port():
    """Browsers send ``Host: 127.0.0.1`` with no port when the server is on port 80."""
    from revamp_core.server import Handler

    ok = Handler._loopback_host
    assert ok(_HostReq("127.0.0.1", 80)) is True
    assert ok(_HostReq("localhost", 80)) is True
    assert ok(_HostReq("127.0.0.1:80", 80)) is True
    assert ok(_HostReq("evil.example", 80)) is False
    assert ok(_HostReq("evil.example:80", 80)) is False
    assert ok(_HostReq("127.0.0.1.evil.example", 80)) is False
    # Any other port still has to be named: an omitted port is 80, not "whatever we bound".
    assert ok(_HostReq("127.0.0.1:8114", 8114)) is True
    assert ok(_HostReq("127.0.0.1", 8114)) is False


def test_symlink_scan_skips_macos_and_windows_system_targets():
    """realpath("/etc") is /private/etc on macOS; Windows system dirs use a drive letter."""
    from revamp_core import library

    assert library._safe_link_dir("/etc") is False
    assert library._safe_link_dir("/private/etc") is False
    assert library._safe_link_dir("/private/etc/ssh") is False
    assert library._safe_link_dir("/private/var/run") is False
    assert library._safe_link_dir("/private/var/root") is False
    assert library._safe_link_dir("/var/root") is False
    assert library._safe_link_dir("C:/Windows/System32") is False
    assert library._safe_link_dir("D:\\Windows") is False
    assert library._safe_link_dir("C:/Program Files/Common Files") is False
    assert library._safe_link_dir("C:/Users/me/AppData/Roaming") is False
    # MPD's library and a normal music drive stay reachable, including the macOS spelling.
    assert library._safe_link_dir("/var/lib/mpd/music") is True
    assert library._safe_link_dir("/private/var/lib/mpd/music") is True
    assert library._safe_link_dir("D:/Music/Albums") is True
    assert library._safe_link_dir("/mnt/nas/Music") is True


def test_walk_does_not_follow_a_link_whose_realpath_is_macos_etc(tmp_path, monkeypatch):
    from revamp_core import library

    music = tmp_path / "Music"
    music.mkdir()
    etc = tmp_path / "etc-files"
    etc.mkdir()
    (etc / "secret.mp3").write_bytes(b"\x00" * 16)
    try:
        os.symlink(etc, music / "system")
    except OSError:
        pytest.skip("symlinks are unavailable")
    real = library.os.path.realpath
    etc_real = real(str(etc))

    def wrapped(path):
        got = real(path)
        if got == etc_real:
            return "/private/etc"
        return got

    monkeypatch.setattr(library.os.path, "realpath", wrapped)
    out = []
    library._walk(music, set(), out)
    assert out == []


def test_folder_kind_follows_platform_case_folding(monkeypatch):
    """Windows and macOS treat /Music and /music as one folder. The kind must too."""
    from revamp_core import library

    monkeypatch.setattr(library.os.path, "normcase", lambda p: p.lower())
    books = {"path": "/Music/Books/Chapter.mp3", "genre": "Rock", "duration": 10}
    kinds = {"/music/books": "books", "/music": "music"}
    assert library.kind_of(books, kinds) == "audiobook"
    # The Music folder itself wins over a genre that would otherwise say "book".
    single = {"path": "/Music/Singles/a.mp3", "genre": "Audiobook", "duration": 10}
    assert library.kind_of(single, kinds) == "music"
    assert library.folder_of("/Music/Books/Chapter.mp3", ["/music", "/music/books"]) == "/music/books"


def test_set_library_folders_accepts_a_plain_path(monkeypatch, tmp_path):
    """A path string is a Music folder. A non-path entry is skipped, not a crash."""
    from revamp_core import config

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    config.set_library_folders(["/mnt/music", {"path": "/mnt/books", "kind": "books"}, 3, None])
    assert config.library_folders() == [
        {"path": "/mnt/music", "kind": "music"},
        {"path": "/mnt/books", "kind": "books"},
    ]


def test_lyrics_cache_save_does_not_follow_a_symlink(tmp_path, monkeypatch):
    from revamp_core import lyrics

    monkeypatch.setattr(lyrics, "data_dir", lambda: tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("keep-me", encoding="utf-8")
    try:
        os.symlink(secret, tmp_path / lyrics.CACHE_NAME)
    except OSError:
        pytest.skip("symlinks are unavailable")
    old = dict(lyrics._CACHE)
    lyrics._CACHE.clear()
    lyrics._CACHE["k"] = {"hit": True, "ts": 1.0, "data": {"plain": "hi"}}
    try:
        lyrics._save()
    finally:
        lyrics._CACHE.clear()
        lyrics._CACHE.update(old)
    assert secret.read_text(encoding="utf-8") == "keep-me"
    cache = tmp_path / lyrics.CACHE_NAME
    assert cache.is_file() and not cache.is_symlink()
    assert "hi" in cache.read_text(encoding="utf-8")
