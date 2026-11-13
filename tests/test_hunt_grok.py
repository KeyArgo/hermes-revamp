"""Bugs found in the grok review pass."""

from __future__ import annotations

import os

import pytest

from revamp_core import playlists as pl


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
