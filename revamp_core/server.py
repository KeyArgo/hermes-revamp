"""Revamp HTTP server (stdlib ``http.server``, threading, 127.0.0.1 only).

Started idempotently by :func:`ensure_server` — if the port already serves a
healthy Revamp (probed via ``/api/health``) we do nothing; if a foreign app
holds the port we log a loud warning. Every ``/api/*`` route except
``/api/health`` requires the token (header ``X-Revamp-Token`` or ``?t=``).
Static routes serve ``web/``, ``web/vendor/`` and skins with path-traversal
protection. ``GET /`` injects the token as a meta tag and sends no ACAO.
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import re

from . import backup, chapters, items, library, lyrics, museum, playback, playlists, radio, sources, store, window
import base64
from .config import (data_dir, library_dirs, library_dirs_custom, library_folders,
                     set_library_folders, setting)

logger = logging.getLogger("revamp.server")

VERSION = "1.0.0-alpha.5"
APP_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = APP_ROOT / "web"

# Focus is a browser executor.  It receives commands through the normal SSE
# channel, but declaring it here lets the controller advertise/gate it.
playback.register_backend(playback.Backend("focus", ["play", "pause", "toggle", "stop", "volume"]))

_lock = threading.Lock()
_started = False


# ---------------------------------------------------------------------------
# Token file
# ---------------------------------------------------------------------------

def token_file() -> Path:
    return data_dir() / "token"


def bridge_key(token: str) -> str:
    """Key for the page's postMessage bridge: proves a request comes from the Hermes plugin."""
    import hmac, hashlib
    return hmac.new(token.encode("utf-8"), b"revamp-bridge", hashlib.sha256).hexdigest() if token else ""


def get_token() -> str:
    p = token_file()
    try:
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except OSError:
        pass
    import secrets
    tok = secrets.token_hex(16)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok)
    except OSError:
        pass
    return tok


# ---------------------------------------------------------------------------
# SSE hub
# ---------------------------------------------------------------------------

class EventHub:
    """Fans SSE events out to connected clients; tracks and prunes them."""

    def __init__(self) -> None:
        import queue as _queue
        self._queue_mod = _queue
        self._clients: List[Any] = []
        self._lk = threading.Lock()

    # A client that stops reading (asleep, wedged) must not make the server queue events
    # forever: past this backlog it is dropped, and it re-reads state when it reconnects.
    MAX_BACKLOG = 256

    def register(self) -> Any:
        q = self._queue_mod.Queue(maxsize=self.MAX_BACKLOG)
        with self._lk:
            self._clients.append(q)
        return q

    def unregister(self, q: Any) -> None:
        with self._lk:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def count(self) -> int:
        with self._lk:
            return len(self._clients)

    def broadcast(self, kind: str, payload: Dict[str, Any]) -> int:
        """Send ``event: <kind>`` to every client; returns deliveries."""
        with self._lk:
            clients = list(self._clients)
        data = json.dumps(payload, ensure_ascii=False)
        n = 0
        for q in clients:
            try:
                q.put_nowait((kind, data))
                n += 1
            except Exception:
                self.unregister(q)
        return n


HUB = EventHub()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

def _jsonify(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "Revamp/" + VERSION
    protocol_version = "HTTP/1.1"
    # Socket timeout while a request is read: a client that opens a connection and
    # stalls mid-headers or mid-body used to hold a server thread forever.
    # Long-lived responses (audio, event stream, radio) set their own afterwards.
    timeout = 30

    # -- plumbing -----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # silence stdout
        logger.debug("revamp http: " + fmt, *args)

    def _send_cors(self) -> None:
        # Hermes sandboxes both player iframes without allow-same-origin, so the
        # page's origin is "null": its module scripts, API calls, event stream and
        # media all load cross-origin and are blocked without this header. Grant
        # exactly that opaque origin and no real web origin. /api/ still requires
        # the token, which only the index page (sent acao=False) carries, and the
        # loopback Host check keeps a rebinding page from ever reading it.
        if self.headers.get("Origin") != "null":
            return
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "X-Revamp-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: Optional[Dict[str, str]] = None, acao: bool = True) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if acao:
                self._send_cors()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send_json(self, obj: Any, code: int = 200) -> None:
        self._send(code, _jsonify(obj))

    def _err(self, code: int, msg: str) -> None:
        self._send_json({"error": msg}, code)

    def _token_ok(self) -> bool:
        want = get_token()
        if not want:
            return False
        hdr = self.headers.get("X-Revamp-Token")
        import hmac
        # Bytes, not str: compare_digest raises TypeError on non-ASCII text.
        want_b = want.encode("utf-8")
        if hdr is not None and hmac.compare_digest(hdr.strip().encode("utf-8", "surrogateescape"), want_b):
            return True
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return hmac.compare_digest(str((qs.get("t") or [""])[0]).encode("utf-8", "surrogateescape"), want_b)

    def _loopback_host(self) -> bool:
        """True only when the browser still names this loopback server."""
        try:
            parsed = urllib.parse.urlsplit("//" + self.headers.get("Host", ""))
            host, port = parsed.hostname, parsed.port
            expected = int(self.server.server_address[1])
            if not host or port != expected:
                return False
            return host in ("127.0.0.1", "localhost")
        except ValueError:
            return False

    def _wants_token(self) -> bool:
        path = urllib.parse.urlsplit(self.path).path
        return path.startswith("/api/")

    def do_OPTIONS(self) -> None:  # noqa: N802
        # OPTIONS is a route too.  Do not let a hostile web origin use it to
        # probe a loopback listener with an attacker-controlled Host header.
        if not self._loopback_host():
            return self._err(421, "request host must be loopback")
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:
        # Without this every HEAD got 501, and the HEAD branches below never ran.
        self._dispatch("HEAD")

    STREAM_WRITE_TIMEOUT = 60.0  # seconds a radio listener may stop reading before we hang up
    MAX_BODY = 4 * 1024 * 1024  # JSON bodies only (folders, playlists, commands): never near this
    # A backup import carries a zip as base64 (a 50 MB zip becomes ~67 MB of
    # base64), so only that route gets a bigger budget.
    MAX_BACKUP_IMPORT_BODY = 72 * 1024 * 1024

    def _dispatch(self, method: str) -> None:
        # Reject hostile Host headers before looking at a route or request body.
        # The local HTML carries the capability token, so this is the boundary
        # that prevents DNS-rebinding pages from becoming the token's origin.
        self._body_read = False
        if not self._loopback_host():
            self.close_connection = True
            return self._err(421, "request host must be loopback")
        if self.headers.get("Transfer-Encoding"):
            # Never sent by the player; unread, its body would be parsed as the next request.
            self.close_connection = True
            return self._err(411, "send a Content-Length")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return self._err(400, "bad Content-Length")
        limit = self.MAX_BODY
        try:
            if urllib.parse.urlsplit(self.path).path == "/api/backups/import":
                limit = self.MAX_BACKUP_IMPORT_BODY
        except ValueError:
            pass
        if length > limit:
            self.close_connection = True  # don't read it
            return self._err(413, "request too large")
        try:
            self._route_dispatch(method)
            if length > 0 and not self._body_read:
                # A body nobody read (rejected early, or sent with a GET) would be read
                # as the next request on this connection.
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("revamp: handler error %s %s", method, self.path)
            try:
                self._err(500, "internal error")
            except Exception:
                pass

    def _route_dispatch(self, method: str) -> None:
        path = urllib.parse.urlsplit(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

        if path == "/api/health" and method in ("GET", "HEAD"):
            return self._send_json(_health_payload(self.server))

        if path.startswith("/api/"):
            if not self._token_ok():
                return self._err(401, "invalid or missing token")
            if method in ("GET", "HEAD"):
                return self._api_get(path, qs)
            if method == "POST":
                return self._api_post(path)
            if method == "PUT":
                return self._api_put(path)
            if method == "DELETE":
                return self._api_delete(path, qs)
            return self._err(405, "method not allowed")

        if method in ("GET", "HEAD"):
            return self._static(path, qs)
        return self._err(405, "method not allowed")

    def _body_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            self._body_read = True
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except (ValueError, OSError):
            return {}

    # -- API GET routes ---------------------------------------------------------

    def _api_get(self, path: str, qs: Dict[str, List[str]]) -> None:
        if path == "/api/events":
            return self._sse()
        if path == "/api/sources" or path.startswith("/api/sources/"):
            return self._sources("GET", path, qs, {})
        if path == "/api/playback":
            return self._send_json(playback.current())
        if path == "/api/resume":
            key = (qs.get("key") or [""])[0]
            if not key:
                return self._err(400, "key required")
            return self._send_json({"key": key, "position": sources.get_progress(key)})
        if path == "/api/library/tracks":
            limit = _int_qs(qs, "limit", 200, 1, 10000)
            offset = _int_qs(qs, "offset", 0, 0, 10_000_000)
            sort = (qs.get("sort") or ["artist"])[0]
            return self._send_json(library.search(
                q=(qs.get("q") or [""])[0],
                artist=(qs.get("artist") or [""])[0],
                album=(qs.get("album") or [""])[0],
                kind=(qs.get("kind") or [""])[0],
                limit=limit, offset=offset, sort=sort))
        if path == "/api/library/folders":
            return self._send_json(_library_folders_payload())
        if path == "/api/fs/dirs":
            return self._fs_dirs((qs.get("path") or [""])[0])
        if path == "/api/library/artists":
            return self._send_json(library.artists(kind=(qs.get("kind") or [""])[0]))
        if path == "/api/library/albums":
            return self._send_json(library.albums(artist=(qs.get("artist") or [None])[0],
                                                  kind=(qs.get("kind") or [""])[0]))
        if path in ("/api/library/chapters", "/api/library/lyrics"):
            tid = (qs.get("id") or [""])[0]
            if not tid:
                return self._err(400, "id required")
            track = library.get_track(tid)
            if track is None:
                return self._err(404, "no such track")
            if path == "/api/library/chapters":
                return self._send_json({"id": tid,
                                        "chapters": chapters.chapters_for(track["path"])})
            return self._send_json({"id": tid, **lyrics.lyrics_for(
                track.get("artist") or "", track.get("title") or "",
                track.get("album") or "", track.get("duration"))})
        if path == "/api/library/status":
            return self._send_json({
                "scanning": library.is_scanning(),
                "progress": library.scan_progress(),
                "count": len(library.all_tracks()),
                "roots": library_dirs(),
                "last_scan": library.last_scan(),
            })
        if path.startswith("/api/media/"):
            return self._media(path.rsplit("/", 1)[-1])
        if path.startswith("/api/art/"):
            return self._art(path.rsplit("/", 1)[-1])
        if path == "/api/playlists":
            return self._send_json(playlists.list_playlists())
        if path.startswith("/api/playlists/") and path.endswith("/export"):
            pid = path[len("/api/playlists/"):-len("/export")]
            if not pid:
                pl = playlists.get_playlist("export")
                if pl is not None:
                    return self._send_json(pl)
            fmt = (qs.get("format") or ["m3u8"])[0]
            out = playlists.export_playlist(pid, fmt)
            if out is None:
                return self._err(404, "no such playlist")
            fname, body, ctype = out
            return self._send(
                200, body.encode("utf-8"), "text/plain; charset=utf-8",
                extra={"Content-Disposition": f'attachment; filename="{fname}"'})
        if path.startswith("/api/playlists/"):
            pl = playlists.get_playlist(path[len("/api/playlists/"):])
            if pl is None:
                return self._err(404, "no such playlist")
            return self._send_json(pl)
        if path == "/api/radio/presets":
            return self._send_json(radio.presets())
        if path == "/api/radio/hermes":
            return self._send_json(radio.hermes_lineup())
        if path == "/api/radio/popular":
            return self._send_json(radio.popular(
                limit=_int_qs(qs, "limit", radio.POPULAR_LIMIT, 1, 200),
                offset=_int_qs(qs, "offset", 0, 0, 1000000)))
        if path == "/api/radio/browse":
            try:
                return self._send_json(radio.browse(
                    q=(qs.get("q") or [""])[0][:120],
                    tag=(qs.get("tag") or [""])[0][:80],
                    country=(qs.get("country") or [""])[0][:16],
                    language=(qs.get("language") or [""])[0][:80],
                    sort=(qs.get("sort") or ["top"])[0][:20],
                    limit=_int_qs(qs, "limit", 50, 1, 100),
                    offset=_int_qs(qs, "offset", 0, 0, 1000000)))
            except radio.RadioError as exc:
                return self._err(502, str(exc))
        if path == "/api/radio/categories":
            try:
                return self._send_json(radio.categories((qs.get("kind") or ["tags"])[0]))
            except ValueError as exc:
                return self._err(400, str(exc))
            except radio.RadioError as exc:
                return self._err(502, str(exc))
        if path == "/api/radio/search":
            try:
                out = radio.search_radiobrowser(
                    q=(qs.get("q") or [""])[0][:120], tag=(qs.get("tag") or [""])[0][:80],
                    limit=_int_qs(qs, "limit", 30, 1, 100),
                    offset=_int_qs(qs, "offset", 0, 0, 1000000))
            except radio.RadioError as exc:
                return self._err(502, str(exc))
            return self._send_json(out)
        if path == "/api/radio/favorites":
            try:
                return self._send_json(radio.favorites())
            except radio.RadioError as exc:  # damaged file: say so, never pretend the list is empty
                return self._err(500, str(exc))
        if path == "/api/radio/nowplaying":
            url = (qs.get("url") or [""])[0]
            if not url:
                return self._err(400, "url must be public http(s)")
            try:  # validates the URL itself on a cache miss: no DNS lookup per poll
                return self._send_json(radio.nowplaying(url))
            except radio.RadioError as exc:
                return self._err(400, str(exc))
        if path == "/api/radio/stream":
            return self._stream_proxy((qs.get("url") or [""])[0])
        if path == "/api/window":
            return self._send_json(window.status())
        if path == "/api/skins/museum":
            try:
                out = museum.browse(
                    q=(qs.get("q") or [""])[0],
                    offset=_int_qs(qs, "offset", 0, 0, 100000),
                    limit=_int_qs(qs, "limit", 24, 1, 60))
            except museum.MuseumError as exc:
                return self._err(502, str(exc))
            return self._send_json(out)
        if path == "/api/skins":
            return self._send_json(skins_list())
        if path == "/api/state":
            return self._send_json(store.get_state())
        if path == "/api/likes":
            return self._send_json(_likes_payload())
        if path == "/api/agent_events":
            return self._send_json(store.agent_events())
        if path == "/api/backups":
            root = backup.backup_root(data_dir())
            # "empty": a backup with no likes, favorites or playlists (a fresh install's own first
            # snapshot): the "Welcome back" offer must skip those and restore real data.
            listed = [dict(m, empty=backup.is_empty(root / m["id"])) for m in backup.list_backups(root)]
            return self._send_json({"root": str(root),
                                    "backups": listed,
                                    "data_empty": backup.is_empty(data_dir())})
        if path == "/api/backups/export":
            blob = backup.export_zip(data_dir())
            return self._send(
                200, blob, "application/zip",
                extra={"Content-Disposition": 'attachment; filename="revamp-backup.zip"'})
        return self._err(404, "unknown route")

    # -- API POST / PUT / DELETE -------------------------------------------------

    def _api_post(self, path: str) -> None:
        body = self._body_json()
        if path == "/api/library/resume":
            track_id = body.get("track_id")
            if not isinstance(track_id, str) or not track_id:
                return self._err(400, "track_id required")
            if library.get_track(track_id) is None:
                return self._err(404, "no such track")
            pos = _position(body.get("position"))
            if pos is False:
                return self._err(400, "position must be a number")
            sources.set_progress(items.local_id(track_id), pos)
            return self._send_json({"ok": True})
        if path == "/api/resume":
            # Any source's item: keyed by its resumeKey, not checked against the library.
            key = body.get("key")
            if not isinstance(key, str) or not key or len(key) > 512:
                return self._err(400, "key required")
            pos = _position(body.get("position"))
            if pos is False:
                return self._err(400, "position must be a number")
            try:
                sources.set_progress(key, pos)
            except sources.SourceError as exc:
                return self._err(exc.status, str(exc))
            return self._send_json({"ok": True})
        if path.startswith("/api/sources/"):
            return self._sources("POST", path, {}, body)
        if path == "/api/likes":
            track_id = body.get("track_id")
            if not isinstance(track_id, str) or not track_id:
                return self._err(400, "track_id required")
            liked = body.get("liked", True) is not False
            if liked and library.get_track(track_id) is None:
                return self._err(404, "no such track")
            try:
                return self._send_json(_set_liked(track_id, liked))
            except store.StoreWriteError as exc:
                return self._err(500, str(exc))
        if path == "/api/library/duration":
            # The player measured a real length (Webamp / <audio>). Token-protected
            # like every other /api route. Chapters still come from ffprobe.
            tid = body.get("id") if body.get("id") is not None else body.get("track_id")
            if isinstance(tid, str) and tid.startswith("local:track:"):
                tid = tid[len("local:track:"):]
            if not isinstance(tid, str) or not tid.strip():
                return self._err(400, "id required")
            tid = tid.strip()
            if library.get_track(tid) is None:
                return self._err(404, "no such track")
            stored = library.set_learned_duration(tid, body.get("duration"))
            if stored is None:
                return self._err(400, "duration must be a positive number")
            return self._send_json({"ok": True, "id": tid, "duration": stored})
        if path == "/api/library/rescan":
            library.scan_async(library_dirs())
            return self._send_json({"started": True})
        if path == "/api/playlists":
            name = str(body.get("name") or "").strip()
            if not name:
                return self._err(400, "name required")
            if len(name) > MAX_PLAYLIST_NAME:
                return self._err(400, f"name is longer than {MAX_PLAYLIST_NAME} characters")
            if not isinstance(body.get("items", []), list):
                return self._err(400, "items must be a list")
            pl = playlists.create_playlist(name, body.get("items") or [])
            HUB.broadcast("playlists", {"action": "created", "id": pl["id"]})
            return self._send_json(pl, 201)
        if path == "/api/playlists/import":
            name = str(body.get("name") or "").strip()
            if len(name) > MAX_PLAYLIST_NAME:
                return self._err(400, f"name is longer than {MAX_PLAYLIST_NAME} characters")
            out = playlists.import_playlist(str(body.get("name") or ""),
                                            str(body.get("format") or ""),
                                            str(body.get("text") or ""),
                                            body.get("origin_dir") if isinstance(body.get("origin_dir"), str) else None)
            if out is None:
                return self._err(400, "bad import (need name, format m3u|pls, text)")
            HUB.broadcast("playlists", {"action": "created", "id": out["id"]})
            return self._send_json(out, 201)
        if path == "/api/shutdown-for-upgrade":
            # Authenticated above.  Never use this path to touch an arbitrary listener:
            # callers first prove this is REVamp through /api/health.
            threading.Thread(target=_shutdown_for_upgrade, args=(self.server,),
                             name="revamp-upgrade-shutdown", daemon=True).start()
            return self._send_json({"ok": True})
        if path == "/api/radio/favorites":
            try:
                st = radio.add_favorite(body)
            except radio.RadioError as exc:
                return self._err(400, str(exc))
            return self._send_json(st, 201)
        if path == "/api/window/open":
            try:
                out = window.open_window(self.server.server_address[1],
                                         position=float(body.get("position") or 0),
                                         playing=bool(body.get("playing")))
            except (TypeError, ValueError):
                return self._err(400, "position must be a number")
            except window.WindowError as exc:
                return self._err(501, str(exc))
            return self._send_json(out)
        if path == "/api/window/close":
            return self._send_json({"closed": window.close_window()})
        if path == "/api/window/size":
            try:
                out = window.resize_window(
                    body.get("width"), body.get("height"),
                    body.get("innerWidth"), body.get("innerHeight"),
                    body.get("outerWidth"), body.get("outerHeight"))
            except (TypeError, ValueError):
                return self._err(400, "width and height must be numbers")
            return self._send_json(out)
        if path == "/api/handoff/claim":
            who = "float" if body.get("for") == "float" else "hermes"
            return self._send_json({"handoff": window.claim_handoff(who)})
        if path == "/api/skins/museum":
            md5 = str(body.get("md5") or "")
            name = str(body.get("name") or "")
            try:
                entry = museum.install(md5, name)
            except ValueError as exc:
                return self._err(400, str(exc))
            except museum.MuseumError as exc:
                return self._err(502, str(exc))
            return self._send_json(entry)
        if path == "/api/command":
            return self._api_command(body)
        if path == "/api/agent_event":
            ev = dict(body)
            ev["ts"] = time.time()
            ev = store.add_agent_event(ev)
            HUB.broadcast("agent", ev)
            return self._send_json({"ok": True})
        if path == "/api/heartbeat":
            return self._send_json(store.heartbeat(body.get("now_playing")))
        if path == "/api/backups/snapshot":
            return self._send_json({"backup": backup.snapshot(data_dir())})
        if path == "/api/backups/restore":
            bid = body.get("id")
            if not isinstance(bid, str) or not bid:
                return self._err(400, "id required")
            try:
                restored = store.run_locked(lambda: backup.restore(bid, data_dir()))
            except KeyError:
                return self._err(404, "no such backup")
            except ValueError as exc:  # a damaged backup: nothing was written
                return self._err(400, str(exc))
            HUB.broadcast("restored", {"restored": restored})
            return self._send_json({"restored": restored})
        if path == "/api/backups/import":
            blob = body.get("zip_b64")
            if not isinstance(blob, str):
                return self._err(400, "zip_b64 required")
            try:
                raw = base64.b64decode(blob, validate=True)
                restored = store.run_locked(lambda: backup.import_zip(raw, data_dir()))
            except (ValueError, TypeError) as exc:
                return self._err(400, str(exc))
            HUB.broadcast("restored", {"restored": restored})
            return self._send_json({"restored": restored})
        return self._err(404, "unknown route")

    def _api_put(self, path: str) -> None:
        body = self._body_json()
        if path.startswith("/api/sources/"):
            return self._sources("PUT", path, {}, body)
        if path == "/api/state":
            partial = {k: v for k, v in body.items() if k != "live"}
            bad = _bad_state_fields(partial)
            if bad:  # junk (volume "loud", playlist "oops") must never reach state.json
                return self._err(400, "invalid state field(s): " + ", ".join(bad))
            try:
                updated = store.update(partial)
            except store.StoreWriteError as exc:
                return self._err(500, str(exc))
            if any(k in partial for k in ("playlist", "current_index", "settings")):
                HUB.broadcast("state", {
                    "playlist": updated.get("playlist"),
                    "current_index": updated.get("current_index"),
                    "settings": updated.get("settings"),
                })
            return self._send_json(updated)
        if path == "/api/likes":
            # The Liked list's editor: remove and reorder hearts in one go.
            ids = body.get("ids")
            if not isinstance(ids, list):
                return self._err(400, "ids must be a list")
            shown = body.get("shown")
            try:
                new = store.set_likes_order(ids, shown if isinstance(shown, list) else None)
            except store.StoreWriteError as exc:
                return self._err(500, str(exc))
            HUB.broadcast("likes", {"ids": new})
            return self._send_json({"ids": new})
        if path == "/api/library/folders":
            folders = body.get("folders")
            if not isinstance(folders, list):
                return self._err(400, "folders must be a list")
            cleaned = _clean_library_folders(folders)
            if cleaned is None:
                return self._err(400, "folders must be existing absolute directories "
                                      "with kind music or books (a folder cannot be both)")
            if len(cleaned) != len({entry["path"] for entry in cleaned}) or len(cleaned) > MAX_LIBRARY_DIRS:
                return self._err(400, f"at most {MAX_LIBRARY_DIRS} folders may be saved")
            try:
                set_library_folders(cleaned)
            except OSError as exc:
                return self._err(500, f"could not save the folder list: {exc}")
            library.scan_async(library_dirs())
            return self._send_json(_library_folders_payload())
        if path.startswith("/api/playlists/"):
            # A bad shape must not overwrite a playlist with nothing.
            if "items" in body and not isinstance(body["items"], list):
                return self._err(400, "items must be a list")
            if "name" in body and body["name"] is not None and not isinstance(body["name"], str):
                return self._err(400, "name must be a string")
            if isinstance(body.get("name"), str) and len(body["name"].strip()) > MAX_PLAYLIST_NAME:
                return self._err(400, f"name is longer than {MAX_PLAYLIST_NAME} characters")
            out = playlists.update_playlist(path[len("/api/playlists/"):],
                                            body.get("name"), body.get("items"))
            if out is None:
                return self._err(404, "no such playlist")
            HUB.broadcast("playlists", {"action": "updated", "id": out["id"]})
            return self._send_json(out)
        return self._err(404, "unknown route")

    def _api_delete(self, path: str, qs: Dict[str, List[str]]) -> None:
        if path.startswith("/api/sources/"):
            return self._sources("DELETE", path, qs, {})
        if path == "/api/skins/museum":
            md5 = (qs.get("md5") or [""])[0]
            if not museum.remove(md5):
                return self._err(404, "no such skin")
            return self._send_json({"ok": True})
        if path == "/api/radio/favorites":
            sid = (qs.get("id") or [""])[0]
            if not sid:
                return self._err(400, "id required")
            return self._send_json({"ok": radio.remove_favorite(sid)})
        if path == "/api/likes":
            tid = (qs.get("id") or [""])[0]
            if not tid:
                return self._err(400, "id required")
            return self._send_json(_set_liked(tid, False))
        if path.startswith("/api/playlists/"):
            ok = playlists.delete_playlist(path[len("/api/playlists/"):])
            if not ok:
                return self._err(404, "no such playlist")
            HUB.broadcast("playlists", {"action": "deleted", "id": path[len("/api/playlists/"): ]})
            return self._send_json({"ok": True})
        return self._err(404, "unknown route")

    # -- commands ------------------------------------------------------------

    KNOWN_ACTIONS = playback.KNOWN_ACTIONS

    def _api_command(self, body: Dict[str, Any]) -> None:
        code, reply = playback.dispatch(body, HUB.broadcast)
        return self._send_json(reply, code)

    # -- sources ---------------------------------------------------------------

    def _sources(self, method: str, path: str, qs: Dict[str, List[str]],
                 body: Dict[str, Any]) -> None:
        """/api/sources[/<instance>[/<verb>[/<rest>]]] -> the source's methods."""
        if path in ("/api/sources", "/api/sources/"):
            return self._send_json([s.describe() for s in sources.all_sources()])
        parts = path[len("/api/sources/"):].split("/", 2)
        src = sources.get(urllib.parse.unquote(parts[0]))
        if src is None:
            return self._err(404, "no such source")
        verb = parts[1] if len(parts) > 1 else ""
        rest = urllib.parse.unquote(parts[2]) if len(parts) > 2 else ""
        params = {k: v[0] for k, v in qs.items() if v}
        params.pop("t", None)
        try:
            if method == "GET" and verb == "":
                return self._send_json(src.describe())
            if method == "GET" and verb == "browse":
                return self._send_json(src.browse(rest, params))
            if method == "GET" and verb == "search":
                return self._send_json(src.search(params.get("q") or "", params))
            if method == "GET" and verb == "item":
                it = src.item(rest)
                if it is None:
                    return self._err(404, "no such item")
                return self._send_json(it)
            if method in ("GET", "HEAD") and verb == "stream":
                return src.stream(rest, self)
            if method == "POST" and verb == "login" and rest == "start":
                return self._send_json(src.login_start(body))
            if method == "GET" and verb == "login" and rest == "poll":
                return self._send_json(src.login_poll())
            sub = verb + ("/" + rest if rest else "")
            return self._send_json(src.route(method, sub, params, body))
        except sources.SourceError as exc:
            return self._err(exc.status, str(exc))

    # -- helpers sources stream with -------------------------------------------

    def send_file(self, path: str) -> None:
        """Serve a local file with Range support (206 / 416 as browsers expect)."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._err(404, "file vanished")
        ctype = _content_type(path)
        rng = self.headers.get("Range")
        headers = {"Accept-Ranges": "bytes"}

        def stream(start: int, length: int, code: int, extra: Dict[str, str]) -> None:
            """Write a bounded slice without ever materialising it in memory."""
            headers_sent = False
            try:
                with open(path, "rb") as fh:
                    fh.seek(start)
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(length))
                    self._send_cors()
                    for key, value in extra.items():
                        self.send_header(key, value)
                    self.end_headers()
                    headers_sent = True
                    if self.command == "HEAD":
                        return
                    # A paused <audio> stops reading for as long as it likes and resumes
                    # on the same connection: no send deadline for local media.
                    conn = getattr(self, "connection", None)
                    if conn is not None:
                        conn.settimeout(None)
                    left = length
                    while left:
                        chunk = fh.read(min(64 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                    if left:
                        # The file shrank: the client still waits for the promised bytes,
                        # so the connection cannot carry another response.
                        self.close_connection = True
                    elif conn is not None:
                        # Keep-alive: the next request read gets its deadline back.
                        conn.settimeout(self.timeout)
            except (BrokenPipeError, ConnectionResetError, OSError):
                if headers_sent:
                    self.close_connection = True
                # A disappeared file or client disconnect must not kill the
                # request thread. Before headers, retain the existing 500.
                if not headers_sent:
                    try:
                        self._err(500, "unreadable file")
                    except OSError:
                        pass

        if not rng:
            return stream(0, size, 200, headers)
        m = re.match(r"bytes=(\d{0,18})-(\d{0,18})$", rng.strip())
        if not m:
            return self._send(416, b"", ctype, extra={"Content-Range": f"bytes */{size}"})
        start_s, end_s = m.groups()
        if not start_s and end_s:
            # suffix form: last N bytes
            n = int(end_s)
            if n <= 0 or size == 0:
                return self._send(416, b"", ctype,
                                  extra={"Content-Range": f"bytes */{size}"})
            start = max(0, size - n)
            end = size - 1
        else:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            if start >= size or end < start:
                return self._send(416, b"", ctype,
                                  extra={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return stream(start, length, 206, headers)

    def pipe(self, url: str, headers: Optional[Dict[str, str]] = None,
             timeout: float = 15.0) -> None:
        """Proxy an upstream URL to the page: forwards Range, keeps the upstream
        status (200/206/416) and its length/range headers. *headers* carry the
        source's credentials, which never reach the browser."""
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers=dict(headers or {}))
        if self.headers.get("Range"):
            req.add_header("Range", self.headers["Range"])
        try:
            upstream = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            code = exc.code if exc.code in (401, 403, 404, 416) else 502
            return self._err(code, f"upstream said {exc.code}")
        except Exception as exc:
            return self._err(502, f"upstream unreachable: {exc}")
        try:
            self.send_response(upstream.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if upstream.headers.get(h):
                    self.send_header(h, upstream.headers[h])
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox")
            self._send_cors()
            self.send_header("Cache-Control", "no-store")
            if not upstream.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    # -- GET helpers -----------------------------------------------------------

    def _fs_dirs(self, raw: str) -> None:
        return _fs_dirs_handler(self, raw)

    def _media(self, tid: str) -> None:
        track = library.get_track(tid)
        if not track:
            return self._err(404, "unknown track id")
        return self.send_file(track["path"])

    def _art(self, tid: str) -> None:
        from . import tags as tags_mod
        track = library.get_track(tid)
        if not track:
            return self._err(404, "unknown track id")
        data = tags_mod.read_cover(track["path"])
        if not data:
            return self._err(404, "no art")
        is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
        ctype = "image/png" if is_png else "image/jpeg"
        return self._send(200, data, ctype)

    def _stream_proxy(self, url: str) -> None:
        if not radio.safe_stream_url(url):
            return self._err(400, "url must be public http(s)")
        try:
            upstream = radio.open_stream(url, icy=False)
        except Exception as exc:
            return self._err(502, f"stream unreachable: {exc}")
        ctype = upstream.headers.get("Content-Type") or "audio/mpeg"
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            # This origin also serves the token page: a station answering text/html must
            # not get a page that runs here.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox")
            self._send_cors()
            self.send_header("Cache-Control", "no-store")
            # This response is deliberately streamed rather than chunked. Tell
            # HTTP/1.1 clients that EOF, not a missing Content-Length, ends it.
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            # A paused or vanished player stops reading; without a send timeout
            # this thread and its upstream connection would block forever.
            self.connection.settimeout(self.STREAM_WRITE_TIMEOUT)
            while True:
                chunk = upstream.read(16 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def _sse(self) -> None:
        q = HUB.register()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self._send_cors()
            self.end_headers()
            # Pings go out every 15 s; a reader that takes none for a minute is gone.
            self.connection.settimeout(self.STREAM_WRITE_TIMEOUT)
            hello = _jsonify({"hello": True})
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            last_ping = time.time()
            while True:
                try:
                    kind, data = q.get(timeout=2.0)
                except Exception:  # nothing queued
                    kind = None
                if kind is not None:
                    # Write errors end the stream: after a timed-out half frame, more
                    # writes would only corrupt it.
                    self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                if time.time() - last_ping > 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unregister(q)

    # -- static ---------------------------------------------------------------

    def _static(self, path: str, qs: Dict[str, List[str]]) -> None:
        if path in ("/", "/index.html"):
            return self._index_html()
        if path.startswith("/web/"):
            return self._serve_dir(WEB_DIR, path[len("/web/"):],
                                   allow_token=True)
        if path.startswith("/vendor/"):
            return self._serve_dir(WEB_DIR / "vendor", path[len("/vendor/"):])
        if path.startswith("/skins/builtin/"):
            return self._serve_dir(WEB_DIR / "skins", path[len("/skins/builtin/"):])
        if path.startswith("/skins/user/"):
            return self._serve_dir(data_dir() / "skins", path[len("/skins/user/"):])
        if path.startswith("/skins/museum/"):
            return self._serve_dir(data_dir() / "skins" / "museum",
                                   path[len("/skins/museum/"):])
        return self._err(404, "not found")

    def _serve_dir(self, base: Path, rel: str, allow_token: bool = False) -> None:
        rel = urllib.parse.unquote(rel).lstrip("/")
        root = base.resolve()
        try:
            target = (base / rel).resolve()
        except (ValueError, OSError):  # NUL bytes and the like
            return self._err(404, "bad path")
        # Strictly inside the served folder: its parent can hold the token
        # (data dir for /skins/user/) or source files (repo for /web/).
        if not target.is_relative_to(root):
            return self._err(404, "bad path")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._err(404, "not found")
        try:
            body = target.read_bytes()
        except OSError:
            return self._err(500, "unreadable")
        # Always revalidate: after an update the page must load the new code, not a cached copy.
        self._send(200, body, _content_type(str(target)), extra={"Cache-Control": "no-cache"})

    def _index_html(self) -> None:
        index = WEB_DIR / "index.html"
        if not index.is_file():
            return self._err(404, "web/index.html missing (lane B pending)")
        try:
            html = index.read_text(encoding="utf-8")
        except OSError:
            return self._err(500, "unreadable")
        meta = f'<meta name="revamp-token" content="{get_token()}">'
        # The Hermes plugin gets the same key from /info (gateway side, which reads the token
        # file). The page only answers bridge requests carrying it: see web/app.js.
        meta += f'<meta name="revamp-bridge-key" content="{bridge_key(get_token())}">'
        meta += f'<meta name="revamp-build" content="{web_build()}">'
        # Fill a placeholder tag if the page ships one; otherwise inject it.
        html, filled = re.subn(r'<meta\s+name="revamp-token"[^>]*>', meta, html, count=1)
        if not filled:
            if "</head>" in html:
                html = html.replace("</head>", meta + "\n</head>", 1)
            else:
                html = meta + html
        # The index carries the token: never let another origin read it.
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", acao=False,
                   extra={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Likes (local tracks)
# ---------------------------------------------------------------------------

def _likes_payload() -> Dict[str, Any]:
    ids = store.likes()
    return {"ids": ids, "tracks": library.tracks_by_ids(ids)}


def _set_liked(track_id: str, liked: bool) -> Dict[str, Any]:
    ids = store.set_liked(track_id, liked)
    HUB.broadcast("likes", {"ids": ids})  # every open player updates its hearts
    return {"ids": ids, "liked": liked}


# ---------------------------------------------------------------------------
# Skins listing + misc helpers
# ---------------------------------------------------------------------------

def _prettify(name: str) -> str:
    stem = name.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").strip()
    # Keep names that already have their own capitals ("REVamp Nous"), title-case the rest.
    return stem if any(c.isupper() for c in stem) else stem.title()


# REVamp's own skins lead the list, in this order; other bundled skins follow alphabetically.
FEATURED_SKINS = ("REVamp-Nous-Dark.wsz", "REVamp-Nous.wsz")


def skins_list() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    builtin = WEB_DIR / "skins"
    if builtin.is_dir():
        files = sorted(builtin.glob("*.wsz"), key=lambda p: (
            FEATURED_SKINS.index(p.name) if p.name in FEATURED_SKINS else len(FEATURED_SKINS), p.name.lower()))
        for p in files:
            entry = {"id": f"builtin:{p.name}", "name": _prettify(p.name),
                     "url": f"/skins/builtin/{p.name}", "builtin": True}
            shot = builtin / "previews" / (p.stem + ".png")
            if shot.is_file():
                entry["screenshot"] = f"/skins/builtin/previews/{shot.name}"
            out.append(entry)
    # Classic right after REVamp's featured skins.
    out.insert(len([e for e in out if e["id"].split(":", 1)[1] in FEATURED_SKINS]),
               {"id": "classic", "name": "Classic (Winamp base 2.91)", "url": None, "builtin": True})
    user = data_dir() / "skins"
    if user.is_dir():
        for p in sorted(user.glob("*.wsz")):
            out.append({"id": f"user:{p.name}", "name": _prettify(p.name),
                        "url": f"/skins/user/{p.name}", "builtin": False})
    out.extend(museum.installed())
    return out


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    table = {
        ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
        ".wsz": "application/octet-stream", ".ico": "image/x-icon",
        ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
        ".m4b": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/x-wav",
        ".webm": "audio/webm", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
    }
    return table.get(ext) or "application/octet-stream"


def _position(raw: Any) -> Any:
    """Seconds (>0) or None to clear; False when it isn't a number."""
    if raw is None:
        return None
    try:
        pos = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return False
    return pos if pos > 0 else None


def _int_qs(qs: Dict[str, List[str]], key: str, default: int,
            lo: int, hi: int) -> int:
    try:
        v = int((qs.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Library folders + filesystem browsing
# ---------------------------------------------------------------------------

MAX_LIBRARY_DIRS = 50
MAX_PLAYLIST_NAME = 200

# Types each client-writable state key must have (store.MERGEABLE_KEYS). None is allowed where noted.
_NUM = (int, float)
_STATE_TYPES = {
    "volume": _NUM, "balance": _NUM, "current_index": int, "shuffle": bool, "repeat": bool,
    "skin": str, "eq": dict, "layout": dict, "playlist": list, "resume": dict, "resume_at": dict,
    "settings": dict, "focus": dict, "handoff": (dict, type(None)),
}


def _bad_state_fields(partial: Dict[str, Any]) -> List[str]:
    """Names of fields whose value has the wrong type (bools are not numbers here)."""

    def non_finite(value: Any) -> bool:
        # A non-finite float (inf, -inf, nan) anywhere inside a nested
        # dict/list would serialise badly into state.json.
        if isinstance(value, bool):
            return False
        if isinstance(value, float):
            return not math.isfinite(value)
        if isinstance(value, dict):
            return any(non_finite(v) for v in value.values())
        if isinstance(value, list):
            return any(non_finite(v) for v in value)
        return False

    bad = []
    for key, value in partial.items():
        want = _STATE_TYPES.get(key)
        if want is None:
            continue  # unknown keys are dropped by store.update anyway
        if isinstance(value, bool) and want in (_NUM, int):
            bad.append(key)
        elif not isinstance(value, want):
            bad.append(key)
        elif non_finite(value):
            bad.append(key)
    return bad


def _clean_library_folders(raw: List[Any]) -> Optional[List[Dict[str, str]]]:
    """expanduser -> absolute -> exists -> normpath -> dedupe, max 50.

    Each item is a path string (a Music folder) or ``{"path", "kind"}``.
    Returns None when any entry is invalid (nothing is saved then)."""
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    real_kind: Dict[str, str] = {}  # a shortcut (symlink) to a folder is that folder
    for item in raw:
        if isinstance(item, str):
            path, kind = item, "music"
        elif isinstance(item, dict):
            path = item.get("path")
            kind = item.get("kind", "music")
        else:
            return None
        if not isinstance(path, str) or not path.strip():
            return None
        if not isinstance(kind, str) or kind.strip().lower() not in ("music", "books"):
            return None
        kind = kind.strip().lower()
        p = os.path.expanduser(path)
        if not os.path.isabs(p) or not os.path.isdir(p):
            return None
        p = os.path.normpath(p)
        real = os.path.realpath(p)
        if real_kind.setdefault(real, kind) != kind:
            return None  # one folder cannot be both Music and Books
        if p in seen:
            continue
        seen.add(p)
        out.append({"path": p, "kind": kind})
    return out


def _library_folders_payload() -> Dict[str, Any]:
    configured = library_folders()
    paths = [os.path.normpath(os.path.expanduser(str(f.get("path") or ""))) for f in configured]
    counts: Dict[str, int] = {}
    for t in library.all_tracks():  # counted under the innermost folder, like its kind
        home = library.folder_of(str(t.get("path") or ""), paths)
        if home:
            counts[home] = counts.get(home, 0) + 1
    folders = []
    for f, p in zip(configured, paths):
        folders.append({"path": p, "kind": f.get("kind") or "music",
                        "exists": os.path.isdir(p), "tracks": counts.get(p, 0)})
    return {"custom": library_dirs_custom(), "folders": folders}


def _fs_dirs_handler(request: "Handler", raw_path: str) -> None:
    path = os.path.normpath(os.path.expanduser(raw_path)) if raw_path \
        else os.path.expanduser("~")
    if not os.path.isdir(path):
        return request._err(400, "not a directory")
    try:
        entries = os.listdir(path)
    except PermissionError as exc:
        return request._err(400, f"cannot list {path}: {exc}")
    except OSError as exc:
        return request._err(400, f"cannot list {path}: {exc}")
    dirs = []
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            if not os.path.isdir(full):
                continue
        except OSError:
            continue
        dirs.append({"name": name, "path": full})
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    if parent == path:  # "/" or drive root: no parent
        parent = None
    return request._send_json({"path": path, "parent": parent, "dirs": dirs})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _health_payload(srv: Any) -> dict:
    return {"app": "revamp", "version": getattr(srv, "revamp_version", VERSION),
            "build": getattr(srv, "revamp_build", web_build()), "pid": os.getpid(),
            "started_at": getattr(srv, "revamp_started_at", time.time()),
            "owner": getattr(srv, "revamp_owner", "plugin")}


def _probe_health(port: int, token: Optional[str] = None, timeout: float = 2.0) -> Optional[dict]:
    """The /api/health payload of a Revamp on *port*, or None."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{int(port)}/api/health")
        req.add_header("Accept", "application/json")
        if token:
            req.add_header("X-Revamp-Token", token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read(4096).decode("utf-8-sig"))
            return payload if isinstance(payload, dict) and payload.get("app") == "revamp" else None
    except Exception:
        return None


def _foreign_holder(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def build_server(port: Optional[int] = None, data: Optional[str] = None,
                 version: Optional[str] = None, build: Optional[str] = None,
                 owner: str = "plugin") -> ThreadingHTTPServer:
    """Construct (not start) the ThreadingHTTPServer; daemon threads."""
    if data:
        os.environ["REVAMP_HOME"] = data
    srv = ThreadingHTTPServer(("127.0.0.1", int(port or setting("port"))), Handler)
    srv.daemon_threads = True
    srv.revamp_version = version or VERSION
    srv.revamp_build = build or web_build()
    srv.revamp_owner = "service" if owner == "service" else "plugin"
    srv.revamp_started_at = time.time()
    return srv


WATCH_INTERVAL = 5.0
_watching = False
_owned = False  # this process holds the port


def _watch_owner(port: int) -> None:
    """Start (once per process) a thread that serves the port if its owner dies."""
    global _watching, _started
    with _lock:
        if _watching:
            return
        _watching = True

    def run() -> None:
        global _watching, _started
        while True:
            time.sleep(WATCH_INTERVAL)
            health = _probe_health(port)
            if health and health.get("app") == "revamp":
                continue
            if health is None and _foreign_holder(port):
                continue  # someone else's app; ensure_server's warning covers it
            with _lock:
                _watching = False
                _started = False
            if ensure_server(port) is not None:
                if _owned:
                    logger.info("revamp: took over port %s after its owner went away", port)
                return  # we serve it now, or a fresh watcher took over the watching
            with _lock:  # lost a bind race; keep watching
                _watching = True

    threading.Thread(target=run, name="revamp-watch", daemon=True).start()


_BUILD: Tuple[float, str] = (0.0, "")


def web_build() -> str:
    """A short fingerprint of the page's files. When an install changes them, open pages
    see a new value on /api/health and reload themselves (see web/app.js)."""
    global _BUILD
    now = time.time()
    if now - _BUILD[0] < 5 and _BUILD[1]:
        return _BUILD[1]
    import hashlib
    h = hashlib.sha1()
    for f in sorted(WEB_DIR.rglob("*")):
        try:
            if f.is_file():
                st = f.stat()
                h.update(f"{f.relative_to(WEB_DIR)}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            continue
    _BUILD = (now, h.hexdigest()[:12])
    return _BUILD[1]


def _version_key(value: Any) -> Tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(value))[:3]) or (0,)


def _request_upgrade_shutdown(port: int, token: str) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{int(port)}/api/shutdown-for-upgrade",
                                     data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("X-Revamp-Token", token)
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _wait_for_port_release(port: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _foreign_holder(port):
            return True
        time.sleep(0.05)
    return False


def _shutdown_for_upgrade(srv: ThreadingHTTPServer) -> None:
    """Release the listener after its acknowledgement has reached the upgrader."""
    time.sleep(0.05)
    srv.shutdown()
    srv.server_close()


def ensure_server(port: Optional[int] = None, *, owner: str = "plugin",
                  version: Optional[str] = None, build: Optional[str] = None) -> Optional[int]:
    """Idempotently start the server. Returns the bound port, or None.

    If the port already serves a healthy Revamp, do nothing. If a foreign app
    holds it, log a loud warning (like pixel-office) and return None.
    """
    global _started, _owned
    port = int(port or setting("port"))
    token = get_token()  # ensure the token file exists before any client reads it
    version = version or VERSION
    build = build or web_build()
    with _lock:
        if _started:
            return port
        _started = True
    # Health deliberately works before authentication; keeping the probe token-free
    # also recognises a compatible instance while its token file is being rotated.
    health = _probe_health(port)
    if health:
        incumbent_owner = health.get("owner")
        incumbent_version = _version_key(health.get("version"))
        local_version = _version_key(version)
        # Pre-handover servers only advertised ``app``.  They are compatible
        # incumbents, but have no upgrade protocol metadata; watch and take
        # over if they disappear instead of rejecting the caller outright.
        legacy_incumbent = not any(health.get(key) for key in ("owner", "version", "build"))
        should_yield = owner != "service" and incumbent_owner == "service" and incumbent_version >= local_version
        should_upgrade = (local_version > incumbent_version or build != health.get("build") or
                          (owner == "service" and incumbent_owner != "service"))
        if should_upgrade and _request_upgrade_shutdown(port, token) and _wait_for_port_release(port):
            health = None
        elif legacy_incumbent or should_yield or not should_upgrade:
            logger.info("revamp: healthy %s already owns port %s; watching it", incumbent_owner or "server", port)
            _watch_owner(port)
            return port
        else:
            logger.warning("revamp: healthy older REVamp on port %s would not hand over", port)
            with _lock:
                _started = False
            return None
    if health is None and _foreign_holder(port):
        logger.warning(
            "revamp: port %s is held by a NON-Revamp app; set "
            "plugins.entries.revamp.port in config.yaml (or $REVAMP_PORT) "
            "and restart.", port)
        with _lock:
            _started = False
        return None
    try:
        srv = build_server(port, version=version, build=build, owner=owner)
    except OSError as exc:
        with _lock:
            _started = False
        logger.error("revamp: cannot bind 127.0.0.1:%s (%s)", port, exc)
        return None
    _owned = True
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, name="revamp-http", daemon=True)
    t.start()
    # A desktop window left by the previous server would play alongside Hermes.
    window.reap_orphan()
    # A fresh install (no library index yet) starts from the saved copy instead of
    # re-reading every song's tags.
    try:
        if backup.seed_index(data_dir()):
            logger.info("revamp: library index restored from the backup copy")
    except Exception:
        logger.exception("revamp: could not seed the library index")
    # Warm the library index; scan in background if empty/stale.
    library.load_index()
    # Rolling private backups of the user's data (outside ~/.hermes).
    try:
        backup.start_thread(data_dir())
    except Exception:  # backups must never keep the server from serving
        logger.exception("revamp: backup thread failed to start")
    # Always reconcile the saved index with the filesystem. Files already read
    # come from the cache, so this is incremental after a normal restart.
    library.scan_async(library_dirs())
    logger.info("revamp: serving at http://127.0.0.1:%s", port)
    return port


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m revamp_core.server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--library", action="append", default=None,
                    help="library dir; repeatable")
    ap.add_argument("--data", default=None, help="data dir override")
    args = ap.parse_args()
    if args.data:
        os.environ["REVAMP_HOME"] = args.data
    if args.library:
        os.environ["REVAMP_LIBRARY_DIRS"] = os.pathsep.join(args.library)
    # The optional systemd unit is the durable owner.  Plugin-loaded instances
    # yield to it when it is healthy at the same or a newer release.
    port = ensure_server(args.port, owner="service")
    if port is None:
        raise SystemExit(2)
    # Standalone: scan synchronously once so the index is warm immediately.
    library.scan_sync([str(x) for x in library_dirs()])
    print(f"revamp serving http://127.0.0.1:{port} (token {token_file()})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
