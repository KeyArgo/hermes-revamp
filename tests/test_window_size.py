"""Desktop pop-out: the window is sized to the player, including via DevTools."""

from __future__ import annotations

import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from revamp_core import window
from revamp_core.window import WindowError

REPO = Path(__file__).resolve().parents[1]


def test_content_outer_size_adds_chrome_only():
    assert window.content_outer_size(275, 464, 800, 600, 816, 639) == (291, 503)
    assert window.content_outer_size(275, 464, 275, 464, 275, 464) == (275, 464)
    # Inner larger than outer (a bad reading): do not invent negative chrome.
    assert window.content_outer_size(275, 464, 900, 700, 800, 600) == (275, 464)


@pytest.mark.parametrize("bad", [True, "275", None, 0, -1, 9000])
def test_content_outer_size_rejects_bad_numbers(bad):
    with pytest.raises((TypeError, ValueError)):
        window.content_outer_size(bad, 464, 800, 600, 816, 639)


def test_resize_when_closed_does_not_touch_a_browser(monkeypatch):
    monkeypatch.setattr(window, "_proc", None)
    called = []
    monkeypatch.setattr(window, "_apply_outer_size", lambda w, h: called.append((w, h)) or True)
    out = window.resize_window(275, 400, 800, 600, 816, 640)
    assert out["resized"] is False and out["reason"] == "closed"
    assert out["width"] == 291 and out["height"] == 440
    assert called == []


def test_resize_skips_when_the_outer_size_already_matches(monkeypatch):
    monkeypatch.setattr(window, "_proc", type("P", (), {"poll": lambda self: None})())
    called = []
    monkeypatch.setattr(window, "_apply_outer_size", lambda w, h: called.append((w, h)) or True)
    # Content already fills the window: inner is the stack, outer adds 16 x 40 of chrome.
    out = window.resize_window(275, 400, 275, 400, 291, 440)
    assert out == {"resized": False, "width": 291, "height": 440, "reason": "already"}
    assert called == []


def test_resize_api_rejects_a_bad_body_and_reports_closed(server):
    code, _, body = server.client.post("/api/window/size", {"width": "wide"})
    assert code == 400 and "number" in body["error"]
    code, _, body = server.client.post("/api/window/size", {
        "width": 275, "height": 400,
        "innerWidth": 800, "innerHeight": 600,
        "outerWidth": 816, "outerHeight": 640,
    })
    assert code == 200 and body["resized"] is False and body["reason"] == "closed"
    assert body["width"] == 291 and body["height"] == 440


class _FakeDevtools(threading.Thread):
    """Just enough of Chrome's DevTools HTTP + websocket for one resize."""

    def __init__(self):
        super().__init__(daemon=True)
        self.bounds = None
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(5)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self.error = ""

    def run(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._serve(conn)
            except Exception as exc:
                self.error = repr(exc)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self, conn):
        data = b""
        conn.settimeout(3)
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
        if head.startswith("GET /json/version"):
            body = json.dumps({
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/browser/test",
            }).encode()
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            )
            return
        if "Upgrade: websocket" not in head:
            return
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: test\r\n\r\n"
        )
        payload = json.loads(_read_client_frame(conn).decode())
        method = payload["method"]
        if method == "Target.getTargets":
            result = {"targetInfos": [{
                "targetId": "page-1", "type": "page",
                "url": "http://127.0.0.1:9/?mode=winamp&float=1",
            }]}
        elif method == "Browser.getWindowForTarget":
            result = {"windowId": 7, "bounds": {"left": 12, "top": 34, "width": 300, "height": 420, "windowState": "normal"}}
        elif method == "Browser.setWindowBounds":
            self.bounds = payload["params"]["bounds"]
            result = {}
        else:
            result = {}
        _send_server_frame(conn, json.dumps({"id": payload["id"], "result": result}))


def _readn(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


def _read_client_frame(conn):
    b0, b1 = _readn(conn, 2)
    ln = b1 & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", _readn(conn, 2))[0]
    elif ln == 127:
        ln = struct.unpack("!Q", _readn(conn, 8))[0]
    mask = _readn(conn, 4) if b1 & 0x80 else b""
    data = _readn(conn, ln)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    assert b0 & 0x0F == 1
    return data


def _send_server_frame(conn, text):
    data = text.encode()
    n = len(data)
    header = bytes([0x81, n]) if n < 126 else bytes([0x81, 126]) + struct.pack("!H", n)
    conn.sendall(header + data)


def test_resize_uses_devtools_and_keeps_the_window_position(monkeypatch, tmp_path):
    fake = _FakeDevtools()
    fake.start()
    monkeypatch.setattr(window, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(window, "_proc", type("P", (), {"poll": lambda self: None})())
    profile = tmp_path / "float-browser"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text(f"{fake.port}\n/devtools/browser/test\n", encoding="utf-8")
    try:
        out = window.resize_window(275, 400, 800, 600, 816, 640)
    finally:
        fake.close()
    assert out["resized"] is True and out["reason"] == "ok", (window._last_resize_error, fake.error)
    assert out["width"] == 291 and out["height"] == 440
    assert fake.bounds["width"] == 291 and fake.bounds["height"] == 440
    assert fake.bounds["windowState"] == "normal"
    assert fake.bounds["left"] == 12 and fake.bounds["top"] == 34


def test_cdp_refuses_a_non_local_debugger():
    with pytest.raises(WindowError):
        window._cdp_calls("ws://example.com/devtools/browser/x", [("Target.getTargets", {})])


def test_desktop_page_pins_player_and_offers_milkdrop():
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    player = (REPO / "web" / "player.js").read_text(encoding="utf-8")
    css = (REPO / "web" / "app.css").read_text(encoding="utf-8")
    assert "function floatLocksDrag" in app
    assert "function fitDesktopWindow" in app
    assert 'btn.id = "float-milkdrop"' in app
    assert "api.layoutDesktop" in player
    assert 'return "milkdropOpenDesktop"' in player
    assert "visualizerPlacement !== \"desktop\"" in player or 'visualizerPlacement !== "desktop"' in player
    assert "float-desktop" in css and "#0b0e14" in css
    assert ".float-milkdrop" in css
