"""Regression tests for the catalog-review round in this lane.

Each test names the item it holds, and fails on the code as it was before the fix.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.request

import pytest

from revamp_core import chapters, lyrics, museum, radio, version_stamp, window


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def win(monkeypatch, tmp_path):
    """window.py with its store, data dir and process slot faked (as test_cov_window does)."""
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(window, "_proc", None)
    monkeypatch.setattr(window, "_closing", None)
    monkeypatch.setattr(window, "_cdp_pipe", None)

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


class _Proc:
    """A browser process that stays running (poll None, wait times out)."""

    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise window.subprocess.TimeoutExpired("fake", timeout or 0)


def _capture_launch(monkeypatch):
    """Capture open_window's argv; returns the list it fills."""
    seen = []
    monkeypatch.setattr(window, "find_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(window, "install_linux_desktop_identity", lambda: None)
    monkeypatch.setattr(window.subprocess, "Popen",
                        lambda args, **kw: (seen.append(list(args)), _Proc())[1])
    monkeypatch.setattr(window.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    return seen


class _JsonServer:
    """A loopback HTTP server that answers every request with one JSON body."""

    def __init__(self, body):
        payload = json.dumps(body).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _dns_answers_loopback(monkeypatch, host, port):
    """Resolve *host* to 127.0.0.1:<port>: a resolver answering a private address."""
    real = socket.getaddrinfo

    def fake(name, p, *a, **k):
        if name == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real(name, p, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ---------------------------------------------------------------------------
# item 2: the Windows pop-out must not open a listening DevTools port
# ---------------------------------------------------------------------------

def test_windows_popout_opens_no_devtools_channel(monkeypatch, win):
    monkeypatch.setattr(window, "USE_DEVTOOLS", False, raising=False)
    monkeypatch.setattr(window, "USE_CDP_PIPE", False)
    seen = _capture_launch(monkeypatch)
    assert window.open_window(8114)["open"] is True
    args = seen[0]
    assert not any(a.startswith("--remote-debugging-port") for a in args), args
    assert not any(a.startswith("--remote-debugging-address") for a in args), args
    assert "--remote-debugging-pipe" not in args, args
    assert any(a.startswith("--window-size=") for a in args), "the window is sized at launch"


def test_windows_resize_reports_fixed_without_touching_devtools(monkeypatch, win):
    monkeypatch.setattr(window, "USE_DEVTOOLS", False, raising=False)
    monkeypatch.setattr(window, "_proc", type("P", (), {"poll": lambda self: None})())

    def boom():
        pytest.fail("_debug_port() must not be consulted when no DevTools channel exists")

    monkeypatch.setattr(window, "_debug_port", boom)
    out = window.resize_window(275, 400, 800, 600, 816, 640)
    assert out == {"resized": False, "width": 291, "height": 440, "reason": "fixed"}


# ---------------------------------------------------------------------------
# item 8: the Wayland desktop entry must claim REVamp's own window class
# ---------------------------------------------------------------------------

def test_wayland_entry_claims_revamps_own_class_and_drops_the_shared_one(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(window.sys, "platform", "linux")
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True)
    stale = apps / "chrome-127.0.0.1__-Default.desktop"
    stale.write_text(
        "[Desktop Entry]\nType=Application\nName=REVamp\n"
        f"Icon={tmp_path / 'data' / 'icons' / 'icon-256.png'}\n"
        "StartupWMClass=chrome-127.0.0.1__-Default\nNoDisplay=true\n", encoding="utf-8")

    window.install_linux_desktop_identity()

    classes = {}
    for entry in apps.glob("*.desktop"):
        text = entry.read_text(encoding="utf-8")
        classes[entry.name] = text.split("StartupWMClass=", 1)[1].split("\n", 1)[0]
    assert window.LINUX_WAYLAND_APP_ID == "chrome-127.0.0.1__-REVamp"
    assert classes == {
        "revamp-player.desktop": "REVamp",
        "chrome-127.0.0.1__-REVamp.desktop": "chrome-127.0.0.1__-REVamp",
    }
    assert not stale.exists(), "the entry for the shared Chrome class is removed"


def test_linux_popout_launches_with_its_own_profile_name(monkeypatch, win):
    monkeypatch.setattr(window.sys, "platform", "linux")
    monkeypatch.setattr(window, "USE_CDP_PIPE", False)
    monkeypatch.setattr(window, "USE_DEVTOOLS", True, raising=False)
    seen = _capture_launch(monkeypatch)
    window.open_window(8114)
    args = seen[0]
    assert "--class=REVamp" in args
    # The Wayland app_id ends in the profile name: "-REVamp" is ours, "-Default" is shared.
    assert "--profile-directory=REVamp" in args, args


# ---------------------------------------------------------------------------
# item 4: Radio Browser answers and redirects must be public, pinned, proxy-free
# ---------------------------------------------------------------------------

def test_radio_browser_refuses_a_private_dns_answer(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **k: answers)
    connected = []
    monkeypatch.setattr(radio, "_connect_first",
                        lambda a, t, s=None: (connected.append(a), object())[1])
    conn = radio._FirstAddressHTTPSConnection("de1.api.radio-browser.info", 443, timeout=1.0)
    with pytest.raises(radio.RadioError):
        conn.connect()
    assert connected == [], "a private answer is refused before any connection"


def test_radio_browser_opens_without_a_proxy_and_checks_redirects(monkeypatch):
    captured = {}

    class Opener:
        def open(self, req, timeout=None):
            return "response"

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(radio.urllib.request, "build_opener", build)
    req = urllib.request.Request("https://de1.api.radio-browser.info/json/stations/search")
    assert radio._rb_open(req, 4.0) == "response"
    handlers = captured["handlers"]
    assert any(isinstance(h, radio._SafeRedirect) for h in handlers), "redirects are validated"
    proxies = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert proxies and proxies[0].proxies == {}, "HTTP(S)_PROXY is not honoured"


# ---------------------------------------------------------------------------
# item 5: LRCLIB and the Skin Museum only talk to their own HTTPS hosts
# ---------------------------------------------------------------------------

def test_lyrics_refuses_a_resolver_that_answers_loopback(monkeypatch):
    # The server is plain HTTP on loopback, so a fetch that went through would fail on TLS
    # rather than on the address check: the message has to be the refusal, not a TLS error.
    srv = _JsonServer({"syncedLyrics": "[00:01.00]x", "plainLyrics": "x"})
    _dns_answers_loopback(monkeypatch, "lrclib.net", srv.port)
    try:
        with pytest.raises(lyrics.LyricsError, match="non-public"):
            lyrics._fetch({"artist_name": "A", "track_name": "B"})
    finally:
        srv.close()


def test_museum_refuses_a_resolver_that_answers_loopback(monkeypatch):
    srv = _JsonServer({"data": {"skins": {"count": 1, "nodes": []}}})
    _dns_answers_loopback(monkeypatch, "api.webamp.org", srv.port)
    try:
        with pytest.raises(museum.MuseumError, match="non-public"):
            museum._graphql("q", {})
    finally:
        srv.close()


def test_fixed_host_fetch_refuses_other_hosts_and_schemes():
    assert lyrics.LRCLIB_HOSTS == frozenset({"lrclib.net"})
    assert museum.MUSEUM_HOSTS == frozenset({"api.webamp.org", "r2.webampskins.org"})
    with pytest.raises(radio.RadioError):
        radio.open_fixed_https("https://example.com/api", lyrics.LRCLIB_HOSTS, 1.0)
    with pytest.raises(radio.RadioError):
        radio.open_fixed_https("http://lrclib.net/api/get", lyrics.LRCLIB_HOSTS, 1.0)


def test_fixed_host_redirect_off_the_host_is_refused():
    handler = radio._FixedHostRedirect({"lrclib.net"})
    req = urllib.request.Request("https://lrclib.net/api/get")
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://lrclib.net/api/get2") is not None
    with pytest.raises(radio.RadioError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x")
    with pytest.raises(radio.RadioError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://lrclib.net/api/get")


# ---------------------------------------------------------------------------
# item 6: the version chip reads only the plugin package, never Hermes's install record
# ---------------------------------------------------------------------------

def test_version_chip_ignores_the_private_install_record(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    pkg = plugins / "revamp"
    pkg.mkdir(parents=True)
    (pkg / "plugin.yaml").write_text('name: revamp\nversion: "1.0.0"\n', encoding="utf-8")
    commit = "abcdef1234567890"
    (pkg / "BUILD_INFO.json").write_text(json.dumps({
        "release": "1.0.0", "commit": commit, "commit_date": "2026-11-13T10:00:00-07:00",
    }), encoding="utf-8")
    # Hermes's private record claims this commit is the catalog pin: no longer consulted.
    (plugins / ".install-metadata.json").write_text(json.dumps({
        "revamp": {"catalog": {"pin": commit}},
    }), encoding="utf-8")
    monkeypatch.setattr(version_stamp, "_HERE", pkg)
    monkeypatch.setattr(version_stamp, "_git", lambda *a: "")

    info = version_stamp.as_dict()
    assert info["version"] == "1.0.0-dev", "no tag to prove the release: an honest dev build"
    assert version_stamp.chip_line(info) == "v1.0.0-dev · abcdef12 · 2026-11-13"


# ---------------------------------------------------------------------------
# item 12: chapter probes run at the library's low priority
# ---------------------------------------------------------------------------

def test_chapter_probe_runs_at_low_priority(tmp_path, monkeypatch):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"x")
    monkeypatch.setattr(chapters.shutil, "which",
                        lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(chapters.tags, "_background_prefix", lambda: ["nice", "-n", "19"])
    seen = {}

    class Proc:
        returncode = 0
        stdout = b'{"chapters": []}'

    def run(cmd, **kw):
        seen["cmd"] = list(cmd)
        return Proc()

    monkeypatch.setattr(chapters.subprocess, "run", run)
    chapters._CACHE.clear()
    assert chapters.chapters_for(str(path)) == []
    assert seen["cmd"][:3] == ["nice", "-n", "19"], seen["cmd"]
    assert seen["cmd"][3].endswith("ffprobe")


# ---------------------------------------------------------------------------
# #3 / #13: the disclosure says what is true per platform, and that mutagen is optional
# ---------------------------------------------------------------------------

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_disclosure_states_the_per_platform_peer_check_and_optional_mutagen():
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "refuses connections from other local accounts" in manifest
    assert "on macOS and Windows any" in manifest
    assert "mutagen is optional" in manifest
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "any other local account on the same machine can reach the loopback server" in readme
