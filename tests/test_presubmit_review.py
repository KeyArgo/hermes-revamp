"""Regression tests for the catalog-review items fixed here.

* the data dir follows Hermes's own profile resolver (config + revampctl),
* the lease folder honours XDG_STATE_HOME,
* the backup root honours XDG_DATA_HOME (POSIX) / LOCALAPPDATA (Windows),
and each keeps its old path when those variables are unset (no data move).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from revamp_core import backup as bk
from revamp_core import config, server

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# config.data_dir(): Hermes's resolver, not the raw env var
# ---------------------------------------------------------------------------

def test_data_dir_uses_the_profile_resolver_when_unpinned(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(config, "_PINNED_HOME", None)
    hc = types.ModuleType("hermes_constants")
    hc.get_hermes_home = lambda: tmp_path / "profile-home"
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    assert config.data_dir() == tmp_path / "profile-home" / "revamp"


def test_data_dir_still_falls_back_to_the_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    monkeypatch.setattr(config, "_PINNED_HOME", None)
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # import fails -> env fallback
    assert config.data_dir() == tmp_path / "env-home" / "revamp"


def test_pinned_home_still_wins(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    monkeypatch.setattr(config, "_PINNED_HOME", tmp_path / "pinned")
    hc = types.ModuleType("hermes_constants")
    hc.get_hermes_home = lambda: tmp_path / "profile-home"
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    assert config.data_dir() == tmp_path / "pinned" / "revamp"


# ---------------------------------------------------------------------------
# bin/revampctl: same resolution order
# ---------------------------------------------------------------------------

def _load_ctl():
    name = "revampctl_under_test"
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(REPO / "bin" / "revampctl")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revampctl_uses_the_profile_resolver_before_the_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    hc = types.ModuleType("hermes_constants")
    hc.get_hermes_home = lambda: tmp_path / "profile-home"
    monkeypatch.setitem(sys.modules, "hermes_constants", hc)
    assert _load_ctl().data_dir() == tmp_path / "profile-home" / "revamp"


def test_revampctl_falls_back_to_the_env_var_without_the_module(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # ImportError -> env fallback
    assert _load_ctl().data_dir() == tmp_path / "env-home" / "revamp"


def test_revampctl_revamp_home_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "explicit"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    assert _load_ctl().data_dir() == tmp_path / "explicit"


# ---------------------------------------------------------------------------
# lease folder: XDG_STATE_HOME on POSIX, unchanged when unset
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX XDG_STATE_HOME branch")
def test_lease_dir_honours_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_LEASE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert server._lease_dir() == tmp_path / "state" / "revamp" / "run"


@pytest.mark.skipif(os.name != "posix", reason="POSIX XDG_STATE_HOME branch")
def test_lease_dir_keeps_the_old_path_without_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_LEASE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert server._lease_dir() == tmp_path / "home" / ".local" / "state" / "revamp" / "run"


# ---------------------------------------------------------------------------
# backup root: XDG_DATA_HOME on POSIX, LOCALAPPDATA on Windows, unchanged when unset
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX XDG_DATA_HOME branch")
def test_backup_root_honours_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_BACKUP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = bk.backup_root(tmp_path / "x" / "dev" / "revamp")
    assert root.parent == tmp_path / "data" / "revamp" / "backups"


@pytest.mark.skipif(os.name != "posix", reason="POSIX XDG_DATA_HOME branch")
def test_backup_root_keeps_the_old_path_without_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_BACKUP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = bk.backup_root(tmp_path / "x" / "dev" / "revamp")
    assert root.parent == tmp_path / "home" / ".local" / "share" / "revamp" / "backups"


@pytest.mark.skipif(os.name != "nt", reason="Windows LOCALAPPDATA branch")
def test_backup_root_uses_localappdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_BACKUP_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = bk.backup_root(tmp_path / "x" / "dev" / "revamp")
    assert root.parent == tmp_path / "appdata" / "revamp" / "backups"
