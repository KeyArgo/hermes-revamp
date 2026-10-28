"""Revamp Hermes Desktop dashboard API (FastAPI, self-contained).

Deliberately does NOT import revamp_core — this file runs inside Hermes's
dashboard plugin loader. It re-derives data dir / port the same way
revamp_core.config does, reads the token file, and proxies the endpoints
the Desktop pane needs: player state/command plus read/write access to the
library search, radio presets/search/favorites and playlists routes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import urllib.parse
import urllib.error
import urllib.request

import re

from fastapi import APIRouter, Response

try:  # the request object of the generic routes; absent under the test stub
    from fastapi import Request
except ImportError:  # pragma: no cover
    Request = object  # type: ignore[assignment,misc]

router = APIRouter()

DEFAULT_PORT = 8114


def _clamp_port(value) -> int:
    try:
        return max(1, min(65535, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def _data_dir() -> Path:
    # Per request: the profile this request is for (Hermes's resolver follows the profile scope
    # when one gateway serves many profiles); HERMES_HOME is the single-profile fallback.
    raw = os.environ.get("REVAMP_HOME")
    if not raw:
        try:
            from hermes_constants import get_hermes_home
            hermes = str(get_hermes_home())
        except Exception:
            hermes = os.environ.get("HERMES_HOME")
        raw = os.path.join(hermes, "revamp") if hermes else "~/.hermes/revamp"
    return Path(raw).expanduser()


def _port() -> int:
    # env override -> hermes config -> default; never raises
    env = os.environ.get("REVAMP_PORT")
    if env and env.isdigit():
        return _clamp_port(env)
    try:
        from hermes_cli.config import cfg_get, load_config

        p = cfg_get(load_config(), "plugins", "entries", "revamp", "port")
        if p:
            return _clamp_port(p)
    except Exception:
        pass
    return DEFAULT_PORT


def _token() -> str:
    try:
        return (_data_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _request(method: str, path: str, body: dict | None = None,
             timeout: float = 5.0) -> tuple[int, dict]:
    base = f"http://127.0.0.1:{_port()}"
    req = urllib.request.Request(base + path, method=method)
    tok = _token()
    if tok:
        req.add_header("X-Revamp-Token", tok)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw else {})
        except Exception:
            return exc.code, {}
    except Exception:
        return 0, {}


@router.get("/info")
def info() -> dict:
    """Connection info for the Desktop pane: url + liveness. The token is not
    needed here: the player page receives it server-side when it loads."""
    port = _port()
    status, health = _request("GET", "/api/health", timeout=2.0)
    tok = _token()
    return {
        "url": f"http://127.0.0.1:{port}",
        # For the player-page bridge (see web/app.js); derived from the token, not the token.
        "bridge_key": hmac.new(tok.encode("utf-8"), b"revamp-bridge", hashlib.sha256).hexdigest() if tok else "",
        "ok": status == 200 and health.get("app") == "revamp",
        "version": health.get("version"),
    }


def _proxied(status: int, payload: dict) -> Response:
    """503 when the server is down; otherwise the server's own status and body."""
    if status == 0:
        return Response('{"error":"revamp server unavailable"}',
                        media_type="application/json", status_code=503)
    return Response(json.dumps(payload), media_type="application/json", status_code=status)


@router.get("/state")
def state() -> Response:
    return _proxied(*_request("GET", "/api/state"))


@router.put("/state")
def state_update(body: dict | None = None) -> Response:
    """Write a partial player state through to the core server."""
    return _proxied(*_request("PUT", "/api/state", body=body or {}))


@router.post("/command")
def command(body: dict | None = None) -> Response:
    return _proxied(*_request("POST", "/api/command", body=body or {}))


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


@router.get("/library/tracks")
def library_tracks(q: str = "", limit: int = 50, kind: str = "", offset: int = 0) -> Response:
    params: dict = {"q": q, "limit": _clamp(limit, 1, 200), "offset": _clamp(offset, 0, 10_000_000)}
    if kind:
        params["kind"] = kind
    query = urllib.parse.urlencode(params)
    return _proxied(*_request("GET", "/api/library/tracks?" + query))


@router.get("/library/lyrics")
def library_lyrics(id: str = "") -> Response:
    query = ("?" + urllib.parse.urlencode({"id": id})) if id else ""
    # The server waits up to 8 s for LRCLIB; give it room before answering 503.
    return _proxied(*_request("GET", "/api/library/lyrics" + query, timeout=10.0))


@router.get("/library/status")
def library_status() -> Response:
    return _proxied(*_request("GET", "/api/library/status"))


@router.get("/library/chapters")
def library_chapters(id: str = "") -> Response:
    query = urllib.parse.urlencode({"id": id})
    return _proxied(*_request("GET", "/api/library/chapters?" + query))


@router.post("/library/resume")
def library_resume(body: dict | None = None) -> Response:
    return _proxied(*_request("POST", "/api/library/resume", body=body or {}))


@router.get("/library/folders")
def library_folders() -> Response:
    return _proxied(*_request("GET", "/api/library/folders"))


@router.put("/library/folders")
def library_folders_set(body: dict | None = None) -> Response:
    return _proxied(*_request("PUT", "/api/library/folders", body=body or {},
                              timeout=10.0))


@router.get("/fs/dirs")
def fs_dirs(path: str = "") -> Response:
    params: dict = {}
    if path:
        params["path"] = path
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    return _proxied(*_request("GET", "/api/fs/dirs" + qs))


@router.get("/window")
def window_status() -> Response:
    return _proxied(*_request("GET", "/api/window"))


@router.post("/window/open")
def window_open(body: dict | None = None) -> Response:
    return _proxied(*_request("POST", "/api/window/open", body=body or {}, timeout=10.0))


@router.post("/window/close")
def window_close() -> Response:
    return _proxied(*_request("POST", "/api/window/close", body={}, timeout=10.0))


@router.get("/radio/presets")
def radio_presets() -> Response:
    return _proxied(*_request("GET", "/api/radio/presets"))


@router.get("/radio/popular")
def radio_popular() -> Response:
    return _proxied(*_request("GET", "/api/radio/popular", timeout=10.0))


@router.get("/radio/search")
def radio_search(q: str = "", limit: int = 30, tag: str = "", offset: int = 0) -> Response:
    params: dict = {"q": q, "limit": _clamp(limit, 1, 60), "offset": _clamp(offset, 0, 1000)}
    if tag:
        params["tag"] = tag
    query = urllib.parse.urlencode(params)
    return _proxied(*_request("GET", "/api/radio/search?" + query, timeout=10.0))


@router.get("/radio/favorites")
def radio_favorites() -> Response:
    return _proxied(*_request("GET", "/api/radio/favorites"))


@router.post("/radio/favorites")
def radio_favorite_add(body: dict | None = None) -> Response:
    return _proxied(*_request("POST", "/api/radio/favorites", body=body or {}))


@router.delete("/radio/favorites")
def radio_favorite_remove(id: str = "") -> Response:
    query = urllib.parse.urlencode({"id": id})
    return _proxied(*_request("DELETE", "/api/radio/favorites?" + query))


@router.get("/playlists")
def playlists() -> Response:
    return _proxied(*_request("GET", "/api/playlists"))


@router.get("/playlists/{pid}")
def playlist(pid: str) -> Response:
    return _proxied(*_request("GET", "/api/playlists/" + urllib.parse.quote(pid, safe="")))


# ---------------------------------------------------------------------------
# Generic proxy: every /sources/* and /playback/* route the server has (new
# sources included) reaches the Desktop half without a route of its own.
# JSON only; audio is fetched by the player page, which has the token itself.
# ---------------------------------------------------------------------------

_GENERIC = re.compile(r"^(sources|playback)(/[A-Za-z0-9@._~%:+-]+)*/?$")


def _generic(method: str, path: str, query: str = "", body: dict | None = None) -> Response:
    path = path.strip("/")
    if not _GENERIC.match(path) or any(seg in ("..", ".") for seg in path.split("/")) \
            or "%2f" in path.lower() or "%2e" in path.lower():
        return Response('{"error":"not allowed"}', media_type="application/json", status_code=404)
    if path.startswith("sources/") and path.split("/")[2:3] == ["stream"]:
        # audio bytes don't go through here
        return Response('{"error":"streams are not proxied"}', media_type="application/json",
                        status_code=404)
    q = "&".join(p for p in (query or "").split("&") if p and not p.startswith("t="))
    full = "/api/" + path + ("?" + q if q else "")
    return _proxied(*_request(method, full, body=body if method in ("POST", "PUT") else None,
                              timeout=15.0))


def _query(request) -> str:
    try:
        return str(request.url.query or "")
    except AttributeError:
        return ""


@router.get("/sources")
def sources_list() -> Response:
    return _generic("GET", "sources")


@router.get("/sources/{rest:path}")
def sources_get(rest: str, request: Request) -> Response:
    return _generic("GET", "sources/" + rest, _query(request))


@router.post("/sources/{rest:path}")
def sources_post(rest: str, request: Request, body: dict | None = None) -> Response:
    return _generic("POST", "sources/" + rest, _query(request), body or {})


@router.put("/sources/{rest:path}")
def sources_put(rest: str, request: Request, body: dict | None = None) -> Response:
    return _generic("PUT", "sources/" + rest, _query(request), body or {})


@router.delete("/sources/{rest:path}")
def sources_delete(rest: str, request: Request) -> Response:
    return _generic("DELETE", "sources/" + rest, _query(request))


@router.get("/playback")
def playback_state() -> Response:
    return _generic("GET", "playback")


@router.get("/playback/{rest:path}")
def playback_get(rest: str, request: Request) -> Response:
    return _generic("GET", "playback/" + rest, _query(request))


@router.post("/playback/{rest:path}")
def playback_post(rest: str, request: Request, body: dict | None = None) -> Response:
    return _generic("POST", "playback/" + rest, _query(request), body or {})


@router.get("/resume")
def resume_get(key: str = "") -> Response:
    return _proxied(*_request("GET", "/api/resume?" + urllib.parse.urlencode({"key": key})))


@router.post("/resume")
def resume_set(body: dict | None = None) -> Response:
    return _proxied(*_request("POST", "/api/resume", body=body or {}))


def _pl_path(pid: str) -> str:
    return "/api/playlists/" + urllib.parse.quote(pid, safe="")


@router.put("/playlists/{pid}")
def playlist_update(pid: str, body: dict | None = None) -> Response:
    """Rename (``{"name"}``) or replace the items of a saved playlist."""
    return _proxied(*_request("PUT", _pl_path(pid), body=body or {}))


@router.delete("/playlists/{pid}")
def playlist_delete(pid: str) -> Response:
    return _proxied(*_request("DELETE", _pl_path(pid)))


@router.get("/likes")
def likes() -> Response:
    return _proxied(*_request("GET", "/api/likes"))


@router.post("/likes")
def like(body: dict | None = None) -> Response:
    """``{"track_id", "liked": true|false}``"""
    return _proxied(*_request("POST", "/api/likes", body=body or {}))
