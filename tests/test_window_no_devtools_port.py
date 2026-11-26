"""The pop-out never opens a listening DevTools port.

On Linux/macOS the window's DevTools channel is a private pipe, which needs a Python
interpreter to hand the pipe ends over. Where there is none (sys.executable empty, as in
some embedded Pythons) the pop-out used to fall back to --remote-debugging-port=0: a
loopback socket any local account can connect to. It now opens no DevTools at all and
keeps its launch size, as on Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from revamp_core import window

ROOT = Path(__file__).resolve().parents[1]

from tests.test_presubmit_fixes import _capture_launch, win  # noqa: E402,F401  (fixture)


def _flags_with_executable(value: str):
    code = ("import sys; sys.executable = %r\n"
            "from revamp_core import window\n"
            "print(window.USE_DEVTOOLS, window.USE_CDP_PIPE)") % value
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
                         text=True, timeout=60, check=True)
    return out.stdout.split()


@pytest.mark.skipif(os.name != "posix", reason="the pipe/port choice is POSIX only")
def test_no_python_to_pass_the_pipe_means_no_devtools():
    assert _flags_with_executable("") == ["False", "False"]
    assert _flags_with_executable(sys.executable) == ["True", "True"]


def test_no_pipe_opens_no_port_whatever_the_flags(monkeypatch, win):  # noqa: F811
    monkeypatch.setattr(window, "USE_CDP_PIPE", False)
    monkeypatch.setattr(window, "USE_DEVTOOLS", True, raising=False)
    seen = _capture_launch(monkeypatch)
    assert window.open_window(8114)["open"] is True
    args = seen[0]
    assert not any(a.startswith("--remote-debugging") for a in args), args


def test_resize_without_a_channel_reports_fixed(monkeypatch, win):  # noqa: F811
    monkeypatch.setattr(window, "USE_CDP_PIPE", False)
    monkeypatch.setattr(window, "USE_DEVTOOLS", False, raising=False)
    monkeypatch.setattr(window, "_proc", type("P", (), {"poll": lambda self: None})())
    out = window.resize_window(275, 400, 800, 600, 816, 640)
    assert out["resized"] is False and out["reason"] == "fixed"
