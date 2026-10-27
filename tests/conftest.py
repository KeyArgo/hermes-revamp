"""Shared pytest fixtures for the Revamp test-suite.

Boots the real ``revamp_core.server`` on a random free port with a temp data
dir and a temp library (tiny sine-wave audio via ffmpeg when available).
"""

from __future__ import annotations

import json
import os
import socket
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from revamp_core import library, server as srv  # noqa: E402

# shutil.which, not `which`: on Windows the shell's `which` answers with a path
# Windows can't run, and every test library silently became fake files.
FFMPEG = shutil.which("ffmpeg")

HAVE_FFMPEG = FFMPEG is not None

TRACKS = [
    # (filename, title, artist, album, ext)
    ("Song One.mp3", "Song One", "The Artists", "Great Album", "mp3"),
    ("Song Two.mp3", "Song Two", "The Artists", "Great Album", "mp3"),
    ("Other Tune.flac", "Other Tune", "Someone Else", "Ok Album", "flac"),
]


def _make_track(lib: Path, name: str, title: str, artist: str, album: str, ext: str) -> Path:
    out = lib / name
    cmd = [FFMPEG, "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "sine=d=2",
           "-metadata", f"title={title}",
           "-metadata", f"artist={artist}",
           "-metadata", f"album={album}"]
    if ext == "flac":
        cmd += ["-c:a", "flac"]
    cmd += [str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    return out


def make_library(tmp_path: Path) -> Path:
    lib = tmp_path / "music"
    lib.mkdir()
    if HAVE_FFMPEG:
        for name, title, artist, album, ext in TRACKS:
            try:
                _make_track(lib, name, title, artist, album, ext)
            except Exception:
                # An installed ffmpeg can still lack an encoder (notably in
                # minimal CI packages). The scanner deliberately accepts
                # extension-valid files, so retain the fixture's contract.
                (lib / name).write_bytes(b"\x00" * 256)
        sub = lib / "nested"
        sub.mkdir()
        try:
            _make_track(sub, "Deep Cut.mp3", "Deep Cut", "The Artists",
                        "Great Album", "mp3")
        except Exception:
            (sub / "Deep Cut.mp3").write_bytes(b"\x00" * 128)
    else:
        # Tiny fake files; the scanner indexes by extension regardless of
        # decodability, which is enough for API tests (tags fall back to
        # filename heuristics).
        for name, title, artist, album, ext in TRACKS:
            (lib / name).write_bytes(b"\x00" * 256)
        sub = lib / "nested"
        sub.mkdir()
        (sub / "Deep Cut.mp3").write_bytes(b"\x00" * 128)
    (lib / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpgdata")
    return lib


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    """Tiny test client bound to the fixture server (token prefilled)."""

    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token

    def _req(self, method: str, path: str, body=None, headers=None,
             raw: bool = False, timeout: float = 10.0):
        import urllib.parse as _up
        url = self.base + _up.quote(path, safe="/?&=:,.%[]-@~_")
        req = urllib.request.Request(url, method=method)
        if "X-Revamp-Token" not in (headers or {}):
            req.add_header("X-Revamp-Token", self.token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        if data is not None:
            req.data = data
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:  # 4xx/5xx: return, not raise
            payload = exc.read()
            try:
                parsed = payload if raw else (
                    json.loads(payload) if payload else {})
            except ValueError:
                parsed = payload
            return exc.code, dict(exc.headers or {}), parsed
        with resp_ctx as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), payload if raw else (
                json.loads(payload) if payload else {})

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self._req("POST", path, body=body, **kw)

    def put(self, path, body=None, **kw):
        return self._req("PUT", path, body=body, **kw)

    def delete(self, path, **kw):
        return self._req("DELETE", path, **kw)


class Server:
    def __init__(self, base: str, token: str, data_dir: Path, lib: Path):
        self.base = base
        self.token = token
        self.data_dir = data_dir
        self.lib = lib
        self.client = Client(base, token)

    def sse_lines(self, path: str = "/api/events", seconds: float = 2.5,
                  want: int = 1) -> list:
        """Read SSE events for a moment; returns (event, data) tuples."""
        import threading

        out: list = []
        sock = socket.create_connection(("127.0.0.1", int(self.base.rsplit(":", 1)[1])),
                                        timeout=5)
        req = (f"GET {path}?t={self.token} HTTP/1.1\r\nHost: x\r\n"
               f"Accept: text/event-stream\r\n\r\n")
        sock.sendall(req.encode())

        def reader():
            buf = b""
            try:
                sock.settimeout(0.5)
                deadline = time.time() + seconds
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    events = parse_sse(buf)
                    if len(events) >= want:
                        out.extend(events)
                        return
            except OSError:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout=seconds + 1.0)
        try:
            sock.close()
        except OSError:
            pass
        return out


def parse_sse(buf: bytes) -> list:
    events = []
    for block in buf.split(b"\n\n"):
        ev, data = None, None
        for line in block.split(b"\n"):
            if line.startswith(b"event:"):
                ev = line.split(b":", 1)[1].strip().decode()
            elif line.startswith(b"data:"):
                data = line.split(b":", 1)[1].strip().decode()
        if ev and data:
            events.append((ev, json.loads(data)))
    return events


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """Boot a real server thread: random port, temp data, temp library."""
    data_dir = tmp_path / "data"
    lib = make_library(tmp_path)
    port = _free_port()
    monkeypatch.setenv("REVAMP_HOME", str(data_dir))
    monkeypatch.setenv("REVAMP_PORT", str(port))
    monkeypatch.setenv("REVAMP_LIBRARY_DIRS", str(lib))
    # force re-read of env in this process
    monkeypatch.setattr(srv, "_started", False)
    from revamp_core import store as store_mod
    monkeypatch.setattr(store_mod, "_STATE", None)
    from revamp_core import library as lib_mod
    monkeypatch.setattr(lib_mod, "_INDEX", {})
    monkeypatch.setattr(lib_mod, "_LAST_SCAN", 0.0)
    th = threading_start(port, str(data_dir), str(lib))
    base = f"http://127.0.0.1:{port}"
    # wait for health
    deadline = time.time() + 10
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.1)
    else:
        th.httpd.shutdown()
        th.httpd.server_close()
        th.join(5)
        raise RuntimeError(f"server never became healthy: {last_err}")
    token = srv.get_token()
    token_path = data_dir / "token"
    token = token_path.read_text().strip() if token_path.exists() else token
    # The startup scan first: started late, it would replace a test's own folders.
    th.scan_thread.join(30)
    yield Server(base, token, data_dir, lib.resolve())
    # Let this test's startup scan finish, or it can land in the next test
    # (the newest scan wins, so a late one would replace that test's index).
    th.scan_thread.join(30)
    th.httpd.shutdown()
    th.httpd.server_close()
    th.join(5)
    assert not th.scan_thread.is_alive(), "startup scan still running; it would leak into the next test"
    assert not th.is_alive(), "HTTP server thread leaked into the next test"


def threading_start(port: int, data: str, lib: str):
    import threading

    # Construct synchronously so bind failures cannot be hidden in a daemon
    # thread, and retain the server for fixture teardown. Leaving hundreds of
    # listeners alive is especially costly on macOS CI.
    httpd = srv.build_server(port=port, data=data)

    def run():
        httpd.serve_forever(poll_interval=0.1)

    # kick a background scan like ensure_server does (without the global
    # _started latch, so each test gets a fresh server)
    import threading as _th

    t = _th.Thread(target=run, daemon=True)
    t.httpd = httpd
    t.start()

    def scan():
        import time as _t
        for _ in range(100):
            try:
                import urllib.request as _u
                with _u.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                _t.sleep(0.1)
        library.scan_sync([lib])

    st = _th.Thread(target=scan, daemon=True)
    st.start()
    t.scan_thread = st
    return t


@pytest.fixture(autouse=True)
def _private_backup_dir(tmp_path, monkeypatch):
    """Every test (and every server it spawns) backs up into a temp dir, never the user's real
    ~/.local/share/revamp/backups."""
    monkeypatch.setenv("REVAMP_BACKUP_DIR", str(tmp_path / "backups-sandbox"))


@pytest.fixture(autouse=True)
def _radio_browser_not_in_cooldown():
    """Each test starts with Radio Browser 'up' (radio.py remembers a full failure for a minute)."""
    from revamp_core import radio
    radio._rb_down_until = 0.0
    yield
    radio._rb_down_until = 0.0
