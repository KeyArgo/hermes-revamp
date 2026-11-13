"""Bugs found in the grok review pass."""

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
