"""Coverage for revamp_core/window.py failure/boundary branches with the
browser, processes, /proc and the store all faked out."""

from __future__ import annotations

import io
import json
import os
import subprocess
import time

import pytest
import sys

from revamp_core import window
from revamp_core.window import WindowError


@pytest.fixture()
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(window, "_proc", None)
    monkeypatch.setattr(window, "_closing", None)

    class FakeStore:
        def __init__(self):
            self.state = {"live": {"now_playing": {}}}

        def get_state(self):
            return self.state

        def update(self, partial, persist=True):
            self.state.update(partial)
            return self.state
    fs = FakeStore()
    monkeypatch.setattr(window, "store", fs)
    return fs


# ---------------------------------------------------------------------------
# browser discovery
# ---------------------------------------------------------------------------

def test_find_browser(monkeypatch, tmp_path):
    exe = tmp_path / "mybrowser"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(window.os, "environ", dict(os.environ, REVAMP_FLOAT_BROWSER=str(exe)))
    monkeypatch.setattr(window.shutil, "which", lambda n: None)
    assert window.find_browser() == str(exe)                        # isfile fallback

    monkeypatch.setattr(window.os, "environ",
                        dict(os.environ, REVAMP_FLOAT_BROWSER=str(tmp_path / "gone")))
    assert window.find_browser() is None                            # 41 both miss

    monkeypatch.setenv("REVAMP_FLOAT_BROWSER", "")                  # 42-46 loop
    names = set()
    monkeypatch.setattr(window.shutil, "which",
                        lambda n: "/usr/bin/x" if n == "chromium" else None)
    assert window.find_browser() == "/usr/bin/x"
    monkeypatch.setattr(window.shutil, "which", lambda n: None)
    assert window.find_browser() is None


def test_handoff_from_live_swallows_errors(win, monkeypatch):
    win.state["live"]["now_playing"] = {"position": "42", "status": "playing"}
    window._handoff_from_live()
    h = win.state["handoff"]
    assert h["position"] == 42.0 and h["playing"] is True

    def boom():
        raise RuntimeError("state gone")
    monkeypatch.setattr(win, "get_state", boom)
    window._handoff_from_live()                                     # 63-64


def test_lease_write_clear_oserror(win, monkeypatch):
    def boom_write(self, *a, **k):
        raise OSError("ro")
    monkeypatch.setattr("pathlib.Path.write_text", boom_write)
    window._write_lease(5)                                          # 74-75, no raise
    import pathlib as _pl
    monkeypatch.setattr(_pl.Path, "write_text", _pl.Path.write_text)
    window._clear_lease()                                           # 81-82 no lease file


# ---------------------------------------------------------------------------
# reap_orphan
# ---------------------------------------------------------------------------

def _lease(tmp_lease_val):
    # write a lease file into the fixture's data dir
    window.data_dir().joinpath(window.LEASE).write_text(
        json.dumps({"pid": tmp_lease_val, "ts": 1}), encoding="utf-8")


def test_reap_orphan_no_or_bad_lease(win, monkeypatch):
    assert window.reap_orphan() is False                            # no lease
    _lease("not-a-pid")
    assert window.reap_orphan() is False                            # ValueError

    _lease(999999999)                                                # /proc unreadable 96-97
    real_open = open

    def open_stub(file, mode="r", *a, **k):
        if str(file).startswith("/proc/"):
            raise OSError("no such pid")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr("builtins.open", open_stub)
    assert window.reap_orphan() is False


@pytest.mark.parametrize("killpg_fails,kill_fails,want", [
    (False, False, True),
    (True, False, True),                                            # 104-105
    (True, True, False),                                             # 106-107
])
@pytest.mark.skipif(sys.platform == "win32", reason="os.killpg / process groups are POSIX-only")
def test_reap_orphan_kill_paths(win, monkeypatch, killpg_fails, kill_fails, want):
    profile = str(window.data_dir() / "float-browser")
    _lease(424242)
    real_open = open

    def open_stub(file, mode="r", *a, **k):
        if str(file).startswith("/proc/"):
            return io.BytesIO(("--user-data-dir=" + profile + "\0--app=x").encode())
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr("builtins.open", open_stub)

    def killpg(pid, sig):
        if killpg_fails:
            raise OSError("no group")
    def kill(pid, sig):
        if kill_fails:
            raise OSError("no pid")
    monkeypatch.setattr(window.os, "killpg", killpg)
    monkeypatch.setattr(window.os, "kill", kill)
    assert window.reap_orphan() is want


def test_reap_orphan_foreign_profile(win, monkeypatch):
    _lease(424242)
    real_open = open

    def open_stub(file, mode="r", *a, **k):
        if str(file).startswith("/proc/"):
            return io.BytesIO(b"--user-data-dir=/someone/else\x00--app=x")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr("builtins.open", open_stub)
    assert window.reap_orphan() is False


def test_reap_orphan_uses_ps_without_proc_and_normalizes_mac_temp_paths(win, monkeypatch):
    """macOS has no /proc and represents its temp root as /private/var."""
    _lease(424242)
    real_open = open

    def open_stub(file, mode="r", *a, **k):
        if str(file).startswith("/proc/"):
            raise OSError("/proc is unavailable")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", open_stub)
    profile = str(window.data_dir() / "float-browser")
    monkeypatch.setattr(
        window.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, f"chrome --user-data-dir={profile}\n", ""),
    )
    monkeypatch.setattr(window.os.path, "realpath",
                        lambda p: "/private" + p if str(p).startswith("/var/") else str(p))
    monkeypatch.setattr(window.os, "killpg", lambda *a: None, raising=False)
    assert window.reap_orphan() is True


# ---------------------------------------------------------------------------
# open / close / claim
# ---------------------------------------------------------------------------

class FakeProc:
    """A window that stays alive: wait() always blocks past its timeout."""

    def __init__(self, poll=None):
        self._poll, self.killed = poll, False
        self.pid = 4321
        self.terminated = False

    def poll(self):
        return self._poll

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("browser", timeout or 0)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_open_window_no_browser(monkeypatch, win):
    monkeypatch.setattr(window, "find_browser", lambda: None)
    with pytest.raises(WindowError, match="no Chrome"):
        window.open_window(8114)


def test_open_window_popen_oserror(monkeypatch, win):
    monkeypatch.setattr(window, "find_browser", lambda: "/usr/bin/chromium")

    def boom(args, **kw):
        raise OSError("fork failed")
    monkeypatch.setattr(window.subprocess, "Popen", boom)
    with pytest.raises(WindowError, match="could not start"):       # 145-146
        window.open_window(8114, position=3, playing=True)
    assert win.state["handoff"]["for"] == "float"


def test_open_window_already_open_and_lease(monkeypatch, win):
    monkeypatch.setattr(window, "find_browser", lambda: "/usr/bin/chromium")
    proc = FakeProc()
    monkeypatch.setattr(window.subprocess, "Popen", lambda args, **kw: proc)
    monkeypatch.setattr(window.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    assert window.open_window(8114) == {"open": True, "already": False}
    assert window.open_window(8114) == {"open": True, "already": True}   # 128-129
    lease = window.data_dir() / window.LEASE
    assert json.loads(lease.read_text())["pid"] == 4321


def test_close_window_paths(monkeypatch, win):
    assert window.close_window() is False                           # nothing open
    proc = FakeProc(poll=None)
    monkeypatch.setattr(window, "_proc", proc)
    assert window.close_window() is True                            # 172-174 kill
    assert proc.killed and proc.terminated
    assert win.state["handoff"]["for"] == "hermes"

    dead = FakeProc(poll=1)
    monkeypatch.setattr(window, "_proc", dead)
    assert window.close_window() is False                           # already exited


def test_claim_handoff(win):
    assert window.claim_handoff("hermes") is None                   # none stored
    win.state["handoff"] = {"for": "float", "ts": time.time()}
    assert window.claim_handoff("hermes") is None                   # wrong owner
    win.state["handoff"] = {"for": "hermes", "ts": "soon"}
    assert window.claim_handoff("hermes") is None                   # 187-188 bad ts
    assert win.state["handoff"] is None                             # cleared anyway

    win.state["handoff"] = {"for": "hermes", "ts": time.time() - 30, "position": 5}
    assert window.claim_handoff("hermes")["position"] == 5
    win.state["handoff"] = {"for": "hermes", "ts": time.time() - window.HANDOFF_TTL - 1}
    assert window.claim_handoff("hermes") is None                   # too old


def test_popout_devtools_is_loopback_only_and_grants_no_origin(monkeypatch, win):
    # Review item 4: --remote-allow-origins=* let any web page drive the pop-out's
    # DevTools. The window's own client sends no Origin, so it needs no grant.
    monkeypatch.setattr(window, "find_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(window, "install_linux_desktop_identity", lambda: None)  # never the real ~/.local
    seen = {}
    def capture(args, **kw):
        seen["args"] = list(args)
        return FakeProc()
    monkeypatch.setattr(window.subprocess, "Popen", capture)
    monkeypatch.setattr(window.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    window.open_window(8114)
    args = seen["args"]
    assert not any(a.startswith("--remote-allow-origins") for a in args), args
    assert "--remote-debugging-address=127.0.0.1" in args


def test_devtools_client_sends_no_origin_header(monkeypatch):
    # Chrome refuses a DevTools websocket that carries an Origin it was not told to
    # allow; the pop-out works without --remote-allow-origins only because of this.
    sent = []
    class Sock:
        def settimeout(self, t): pass
        def sendall(self, b): sent.append(b); raise OSError("stop after handshake")
        def close(self): pass
    monkeypatch.setattr(window.socket, "create_connection", lambda *a, **k: Sock())
    with pytest.raises(Exception):
        window._cdp_calls("ws://127.0.0.1:9222/devtools/browser/x", [("Browser.getVersion", {})])
    assert sent and b"\r\nOrigin:" not in sent[0]
