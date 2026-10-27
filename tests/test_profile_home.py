"""One Hermes gateway serving many profiles (multiplexing): Revamp must use its own profile's home,
even from server threads that run outside the profile scope."""

from __future__ import annotations

import sys
import threading
import types

from revamp_core import config


def test_pinned_profile_home_wins_over_gateway_env(tmp_path, monkeypatch):
    default_home, dev_home = tmp_path / "default", tmp_path / "profiles" / "dev"
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(default_home))          # the gateway process's own home
    fake = types.ModuleType("hermes_constants")
    fake.get_hermes_home = lambda: dev_home                         # the plugin's load scope
    monkeypatch.setitem(sys.modules, "hermes_constants", fake)
    monkeypatch.setattr(config, "_PINNED_HOME", None)
    config.pin_hermes_home(config.current_hermes_home())             # what register() does
    seen = []
    t = threading.Thread(target=lambda: seen.append(config.data_dir()))  # a server thread: no scope
    t.start(); t.join()
    assert seen == [dev_home / "revamp"]
    config.pin_hermes_home(str(tmp_path / "other"))                  # first pin wins
    assert config.data_dir() == dev_home / "revamp"


def test_no_pin_keeps_the_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.delenv("REVAMP_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(config, "_PINNED_HOME", None)
    assert config.data_dir() == tmp_path / "revamp"


def test_revamp_home_still_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "x"))
    monkeypatch.setattr(config, "_PINNED_HOME", tmp_path / "pinned")
    assert config.data_dir() == tmp_path / "x"
