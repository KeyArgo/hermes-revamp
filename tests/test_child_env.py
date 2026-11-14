"""Programs REVamp starts (ffprobe, ps, the pop-out browser) get an allowlisted environment, never
Hermes's whole one: provider keys and agent settings must not reach them.

Before, every subprocess call left out ``env=``, so OPENAI_API_KEY, HERMES_YOLO_MODE and friends
were inherited by ffprobe and Chrome — the same finding the catalog review raised for other plugins."""
from __future__ import annotations

import subprocess

import pytest

from revamp_core import chapters, childenv, tags

HERMES_LIKE = {
    "PATH": "/usr/bin", "HOME": "/home/me", "LANG": "en_US.UTF-8", "LC_ALL": "C.UTF-8",
    "TMPDIR": "/tmp", "SystemRoot": r"C:\Windows", "DISPLAY": ":0", "XAUTHORITY": "/run/user/1000/xauth",
    "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "PULSE_SERVER": "unix:/run/pulse",
    # what must never leave Hermes
    "OPENAI_API_KEY": "sk-secret", "ANTHROPIC_API_KEY": "sk-ant", "OPENROUTER_API_KEY": "or",
    "GITHUB_TOKEN": "ghp_x", "HERMES_YOLO_MODE": "1", "HERMES_HOME": "/home/me/.hermes",
    "REVAMP_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "a", "XDG_SECRET_THING": "s", "DB_PASSWORD": "p",
}


def test_child_env_keeps_what_a_tool_or_window_needs():
    env = childenv.child_env(source=HERMES_LIKE)
    for name in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SystemRoot", "DISPLAY", "XAUTHORITY",
                 "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER"):
        assert env.get(name) == HERMES_LIKE[name], name


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN",
                                  "HERMES_YOLO_MODE", "HERMES_HOME", "REVAMP_TOKEN", "AWS_SECRET_ACCESS_KEY",
                                  "XDG_SECRET_THING", "DB_PASSWORD"])
def test_child_env_drops_keys_and_agent_settings(name):
    assert name not in childenv.child_env(source=HERMES_LIKE)


def test_child_env_extra_and_keep():
    env = childenv.child_env({"FOO": "1"}, keep=["CUDA_VISIBLE_DEVICES"],
                             source={"CUDA_VISIBLE_DEVICES": "0", "PATH": "/bin"})
    assert env == {"CUDA_VISIBLE_DEVICES": "0", "PATH": "/bin", "FOO": "1"}


@pytest.fixture()
def secret_in_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")


class _Done:
    returncode = 0
    stdout = b"{}"
    stderr = b""


def _capture(seen):
    def run(*a, **k):
        seen.append(k)
        return _Done()
    return run


def _assert_clean(kwargs):
    env = kwargs.get("env")
    assert env is not None, "started without env=: it inherits every Hermes key"
    assert "OPENAI_API_KEY" not in env and "HERMES_YOLO_MODE" not in env
    assert "PATH" in env


def test_tag_ffprobe_gets_a_clean_env(tmp_path, monkeypatch, secret_in_env):
    seen = []
    monkeypatch.setattr(subprocess, "run", _capture(seen))
    f = tmp_path / "a.m4a"
    f.write_bytes(b"\0" * 64)
    tags._probe_ffprobe(str(f))
    assert seen and all(_assert_clean(k) is None for k in seen)


def test_chapter_ffprobe_gets_a_clean_env(tmp_path, monkeypatch, secret_in_env):
    seen = []
    monkeypatch.setattr(subprocess, "run", _capture(seen))
    monkeypatch.setattr(chapters.shutil, "which", lambda n, *a, **k: "ffprobe")
    if hasattr(chapters, "_CACHE"):
        chapters._CACHE.clear()
    f = tmp_path / "b.m4b"
    f.write_bytes(b"\0" * 64)
    chapters.chapters_for(str(f))
    assert seen
    _assert_clean(seen[0])


def test_every_subprocess_call_in_revamp_core_passes_env():
    """A new subprocess call that forgets env= fails here, not in a reviewer's comment."""
    import ast
    import pathlib
    root = pathlib.Path(childenv.__file__).parent
    missing = []
    for path in root.glob("*.py"):
        if path.name == "version_stamp.py":   # shared verbatim with the suite's other plugins; runs git only
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}
                    and not any(kw.arg == "env" for kw in node.keywords)):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"subprocess calls without env=: {missing}"
