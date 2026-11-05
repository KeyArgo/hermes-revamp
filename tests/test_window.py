"""The desktop window: open, status, handoff, close (with a fake browser)."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
import sys

from revamp_core import store, window

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the desktop pop-out window is not supported on Windows yet")


@pytest.fixture()
def fake_browser(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    script = tmp_path / "fake-chrome"
    script.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexec sleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("REVAMP_FLOAT_BROWSER", str(script))
    yield log
    window.close_window()


def test_open_status_close(server, fake_browser):
    c = server.client
    code, _, st = c.get("/api/window")
    assert code == 200 and st == {"open": False, "available": True}
    code, _, out = c.post("/api/window/open", {"position": 42.5, "playing": True})
    assert code == 200 and out["open"] is True and out["already"] is False
    time.sleep(0.3)
    args = fake_browser.read_text()
    port = server.base.rsplit(":", 1)[1]
    assert f"--app=http://127.0.0.1:{port}/?mode=winamp&float=1" in args
    assert "--user-data-dir=" in args and "float-browser" in args  # its own profile
    if window.USE_CDP_PIPE:  # Linux/macOS: DevTools over a private pipe, no port
        assert "--remote-debugging-pipe" in args and "--remote-debugging-port" not in args
    else:
        assert "--remote-debugging-port=0" in args and "--remote-debugging-address=127.0.0.1" in args
    if sys.platform.startswith("linux"):
        assert "--class=REVamp" in args
    _, _, state = c.get("/api/state")
    assert state["handoff"]["position"] == 42.5 and state["handoff"]["playing"] is True
    assert c.get("/api/window")[2]["open"] is True
    assert c.post("/api/window/open", {})[2]["already"] is True  # never two windows
    assert c.post("/api/window/close", {})[2]["closed"] is True
    assert c.get("/api/window")[2]["open"] is False
    assert c.post("/api/window/close", {})[2]["closed"] is False


def test_user_closing_the_window_leaves_a_handoff(server, fake_browser):
    server.client.post("/api/window/open", {"position": 0, "playing": False})
    store.update({"handoff": None})
    with window._lock:
        proc = window._proc
    proc.terminate()  # as if the user closed the window
    deadline = time.time() + 5
    while window.is_open() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    assert not window.is_open()
    assert isinstance(server.client.get("/api/state")[2].get("handoff"), dict)


def test_no_browser_is_501(server, monkeypatch):
    monkeypatch.setenv("REVAMP_FLOAT_BROWSER", "/nonexistent/chrome")
    code, _, body = server.client.post("/api/window/open", {})
    assert code == 501 and "REVAMP_FLOAT_BROWSER" in body["error"]
    assert server.client.get("/api/window")[2]["available"] is False


def test_handoff_is_claimed_once_and_must_be_fresh(server):
    c = server.client
    store.update({"handoff": {"position": 30, "playing": True, "ts": time.time(), "for": "hermes"}})
    assert c.post("/api/handoff/claim", {"for": "float"})[2]["handoff"] is None  # not meant for it
    first = c.post("/api/handoff/claim", {"for": "hermes"})[2]["handoff"]
    assert first["position"] == 30
    assert c.post("/api/handoff/claim", {"for": "hermes"})[2]["handoff"] is None  # gone after one claim
    store.update({"handoff": {"position": 1, "playing": True, "ts": time.time() + 3600, "for": "hermes"}})
    assert c.post("/api/handoff/claim", {"for": "hermes"})[2]["handoff"] is None  # future-dated: ignored
    store.update({"handoff": {"position": 1, "playing": True, "ts": time.time() - 999, "for": "hermes"}})
    assert c.post("/api/handoff/claim", {"for": "hermes"})[2]["handoff"] is None  # stale: ignored


def test_browser_that_exits_at_once_is_an_error(server, tmp_path, monkeypatch):
    quick = tmp_path / "quick"
    quick.write_text("#!/bin/sh\nexit 0\n")
    quick.chmod(0o755)
    monkeypatch.setenv("REVAMP_FLOAT_BROWSER", str(quick))
    code, _, body = server.client.post("/api/window/open", {})
    assert code == 501 and "exited immediately" in body["error"]
    assert server.client.get("/api/window")[2]["open"] is False


def test_non_linux_launch_does_not_pass_x11_class(server, fake_browser, monkeypatch):
    monkeypatch.setattr(window.sys, "platform", "darwin")
    server.client.post("/api/window/open", {})
    time.sleep(0.3)
    assert "--class=REVamp" not in fake_browser.read_text()


def test_linux_desktop_identity_is_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(window.sys, "platform", "linux")

    window.install_linux_desktop_identity()
    apps = home / ".local" / "share" / "applications"
    icons = tmp_path / "data" / "icons"
    mtimes = {}
    for filename, wm_class in window.LINUX_DESKTOP_ENTRIES.items():
        entry = apps / filename
        assert entry.read_text(encoding="utf-8") == (
            "[Desktop Entry]\nType=Application\nName=REVamp\n"
            f"Icon={icons / 'icon-256.png'}\n"
            f"StartupWMClass={wm_class}\nNoDisplay=true\n"
        )
        mtimes[filename] = entry.stat().st_mtime_ns
    # Never claim every Chrome window: only the pop-out's own identities.
    assert set(window.LINUX_DESKTOP_ENTRIES.values()) == {"REVamp", "chrome-127.0.0.1__-Default"}
    assert all((icons / name).is_file() for name in window._ICON_NAMES)

    window.install_linux_desktop_identity()
    assert all((apps / f).stat().st_mtime_ns == m for f, m in mtimes.items())


def test_index_declares_g2_favicon_and_app_icons():
    root = Path(__file__).resolve().parents[1]
    index = (root / "web" / "index.html").read_text(encoding="utf-8")
    for rel in ("favicon.svg", "favicon-16.png", "favicon-32.png", "icon-256.png", "manifest.webmanifest"):
        assert rel in index
    manifest = (root / "web" / "manifest.webmanifest").read_text(encoding="utf-8")
    for size in ("64x64", "128x128", "256x256"):
        assert size in manifest


def test_orphan_window_from_a_previous_server_is_closed(server, tmp_path):
    import subprocess
    # looks like our browser in /proc/<pid>/cmdline
    from revamp_core.config import data_dir
    profile = data_dir() / "float-browser"
    orphan = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)", f"--user-data-dir={profile}"],
                              start_new_session=True)
    time.sleep(0.2)
    (server.data_dir / window.LEASE).write_text(f'{{"pid": {orphan.pid}, "ts": 0}}')
    assert window.reap_orphan() is True
    orphan.wait(5)
    assert not (server.data_dir / window.LEASE).exists()
    # an unrelated process with a reused pid is left alone
    other = subprocess.Popen(["sleep", "30"])
    (server.data_dir / window.LEASE).write_text(f'{{"pid": {other.pid}, "ts": 0}}')
    assert window.reap_orphan() is False
    assert other.poll() is None
    other.kill()


def test_finds_edge_and_chrome_where_windows_and_macos_install_them(monkeypatch, tmp_path):
    # User report 11-03 (Windows, alpha.6): "no Chrome or Chromium found" though Edge ships
    # with every Windows 10/11; nothing is on PATH there.
    monkeypatch.delenv("REVAMP_FLOAT_BROWSER", raising=False)
    monkeypatch.setattr(window.shutil, "which", lambda name: None)
    monkeypatch.setattr(window, "_os_family", lambda: "windows")
    pf = tmp_path / "Program Files (x86)"
    edge = pf / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    for k in ("PROGRAMFILES", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROGRAMFILES(X86)", str(pf))
    assert window.find_browser() == str(edge)


def test_windows_skips_the_windowsapps_alias_for_a_real_install(monkeypatch, tmp_path):
    # Grok a10r7: PATH on Windows holds WindowsApps app-execution aliases (msedge.exe), stubs that
    # hand off and exit at once ("exited immediately"); the real install must win.
    monkeypatch.delenv("REVAMP_FLOAT_BROWSER", raising=False)
    alias = r"C:\Users\e\AppData\Local\Microsoft\WindowsApps\msedge.exe"
    monkeypatch.setattr(window.shutil, "which", lambda name: alias if name == "msedge" else None)
    monkeypatch.setattr(window, "_os_family", lambda: "windows")
    pf = tmp_path / "Program Files"
    chrome = pf / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    for k in ("PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(pf))
    assert window.find_browser() == str(chrome)
    chrome.unlink()  # nothing installed: the alias alone is still never used
    assert window.find_browser() is None


def test_linux_still_prefers_the_browser_on_path(monkeypatch):
    monkeypatch.delenv("REVAMP_FLOAT_BROWSER", raising=False)
    monkeypatch.setattr(window, "_os_family", lambda: "other")
    monkeypatch.setattr(window.shutil, "which", lambda name: "/usr/bin/chromium" if name == "chromium" else None)
    assert window.find_browser() == "/usr/bin/chromium"


def test_finds_chrome_in_macos_applications(monkeypatch, tmp_path):
    monkeypatch.delenv("REVAMP_FLOAT_BROWSER", raising=False)
    monkeypatch.setattr(window.shutil, "which", lambda name: None)
    monkeypatch.setattr(window, "_os_family", lambda: "mac")
    apps = tmp_path / "Applications"
    chrome = apps / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    monkeypatch.setattr(window.os.path, "expanduser", lambda p: str(apps) if p == "~/Applications" else p)
    # In the list, not necessarily first: a Mac CI machine also has the real /Applications one.
    assert str(chrome) in window._installed_browsers() and window.find_browser() == window._installed_browsers()[0]
