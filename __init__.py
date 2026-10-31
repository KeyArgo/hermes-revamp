"""Revamp — Winamp-class music player for Hermes Agent.

Hermes plugin glue: registers the four ``revamp_*`` tools and the observer
hooks that feed the agent ticker / ducking. The HTTP server itself lives in
``revamp_core.server`` and is started lazily (first tool call or hook, plus
once here in a background thread — never blocking ``register``).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

# Import the core eagerly: a daemon thread that imports lazily can run after
# the plugin loader has torn down its package namespace (seen under
# `hermes plugins doctor`), and then nothing is importable any more.
try:
    from .revamp_core import client as _core_client, config as _core_config, server as _core_server
except ImportError:  # loaded as a plain directory, not a package
    from revamp_core import client as _core_client, config as _core_config, server as _core_server

logger = logging.getLogger("revamp")

TOOLSET = "revamp"

_HINT = ("Open Revamp in Hermes Desktop (sidebar → Revamp) "
         "or http://127.0.0.1:{port}")


# ---------------------------------------------------------------------------
# lazy server + client helpers
# ---------------------------------------------------------------------------

def _ensure() -> None:
    ensure_server = _core_server.ensure_server
    ensure_server()


def _boot() -> None:
    # Background start at load time. A host that forbids sockets (Plugin Doctor,
    # sandboxes) or a busy port must not print a traceback; the next tool call
    # or hook retries and reports a real error there.
    try:
        _ensure()
    except Exception as exc:  # noqa: BLE001
        logger.info("revamp: server not started at load (%s); will retry on first use", exc)


def _client():
    RevampClient = _core_client.RevampClient
    RevampUnavailable = _core_client.RevampUnavailable
    _ensure()
    return RevampClient()


def _hint_payload(payload: dict) -> dict:
    """Attach the open-the-player hint when no player page is connected."""
    try:
        live = payload.get("live") or {}
        if payload.get("player_connected") is False or \
                (live and not live.get("player_connected")):
            setting = _core_config.setting
            payload["hint"] = _HINT.format(port=setting("port"))
    except Exception:
        pass
    return payload


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _tool_error(exc: Exception) -> str:
    RevampUnavailable = _core_client.RevampUnavailable
    if isinstance(exc, RevampUnavailable) and "control characters" not in str(exc):
        return _dump({"ok": False, "error": str(exc),
                      "hint": "start the player: any revamp_* call retries, "
                              "or run python -m revamp_core.server"})
    return _dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _tool_player(args: dict, **kw) -> str:
    """revamp_player — transport + volume + shuffle/repeat + status."""
    if not isinstance(args, dict):  # wrong-type arguments get an answer, never a crash
        return _dump({"ok": False, "error": "arguments must be an object"})
    action = str(args.get("action") or "status").lower()
    try:
        c = _client()
        if action == "status":
            return _dump(_hint_payload({
                "ok": True,
                "now_playing": (c.get("/api/state").get("live") or {}).get("now_playing"),
                "state": {k: v for k, v in c.get("/api/state").items()
                          if k in ("volume", "shuffle", "repeat", "current_index")},
            }))
        simple = {"play", "pause", "toggle", "stop", "next", "previous"}
        body: dict = {"action": "prev" if action == "previous" else action}
        if action == "location":
            body["action"] = "player_location"
        if action == "seek":
            body["seconds"] = args.get("seconds")
        elif action == "volume":
            body["percent"] = args.get("percent")
        elif action in ("shuffle", "repeat"):
            body["on"] = bool(args.get("on"))
        elif action == "location":
            loc = str(args.get("location") or "").lower()
            if loc not in ("docked", "floating", "mini"):
                return _dump({"ok": False,
                              "error": "location must be docked, floating or mini"})
            body["location"] = loc
        if action == "location":
            res = c.post("/api/command", body)
            return _dump({"ok": True, **res})
        if action in simple or (
                action == "seek" and body.get("seconds") is not None) or (
                action == "volume" and body.get("percent") is not None) or (
                action in ("shuffle", "repeat")):
            res = c.post("/api/command", body)
            return _dump({"ok": True, **res})
        return _dump({"ok": False, "error": f"unknown player action {action!r}"})
    except Exception as exc:
        return _tool_error(exc)


def _track_item(t: dict) -> dict:
    return {"kind": "track", "title": t.get("title"), "artist": t.get("artist"),
            "url": t.get("url"), "duration": t.get("duration"),
            "track_id": t.get("id"), "station_id": None}


def _tool_library(args: dict, **kw) -> str:
    """revamp_library — search / play / enqueue / rescan / stats."""
    if not isinstance(args, dict):  # wrong-type arguments get an answer, never a crash
        return _dump({"ok": False, "error": "arguments must be an object"})
    action = str(args.get("action") or "search").lower()
    try:
        c = _client()
        if action == "rescan":
            return _dump({"ok": True, **c.post("/api/library/rescan")})
        if action == "stats":
            return _dump({"ok": True, **c.get("/api/library/status")})
        limit = min(50, max(1, int(args.get("limit") or 10)))
        res = c.get("/api/library/tracks", q=args.get("query") or "",
                    artist=args.get("artist") or "", album=args.get("album") or "",
                    limit=min(200, limit * 4))
        tracks = res.get("tracks") or []
        if action == "search":
            return _dump({"ok": True, "total": res.get("total"),
                          "tracks": tracks[:limit]})
        if action in ("play", "enqueue"):
            if not tracks:
                return _dump({"ok": False, "error": "no matching tracks"})
            items = [_track_item(t) for t in tracks[:50]]
            res2 = c.post("/api/command", {
                "action": "load", "items": items,
                "mode": "replace" if action == "play" else "append",
                "play": action == "play"})
            return _dump({"ok": True, "loaded": len(items), **res2})
        return _dump({"ok": False, "error": f"unknown library action {action!r}"})
    except Exception as exc:
        return _tool_error(exc)


def _resolve_playlist(c, name: str) -> str:
    """Map a playlist id or display name (any case) to its URL-safe id, or ''."""
    from urllib.parse import quote
    want = name.strip().lower()
    if not want:
        return ""
    for p in c.get("/api/playlists") or []:
        if want in (str(p.get("id", "")).lower(), str(p.get("name", "")).lower()):
            return quote(str(p["id"]), safe="")
    return ""


def _tool_playlist(args: dict, **kw) -> str:
    """revamp_playlist — list / show / create / add / play / delete."""
    if not isinstance(args, dict):  # wrong-type arguments get an answer, never a crash
        return _dump({"ok": False, "error": "arguments must be an object"})
    action = str(args.get("action") or "list").lower()
    try:
        c = _client()
        if action == "list":
            return _dump({"ok": True, "playlists": c.get("/api/playlists")})
        pid = _resolve_playlist(c, str(args.get("name") or ""))
        if action != "create" and not pid:
            return _dump({"ok": False, "error": f"no playlist named {args.get('name')!r}",
                          "playlists": [p.get("name") for p in c.get("/api/playlists") or []]})
        if action == "show":
            return _dump({"ok": True, **c.get(f"/api/playlists/{pid}")})
        if action == "create":
            return _dump({"ok": True, **c.post("/api/playlists",
                                               {"name": args.get("name"),
                                                "items": args.get("items") or []})})
        if action == "delete":
            return _dump({"ok": True, **c.delete(f"/api/playlists/{pid}")})
        if action == "add":
            res = c.get("/api/library/tracks",
                        q=args.get("query") or "", limit=200)
            items = [_track_item(t) for t in (res.get("tracks") or [])[:50]]
            pl = c.get(f"/api/playlists/{pid}")
            if not pl or "id" not in pl:
                return _dump({"ok": False, "error": "no such playlist"})
            pl["items"] = (pl.get("items") or []) + items
            return _dump({"ok": True, "added": len(items),
                          **c.put(f"/api/playlists/{pl['id']}",
                                  {"items": pl["items"]})})
        if action == "play":
            pl = c.get(f"/api/playlists/{pid}")
            if not pl or "id" not in pl:
                return _dump({"ok": False, "error": "no such playlist"})
            res = c.post("/api/command", {"action": "load",
                                          "items": pl.get("items") or [],
                                          "mode": "replace", "play": True})
            return _dump({"ok": True, **res})
        return _dump({"ok": False, "error": f"unknown playlist action {action!r}"})
    except Exception as exc:
        return _tool_error(exc)


def _station_item(s: dict) -> dict:
    return {"kind": "stream", "title": s.get("name"), "artist": "Radio",
            "url": s.get("url"), "duration": None, "track_id": None,
            "station_id": s.get("id")}


def _own_music_matching(c, q: str) -> dict:
    """Playlists whose name contains *q* and library tracks matching it (empty when none)."""
    out: dict = {}
    try:
        names = [p.get("name") for p in c.get("/api/playlists") or []
                 if q in str(p.get("name", "")).lower()]
        if names:
            out["playlists"] = names[:5]
    except Exception:
        pass
    try:
        res = c.get("/api/library/tracks", q=q, limit=5) or {}
        total = int(res.get("total") or len(res.get("tracks") or []))
        if total:
            out["library_tracks"] = total
    except Exception:
        pass
    return out


def _hermes_rows(c) -> list:
    """Hermes Radio lineup, or [] when that route is absent (older servers)."""
    try:
        rows = c.get("/api/radio/hermes") or []
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def _tool_radio(args: dict, **kw) -> str:
    """revamp_radio — hermes lineup / presets / search / play / favorites / add_favorite."""
    if not isinstance(args, dict):  # wrong-type arguments get an answer, never a crash
        return _dump({"ok": False, "error": "arguments must be an object"})
    action = str(args.get("action") or "presets").lower()
    try:
        c = _client()
        if action == "hermes":
            return _dump({"ok": True, "stations": c.get("/api/radio/hermes"),
                          "coming_soon": "Hermes FM is not on the air yet."})
        if action == "presets":
            return _dump({"ok": True, "stations": c.get("/api/radio/presets")})
        if action == "favorites":
            return _dump({"ok": True, "stations": c.get("/api/radio/favorites")})
        if action == "search":
            res = c.get("/api/radio/search", q=args.get("query") or "",
                        limit=min(50, int(args.get("limit") or 20)))
            return _dump({"ok": True, "stations": res})
        if action == "play":
            sid = args.get("station_id")
            station = None
            if sid:
                for src in ("presets", "favorites"):
                    for s in c.get(f"/api/radio/{src}") or []:
                        if s.get("id") == sid:
                            station = s
                            break
                    if station:
                        break
                if station is None:
                    for s in _hermes_rows(c):
                        if s.get("id") == sid:
                            station = s
                            break
            if station is None and args.get("url"):
                station = {"id": "custom", "name": args.get("name") or "Radio",
                           "url": args.get("url")}
            q = str(args.get("query") or args.get("name") or "").strip().lower()
            if station is None and q and not args.get("radio_ok"):
                # The user's own music comes first: if a playlist or library tracks match,
                # the agent asks which they want instead of guessing (call again with
                # radio_ok=true for the radio, or use revamp_playlist / revamp_library).
                mine = _own_music_matching(c, q)
                if mine:
                    return _dump({"ok": False, "ask_user": True, **mine, "message":
                                  f"The user has their own music matching {q!r}. Ask whether they want "
                                  "that or a radio station; for the radio, call again with radio_ok=true."})
            if station is None and q:
                # Popular first: ask Radio Browser before the local lists, so a query
                # finds the world's most popular match, not whichever preset names it.
                try:
                    hits = c.get("/api/radio/search", q=q, limit=5)  # q: query or name
                except Exception:
                    hits = None
                if isinstance(hits, list) and hits:
                    station = hits[0]
            if station is None and q:  # offline/no hit: saved stations, favorites first
                for src in ("favorites", "presets"):
                    for s in c.get(f"/api/radio/{src}") or []:
                        if q in str(s.get("name", "")).lower() or q == str(s.get("id", "")).lower():
                            station = s
                            break
                    if station:
                        break
                if station is None:
                    for s in _hermes_rows(c):
                        blob = " ".join(str(s.get(k) or "") for k in ("name", "id", "provider")).lower()
                        if q in blob:
                            station = s
                            break
            if station is None:
                return _dump({"ok": False, "error": "no station found"})
            res = c.post("/api/command", {"action": "load", "mode": "replace",
                                          "play": True,
                                          "items": [_station_item(station)]})
            return _dump({"ok": True, "station": station.get("name"), **res})
        if action == "add_favorite":
            return _dump({"ok": True, **c.post("/api/radio/favorites", {
                "id": args.get("station_id") or "",
                "name": args.get("name") or "Station",
                "url": args.get("url") or "",
            })})
        return _dump({"ok": False, "error": f"unknown radio action {action!r}"})
    except Exception as exc:
        return _tool_error(exc)


# ---------------------------------------------------------------------------
# hooks (fire-and-forget agent events; never raise, never block)
# ---------------------------------------------------------------------------

def _post_agent_event(kind: str, fields: dict) -> None:
    def run() -> None:
        try:
            ensure_server = _core_server.ensure_server
            RevampClient = _core_client.RevampClient
            ensure_server()
            c = RevampClient(timeout=0.5)
            body = {"kind": kind, "source": "hermes", **fields}
            c.request("POST", "/api/agent_event", body=body)
        except Exception:
            pass

    threading.Thread(target=run, name="revamp-hook", daemon=True).start()


_SECRET_RE = None  # compiled lazily; kept module-level so hooks share one pattern


def _mask_secrets(text: str) -> str:
    """Mask credential values in a preview before it is persisted/shown.

    Previews end up in the state file and on the ticker; tokens passed on the
    command line or in URLs would sit there in plain text.
    """
    global _SECRET_RE
    if _SECRET_RE is None:
        import re as _re
        _SECRET_RE = _re.compile(
            r"((?:authorization|api[-_]?key|api[-_]?secret|token|secret|password|passwd|pwd"
            r"|bearer|basic)[-_ ]?[=: ]*(?:bearer|basic)?[ \t]*)(?:[^\s'\"]+)", _re.IGNORECASE)
    try:
        return _SECRET_RE.sub(lambda m: f"{m.group(1)}***", text)
    except Exception:
        return text


def _preview_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for k in ("command", "path", "query", "url", "pattern", "prompt", "goal"):
        v = args.get(k)
        if v:
            s = str(v).strip().replace("\n", " ")
            return _mask_secrets(s)[:80]
    return ""


def _hook_pre_tool_call(**kw) -> None:
    _post_agent_event("tool_start", {
        "tool": kw.get("tool_name") or (kw.get("tool") or {}).get("name"),
        "preview": _preview_from_args(kw.get("args")),
        "session_id": kw.get("session_id"),
    })
    return None


def _hook_post_tool_call(**kw) -> None:
    ok = not kw.get("error_message") and kw.get("status") != "error"
    _post_agent_event("tool_end", {
        "tool": kw.get("tool_name"),
        "ok": bool(ok),
        "session_id": kw.get("session_id"),
    })
    return None


def _hook_session_start(**kw) -> None:
    _post_agent_event("session_start", {"session_id": kw.get("session_id")})


def _hook_session_end(**kw) -> None:
    _post_agent_event("session_end", {"session_id": kw.get("session_id")})


def _hook_pre_approval_request(**kw) -> None:
    _post_agent_event("approval_request", {
        "preview": str(kw.get("command") or kw.get("description") or "")[:80],
        "session_id": kw.get("session_id"),
        "session_key": kw.get("session_key"),
        # Hermes reserves cli for a local interactive prompt. Gateway and
        # transport:* include bots, cron, and background sessions.
        "approval_surface": str(kw.get("surface") or ""),
    })


def _hook_post_approval_response(**kw) -> None:
    _post_agent_event("approval_response", {
        "ok": str(kw.get("choice") or "").lower() not in ("deny", "timeout", ""),
        "session_id": kw.get("session_id"),
        "session_key": kw.get("session_key"),
        "approval_surface": str(kw.get("surface") or ""),
    })


HOOKS = (
    ("pre_tool_call", _hook_pre_tool_call),
    ("post_tool_call", _hook_post_tool_call),
    ("on_session_start", _hook_session_start),
    ("on_session_end", _hook_session_end),
    ("pre_approval_request", _hook_pre_approval_request),
    ("post_approval_response", _hook_post_approval_response),
)


# ---------------------------------------------------------------------------
# tool schemas (Hermes format: name/description/parameters JSON schema)
# ---------------------------------------------------------------------------

def _schema(name: str, description: str, props: dict, required: list) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}


PLAYER_ACTIONS = ["status", "play", "pause", "toggle", "stop", "next",
                  "previous", "seek", "volume", "shuffle", "repeat", "location"]

SCHEMA_PLAYER = _schema(
    "revamp_player",
    "Control the Revamp music player (a Winamp clone the user runs locally): "
    "transport, seek, volume, shuffle, repeat, get now-playing status, or "
    "move the player: docked/floating/mini. Use "
    "only when the user asks about music or the player. In Hermes Desktop you "
    "can show a live now-playing card by writing ::nowplaying{} alone on its "
    "own line. For DJ guidance load the skill revamp:dj.",
    {
        "action": {"type": "string", "enum": PLAYER_ACTIONS,
                   "description": "What to do (default status)."},
        "seconds": {"type": "number", "description": "seek target, seconds"},
        "percent": {"type": "number", "description": "volume 0-100"},
        "on": {"type": "boolean", "description": "shuffle/repeat on/off"},
        "location": {"type": "string", "enum": ["docked", "floating", "mini"],
                     "description": "for location: where the player lives"},
    },
    [],
)

SCHEMA_LIBRARY = _schema(
    "revamp_library",
    "Search the Revamp music library, or play/enqueue matches, rescan, stats.",
    {
        "action": {"type": "string", "enum": ["search", "play", "enqueue",
                                              "rescan", "stats"]},
        "query": {"type": "string", "description": "free-text match"},
        "artist": {"type": "string"},
        "album": {"type": "string"},
        "limit": {"type": "integer", "description": "max results (<=50)"},
    },
    [],
)

SCHEMA_PLAYLIST = _schema(
    "revamp_playlist",
    "Manage saved Revamp playlists: list, show, create, add (search results), play, delete.",
    {
        "action": {"type": "string", "enum": ["list", "show", "create", "add",
                                              "play", "delete"]},
        "name": {"type": "string", "description": "playlist name (or id)"},
        "query": {"type": "string", "description": "for add: library search"},
        "items": {"type": "array", "description": "for create: track items"},
    },
    [],
)

SCHEMA_RADIO = _schema(
    "revamp_radio",
    "Internet radio. action=hermes lists Hermes Radio (Chillsynth, Nightride, Darksynth, "
    "Spacesynth, Radio Paradise Main and Mellow, EVE Radio); play one that fits what the "
    "user is doing. Hermes FM is not on the air yet — do not invent a stream for it. "
    "Other actions: presets, search Radio Browser, play a station, manage favorites.",
    {
        "action": {"type": "string", "enum": ["hermes", "presets", "search", "play",
                                              "favorites", "add_favorite"]},
        "query": {"type": "string", "description": "search text / station name"},
        "station_id": {"type": "string"},
        "url": {"type": "string", "description": "stream URL (play/add_favorite)"},
        "name": {"type": "string"},
        "radio_ok": {"type": "boolean", "description": "play: the user chose the radio over their own matching music"},
    },
    [],
)

TOOLS = (
    ("revamp_player", SCHEMA_PLAYER, _tool_player),
    ("revamp_library", SCHEMA_LIBRARY, _tool_library),
    ("revamp_playlist", SCHEMA_PLAYLIST, _tool_playlist),
    ("revamp_radio", SCHEMA_RADIO, _tool_radio),
)


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    # Before anything starts the server: pin this profile's home (see config.pin_hermes_home).
    try:
        _core_config.pin_hermes_home(_core_config.current_hermes_home())
    except Exception:
        pass
    for name, schema, handler in TOOLS:
        kwargs = {"name": name, "toolset": TOOLSET, "schema": schema,
                  "handler": handler}
        try:
            ctx.register_tool(**kwargs)
        except TypeError:
            # Older cores: positional name/schema/handler without toolset.
            ctx.register_tool(name=name, schema=schema, handler=handler)
    for hook_name, fn in HOOKS:
        ctx.register_hook(hook_name, fn)
    # Bundled skills (read-only, loaded on demand as "revamp:<name>").
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir() and hasattr(ctx, "register_skill"):
        for child in sorted(skills_dir.iterdir()):
            if (child / "SKILL.md").is_file():
                ctx.register_skill(child.name, child / "SKILL.md")
    # Warm the server without blocking register.
    threading.Thread(target=_boot, name="revamp-boot", daemon=True).start()
    logger.info("revamp registered (4 tools, %d hooks)", len(HOOKS))


__all__ = ["register"]
