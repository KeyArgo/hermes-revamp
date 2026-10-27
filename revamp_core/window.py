"""The player in its own desktop window (a Chromium "app" window).

Hermes Desktop plugins cannot open OS windows, so the Revamp server launches a
small Chrome/Chromium app window on the player page in winamp-only mode. While
it is open, Hermes parks its own player and acts as a remote control. When the
window closes (by the user, or via close()), the last known position is left as
a handoff so the player that takes over carries on from there.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import store
from .config import data_dir

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")
WINDOW_SIZE = (300, 420)  # first paint; the page then sizes the window to the player stack

_lock = threading.Lock()
_last_resize_error = ""
_proc: Optional[subprocess.Popen] = None
_closing: Optional[subprocess.Popen] = None  # closed by us: the handoff is already written
HANDOFF_TTL = 120.0
LEASE = "float-window.json"  # survives a server restart, so an orphaned window can be found
# One entry per window identity, each matching ONLY the pop-out:
# - X11: Chrome sets WM_CLASS from --class=REVamp.
# - Wayland: Chrome ignores --class; an --app window's app_id is derived from its
#   URL and profile ("chrome-127.0.0.1__-Default" for http://127.0.0.1:<any port>/),
#   verified with KWin on Chrome 150. Never "google-chrome": that would badge every
#   Chrome window with the REVamp icon.
LINUX_X11_CLASS = "REVamp"
LINUX_WAYLAND_APP_ID = "chrome-127.0.0.1__-Default"
LINUX_DESKTOP_ENTRIES = {
    "revamp-player.desktop": LINUX_X11_CLASS,
    f"{LINUX_WAYLAND_APP_ID}.desktop": LINUX_WAYLAND_APP_ID,
}
_ICON_NAMES = ("icon-64.png", "icon-128.png", "icon-256.png")
_SOURCE_ICON_DIR = Path(__file__).resolve().parent.parent / "web" / "img" / "icons"


class WindowError(Exception):
    """No usable browser, or it would not start."""


def find_browser() -> Optional[str]:
    override = os.environ.get("REVAMP_FLOAT_BROWSER")
    if override:
        return shutil.which(override) or (override if os.path.isfile(override) else None)
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    return None


def float_gpu_args() -> List[str]:
    """Return the narrowly-scoped native-Wayland tweak for the pop-out.

    Chrome's Linux default is still the X11 Ozone backend.  On NVIDIA/Wayland
    that can make its private app profile fall back to SwiftShader; selecting
    the native Wayland backend lets Chrome use the normal EGL/ANGLE GPU path.
    Do not force an ANGLE backend or ignore Chrome's driver blocklist here:
    those switches can turn a safe fallback into a crash or software Vulkan.
    """
    if (sys.platform.startswith("linux")
            and os.environ.get("REVAMP_FLOAT_GPU") != "0"
            and (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
                 or os.environ.get("WAYLAND_DISPLAY"))):
        return ["--ozone-platform=wayland"]
    return []


def is_open() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None


def _handoff_from_live() -> None:
    """Record where the desktop window was, for the Hermes player taking over."""
    try:
        live = (store.get_state().get("live") or {}).get("now_playing") or {}
        store.update({"handoff": {
            "position": float(live.get("position") or 0),
            "playing": str(live.get("status") or "").upper() == "PLAYING",
            "ts": time.time(), "for": "hermes",
        }})
    except Exception:
        pass


def _lease_path():
    return data_dir() / LEASE


def _write_lease(pid: int) -> None:
    try:
        _lease_path().write_text(json.dumps({
            "pid": pid,
            "ts": time.time(),
            "profile": str(data_dir() / "float-browser"),
        }), encoding="utf-8")
    except OSError:
        pass


def _clear_lease() -> None:
    try:
        _lease_path().unlink()
    except OSError:
        pass


def _copy_if_changed(source: Path, destination: Path) -> None:
    """Copy a bundled icon without changing a matching file's mtime."""
    if not destination.is_file() or source.read_bytes() != destination.read_bytes():
        shutil.copyfile(source, destination)


def install_linux_desktop_identity() -> None:
    """Install the private launcher metadata used to badge the pop-out on Linux.

    This deliberately runs only after the user asks to open a desktop player.
    The entry is not a launcher (``NoDisplay=true``); it associates Chrome's
    pop-out window with the REVamp icon in KDE/GNOME task switchers and docks.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        icons_dir = data_dir().resolve() / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)
        for name in _ICON_NAMES:
            _copy_if_changed(_SOURCE_ICON_DIR / name, icons_dir / name)

        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        for filename, wm_class in LINUX_DESKTOP_ENTRIES.items():
            entry = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=REVamp\n"
                f"Icon={icons_dir / 'icon-256.png'}\n"
                f"StartupWMClass={wm_class}\n"
                "NoDisplay=true\n"
            )
            destination = desktop_dir / filename
            if not destination.is_file() or destination.read_text(encoding="utf-8") != entry:
                destination.write_text(entry, encoding="utf-8")
    except OSError:
        # A read-only home must not prevent the optional player from opening.
        pass


def reap_orphan() -> bool:
    """At server start: close a window left by a previous server (gateway restart).

    Otherwise it would reconnect and play alongside the Hermes player."""
    try:
        lease = json.loads(_lease_path().read_text(encoding="utf-8"))
        pid = int(lease["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    profile = str(data_dir() / "float-browser")
    try:
        # Linux exposes argv without shell quoting, and NUL separates args.
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
    except OSError:
        # macOS has no /proc. Do not trust the lease's profile alone: a stale
        # PID could belong to an unrelated process. BSD ps provides argv for
        # the same ownership check, including leases written by older versions
        # that did not record a profile.
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, check=False, timeout=1,
            )
            cmd = result.stdout if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return False
    # Ours only if it runs on this data dir's own browser profile (PIDs get reused).
    # realpath accounts for macOS's /var -> /private/var compatibility symlink.
    profiles = {profile, os.path.realpath(profile)}
    if not any(re.search(r"(?:^|[\0\s])--user-data-dir=" + re.escape(candidate) +
                         r"(?=[\0\s]|$)", cmd) for candidate in profiles):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)  # started with start_new_session: its own group
    except (OSError, AttributeError):  # AttributeError: no killpg on Windows
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
    _clear_lease()
    return True


def _watch(proc: subprocess.Popen) -> None:
    proc.wait()
    global _proc
    with _lock:
        ours = _closing is proc
    if not ours:  # the user closed it: hand over from its last heartbeat, BEFORE reporting closed
        _handoff_from_live()
    with _lock:
        if _proc is proc:
            _proc = None
            _clear_lease()


def open_window(port: int, position: float = 0.0, playing: bool = False) -> Dict[str, Any]:
    """Open the window (or report the one already open)."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return {"open": True, "already": True}
        browser = find_browser()
        if not browser:
            raise WindowError("no Chrome or Chromium found (set REVAMP_FLOAT_BROWSER to its path)")
        store.update({"handoff": {"position": float(position or 0), "playing": bool(playing),
                                  "ts": time.time(), "for": "float"}})
        install_linux_desktop_identity()
        profile = data_dir() / "float-browser"  # its own profile: never touches the user's browser
        args: List[str] = [
            browser, f"--app=http://127.0.0.1:{int(port)}/?mode=winamp&float=1",
            f"--user-data-dir={profile}", f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
            # Localhost only, in this window's private profile. The page uses it to
            # shrink the window to the player (resizeTo is ignored on many app windows).
            "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--disable-features=Translate",
        ]
        if sys.platform.startswith("linux"):
            args.append("--class=REVamp")
        args.extend(float_gpu_args())
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise WindowError(f"could not start {browser}: {exc}") from exc
        _proc = proc  # claimed now, so a second open in the next 1.5 s sees it
    # A browser that forwards to an instance already using this profile exits at once.
    try:
        proc.wait(1.5)
        with _lock:
            if _proc is proc:
                _proc = None
        raise WindowError(f"{os.path.basename(browser)} exited immediately (is another Revamp window open?)")
    except subprocess.TimeoutExpired:
        pass
    _write_lease(proc.pid)
    threading.Thread(target=_watch, args=(proc,), name="revamp-window", daemon=True).start()
    return {"open": True, "already": False}


def close_window() -> bool:
    global _closing
    with _lock:
        proc = _proc
        if proc is None or proc.poll() is not None:
            return False
        _closing = proc
    _handoff_from_live()  # its last heartbeat, before Hermes's player takes over
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


def claim_handoff(who: str) -> Optional[Dict[str, Any]]:
    """Take (and clear) a fresh handoff meant for *who* ('float' or 'hermes'), atomically."""
    with _lock:
        h = store.get_state().get("handoff")
        if not isinstance(h, dict) or h.get("for") != who:
            return None
        store.update({"handoff": None})
    try:
        age = time.time() - float(h.get("ts"))
    except (TypeError, ValueError):
        return None
    return h if 0 <= age < HANDOFF_TTL else None


def content_outer_size(content_w: float, content_h: float, inner_w: float, inner_h: float,
                       outer_w: float, outer_h: float) -> Tuple[int, int]:
    """Outer window size whose content area equals the player stack.

    ``resizeTo`` and DevTools ``setWindowBounds`` both take the outer size,
    which includes the title bar and borders (outer − inner).
    """
    vals = (content_w, content_h, inner_w, inner_h, outer_w, outer_h)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
        raise TypeError("sizes must be numbers")
    nums = [int(round(float(v))) for v in vals]
    if any(n < 1 or n > 4200 for n in nums):
        raise ValueError("size out of range")
    cw, ch, iw, ih, ow, oh = nums
    return cw + max(0, ow - iw), ch + max(0, oh - ih)


def resize_window(content_w: float, content_h: float, inner_w: float, inner_h: float,
                  outer_w: float, outer_h: float) -> Dict[str, Any]:
    """Size the open desktop window so its content box matches the player."""
    width, height = content_outer_size(content_w, content_h, inner_w, inner_h, outer_w, outer_h)
    out: Dict[str, Any] = {"resized": False, "width": width, "height": height}
    if not is_open():
        out["reason"] = "closed"
        return out
    ow, oh = int(round(float(outer_w))), int(round(float(outer_h)))
    if abs(width - ow) <= 2 and abs(height - oh) <= 2:
        out["reason"] = "already"
        return out
    out["resized"] = _apply_outer_size(width, height)
    out["reason"] = "ok" if out["resized"] else "debugger"
    return out


def _debug_port() -> Optional[int]:
    path = data_dir() / "float-browser" / "DevToolsActivePort"
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0].strip()
        port = int(line)
    except (OSError, IndexError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _apply_outer_size(width: int, height: int) -> bool:
    """Set the app window's outer bounds through its localhost DevTools port."""
    port = _debug_port()
    if port is None:
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            version = json.loads(resp.read().decode("utf-8"))
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        targets = _cdp_calls(ws_url, [("Target.getTargets", {})])[0]
        infos = targets.get("targetInfos") or []
        page = next((t for t in infos if t.get("type") == "page" and "float=1" in str(t.get("url") or "")), None)
        if page is None:
            page = next((t for t in infos if t.get("type") == "page"), None)
        if not page or not page.get("targetId"):
            return False
        win = _cdp_calls(ws_url, [("Browser.getWindowForTarget", {"targetId": page["targetId"]})])[0]
        bounds: Dict[str, Any] = {"width": int(width), "height": int(height), "windowState": "normal"}
        current = win.get("bounds") if isinstance(win.get("bounds"), dict) else {}
        for edge in ("left", "top"):
            if isinstance(current.get(edge), (int, float)) and not isinstance(current.get(edge), bool):
                bounds[edge] = int(current[edge])
        _cdp_calls(ws_url, [("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": bounds})])
        return True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, WindowError) as exc:
        global _last_resize_error
        _last_resize_error = repr(exc)
        return False


def _cdp_calls(ws_url: str, calls: List[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Send DevTools commands on one websocket. Localhost only."""
    parts = urllib.parse.urlsplit(ws_url)
    host = parts.hostname or ""
    if parts.scheme != "ws" or host not in ("127.0.0.1", "localhost", "::1"):
        raise WindowError("debugger is not a local websocket")
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    sock = socket.create_connection((host, port), timeout=1.5)
    try:
        sock.settimeout(1.5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("debugger closed during handshake")
            buf += chunk
        head, buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("debugger refused the websocket")
        results: List[Dict[str, Any]] = []
        for i, (method, params) in enumerate(calls, start=1):
            _ws_send(sock, json.dumps({"id": i, "method": method, "params": params}))
            while True:
                text, buf = _ws_recv(sock, buf)
                msg = json.loads(text)
                if msg.get("id") != i:
                    continue
                if msg.get("error"):
                    raise WindowError(str(msg["error"]))
                results.append(msg.get("result") or {})
                break
        return results
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _ws_send(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _ws_recv(sock: socket.socket, buf: bytes) -> Tuple[str, bytes]:
    """Read one full text message. Returns leftover bytes for the next read."""
    chunks: List[bytes] = []
    while True:
        frame, opcode, fin, buf = _ws_frame(sock, buf)
        if opcode == 0x9:  # ping
            _ws_send_pong(sock, frame)
            continue
        if opcode == 0x8:
            raise OSError("debugger closed the websocket")
        if opcode in (0x1, 0x0):
            chunks.append(frame)
            if fin:
                return b"".join(chunks).decode("utf-8"), buf
        elif fin and not chunks:
            continue


def _ws_send_pong(sock: socket.socket, payload: bytes) -> None:
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytearray([0x8A])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    else:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    sock.sendall(bytes(header) + mask + masked)


def _ws_frame(sock: socket.socket, buf: bytes) -> Tuple[bytes, int, bool, bytes]:
    def take(n: int, pending: bytes) -> Tuple[bytes, bytes]:
        while len(pending) < n:
            chunk = sock.recv(max(4096, n - len(pending)))
            if not chunk:
                raise OSError("debugger closed the websocket")
            pending += chunk
        return pending[:n], pending[n:]

    pair, buf = take(2, buf)
    b0, b1 = pair[0], pair[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    ln = b1 & 0x7F
    if ln == 126:
        raw, buf = take(2, buf)
        ln = struct.unpack("!H", raw)[0]
    elif ln == 127:
        raw, buf = take(8, buf)
        ln = struct.unpack("!Q", raw)[0]
    mask = b""
    if b1 & 0x80:
        mask, buf = take(4, buf)
    data, buf = take(ln, buf)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return data, opcode, fin, buf


def status() -> Dict[str, Any]:
    return {"open": is_open(), "available": find_browser() is not None}
