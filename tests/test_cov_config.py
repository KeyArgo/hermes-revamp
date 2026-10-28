"""Coverage for revamp_core/config.py: setting resolution (hermes -> env ->
default), coercions, data-dir pinning, and settings.json folding."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from revamp_core import config


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    for var in ("REVAMP_HOME", "HERMES_HOME", "REVAMP_PORT", "REVAMP_LIBRARY_DIRS",
                "REVAMP_DUCK_ON_APPROVAL", "REVAMP_DUCK_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "_PINNED_HOME", None)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    for m in ("hermes_constants", "hermes_cli", "hermes_cli.config"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# hermes home resolution / pinning
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path separators in the expected value")
def test_current_hermes_home(monkeypatch, isolated):
    monkeypatch.setenv("HERMES_HOME", "/env-home")
    assert config.current_hermes_home() == "/env-home"

    hc = types.ModuleType("hermes_constants")
    hc.get_hermes_home = lambda: __import__("pathlib").Path("/resolver-home")
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    assert config.current_hermes_home() == "/resolver-home"

    def boom():
        raise RuntimeError("no profile")
    hc.get_hermes_home = boom
    assert config.current_hermes_home() == "/env-home"   # resolver errors -> env


def test_pin_first_wins_and_data_dir(monkeypatch, isolated, tmp_path):
    config.pin_hermes_home(None)
    assert config._PINNED_HOME is None
    config.pin_hermes_home(str(isolated / "profile"))
    config.pin_hermes_home("/other")                      # first pin wins
    monkeypatch.delenv("REVAMP_HOME")
    assert config.data_dir() == isolated / "profile" / "revamp"
    assert config.data_dir().is_dir()

    monkeypatch.setattr(config, "_PINNED_HOME", None)
    monkeypatch.setenv("HERMES_HOME", str(isolated / "hh"))
    assert config.data_dir() == isolated / "hh" / "revamp"

    monkeypatch.delenv("HERMES_HOME")
    ok = config.data_dir()                                 # ~ expansion path
    assert str(ok).endswith(".hermes/revamp") or ok.exists()


def test_data_dir_mkdir_oserror(monkeypatch, isolated):
    from pathlib import Path
    real = Path.mkdir

    def boom(self, *a, **k):
        raise OSError("read-only fs")
    monkeypatch.setattr(Path, "mkdir", boom)
    d = config.data_dir()
    monkeypatch.setattr(Path, "mkdir", real)
    assert d == isolated


# ---------------------------------------------------------------------------
# setting() resolution + coercion
# ---------------------------------------------------------------------------

def _inject_hermes(monkeypatch, values):
    cli = types.ModuleType("hermes_cli")
    conf = types.ModuleType("hermes_cli.config")

    def cfg_get(_cfg, *path):
        if path[-1] not in values:
            raise KeyError(path[-1])
        return values[path[-1]]
    conf.load_config = lambda: {}
    conf.cfg_get = cfg_get
    cli.config = conf
    monkeypatch.setitem(sys.modules, "hermes_cli", cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", conf)


def test_setting_hermes_beats_env(monkeypatch, isolated):
    _inject_hermes(monkeypatch, {"port": 8222})
    monkeypatch.setenv("REVAMP_PORT", "9333")
    assert config.setting("port") == 8222


def test_setting_env_and_defaults(monkeypatch, isolated):
    monkeypatch.setenv("REVAMP_PORT", "8222")
    assert config.setting("port") == 8222
    monkeypatch.setenv("REVAMP_PORT", "99999")            # clamped
    assert config.setting("port") == 65535
    monkeypatch.setenv("REVAMP_PORT", "junk")             # ValueError -> default
    assert config.setting("port") == config.DEFAULT_PORT

    monkeypatch.setenv("REVAMP_LIBRARY_DIRS", f"a{os.pathsep} b {os.pathsep} ")
    assert config.setting("library_dirs") == ["a", "b"]
    monkeypatch.setenv("REVAMP_LIBRARY_DIRS", os.pathsep)  # all empty -> default
    assert config.setting("library_dirs") == ["~/Music"]
    monkeypatch.delenv("REVAMP_LIBRARY_DIRS")
    assert config.setting("library_dirs") == ["~/Music"]

    monkeypatch.setenv("REVAMP_DUCK_ON_APPROVAL", "YES")
    assert config.setting("duck_on_approval") is True
    monkeypatch.setenv("REVAMP_DUCK_ON_APPROVAL", "off")
    assert config.setting("duck_on_approval") is False
    monkeypatch.delenv("REVAMP_DUCK_ON_APPROVAL")
    assert config.setting("duck_on_approval") is False

    monkeypatch.setenv("REVAMP_DUCK_LEVEL", "0.5")
    assert config.setting("duck_level") == 0.5
    monkeypatch.setenv("REVAMP_DUCK_LEVEL", "9")
    assert config.setting("duck_level") == 1.0
    monkeypatch.setenv("REVAMP_DUCK_LEVEL", "-2")
    assert config.setting("duck_level") == 0.0
    monkeypatch.setenv("REVAMP_DUCK_LEVEL", "loud")       # coerces back to default
    assert config.setting("duck_level") == 0.25

    assert set(config.settings()) == {"port", "library_dirs",
                                      "duck_on_approval", "duck_level"}


# ---------------------------------------------------------------------------
# settings.json app choices
# ---------------------------------------------------------------------------

def test_folders_roundtrip_and_kinds(isolated):
    config.set_library_folders([
        {"path": "/m", "kind": "BOOKS "}, {"path": "/x", "kind": "weird"},
        {"path": None, "kind": None}, {"nopath": 1},
    ])
    raw = json.loads((isolated / "settings.json").read_text())
    assert raw["library_folders"] == [
        {"path": "/m", "kind": "books"}, {"path": "/x", "kind": "music"},
        {"path": "", "kind": "music"}, {"path": "", "kind": "music"}]
    assert config.library_dirs() == ["/m", "/x", "", ""]
    assert config.library_dirs_custom() is True
    assert config._norm_kind(None) == "music"

    config.set_library_folders([])                        # explicit empty sticks
    assert config.library_dirs() == [] and config.library_dirs_custom()


@pytest.mark.xfail(reason="BUG: set_library_folders crashes on non-dict folder entries",
                   strict=True)
def test_folders_non_dict_entry(isolated):
    config.set_library_folders(["not-a-dict"])


def test_folders_legacy_and_corrupt_settings(isolated):
    (isolated / "settings.json").write_text(json.dumps(
        {"library_dirs": ["/legacy", 3]}))
    assert config.library_folders() == [{"path": "/legacy", "kind": "music"},
                                        {"path": "3", "kind": "music"}]
    assert config.library_dirs_custom() is True

    (isolated / "settings.json").write_text("{ broken")
    assert config._load_settings() == {}
    (isolated / "settings.json").write_text("[1, 2]")     # not a dict
    assert config._load_settings() == {}
    assert config.library_dirs() == ["~/Music"]           # falls to default


def test_save_settings_oserror_cleans_tmp(isolated, monkeypatch):
    real = os.replace

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        config.set_library_dirs(["/nope"])
    monkeypatch.setattr(os, "replace", real)
    assert not list(isolated.glob(".settings-*.tmp"))     # tmp cleaned up
    assert not (isolated / "settings.json").exists()
