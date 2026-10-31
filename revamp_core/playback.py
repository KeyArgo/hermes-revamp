"""Playback controller: routes /api/command to the backend that is playing.

Backends execute queue items (``item.playback.backend``):

- ``webamp``  — the browser player; commands go out over SSE (as before).
- ``focus``   — a generated-audio engine in the same page; also SSE.
- ``spotify`` — a remote executor; the server calls it directly.

The active backend is the one the latest fresh report names
(``state.live.playback``); with no player connected it is ``webamp``. A command
the active backend/item can't do (seek on a radio stream, next on focus) is
answered ``{"ok": false, "unsupported": true}`` instead of being dropped, and
the capabilities are exposed so UIs can grey their buttons out.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import store

#: Every action /api/command accepts.
KNOWN_ACTIONS = (
    "play pause toggle stop next prev seek volume shuffle repeat load "
    "play_index eq skin toast "
    # seek_by: relative seek (±seconds); sleep: timer (minutes, or end_of_track);
    # queue_*: edit the current queue by position
    "seek_by sleep queue_remove queue_move queue_clear "
    # rate: playback speed (books)
    "rate focus_start focus_stop focus_toggle focus_set "
    # visualizer: show/hide the MilkDrop window; player_location: where the desktop player lives
    # settings: change saved player settings (duck_on_approval, duck_level, duck_toast) from Hermes
    "visualizer player_location settings".split()
)
#: App-level actions: never gated by the backend (the page handles them).
APP_ACTIONS = {"load", "skin", "toast", "settings", "sleep", "queue_remove", "queue_move", "queue_clear", "rate",
               "focus_start", "focus_stop", "focus_toggle", "focus_set", "visualizer", "player_location"}
#: Actions allowed exactly when another capability is (a relative seek is a seek).
NEEDS = {"seek_by": "seek"}
#: Capabilities an item can withhold even when its backend has them.
ITEM_GATED = {"seek"}


class Backend:
    """A playback executor. ``remote=False``: the browser runs it (SSE)."""

    remote = False

    def __init__(self, backend_id: str, capabilities: List[str]) -> None:
        self.id = backend_id
        self.capabilities = list(capabilities)

    def command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Remote backends only: execute *cmd*; return extra reply fields."""
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "remote": self.remote, "capabilities": list(self.capabilities)}


WEBAMP_CAPS = ["play", "pause", "toggle", "stop", "next", "prev", "seek", "volume",
               "shuffle", "repeat", "play_index", "eq"]

_LOCK = threading.RLock()
_BACKENDS: Dict[str, Backend] = {"webamp": Backend("webamp", WEBAMP_CAPS)}


def register_backend(backend: Backend) -> Backend:
    with _LOCK:
        _BACKENDS[backend.id] = backend
    return backend


def get_backend(backend_id: str) -> Optional[Backend]:
    with _LOCK:
        return _BACKENDS.get(backend_id)


def backends() -> List[Dict[str, Any]]:
    with _LOCK:
        return [b.describe() for b in _BACKENDS.values()]


def effective_capabilities(backend_caps: List[str], item_caps: Optional[List[str]]) -> List[str]:
    """What can be done now: the backend's abilities minus what the item withholds."""
    if item_caps is None:
        return list(backend_caps)
    return [c for c in backend_caps if c not in ITEM_GATED or c in item_caps]


def current() -> Dict[str, Any]:
    """``state.playback`` plus the active backend and its capabilities now."""
    live = store.get_state().get("live") or {}
    pb = live.get("playback") if live.get("player_connected") else None
    backend_id = (pb or {}).get("backend") or "webamp"
    backend = get_backend(backend_id) or _BACKENDS["webamp"]
    caps = (pb or {}).get("capabilities")
    if caps is None:  # an older page: assume what the backend can do
        caps = list(backend.capabilities)
    return {"active": backend.id, "playback": pb, "capabilities": caps,
            "backends": backends()}


def dispatch(body: Dict[str, Any], broadcast: Callable[[str, Dict[str, Any]], int]
             ) -> Tuple[int, Dict[str, Any]]:
    """(http status, reply) for one /api/command body."""
    from . import sources  # late: sources import the library
    action = str(body.get("action") or "").strip()
    if action not in KNOWN_ACTIONS:
        return 400, {"error": f"unknown action {action!r}"}
    cmd = dict(body)
    cmd["ts"] = time.time()
    now = current()
    target = now["active"]
    # Focus controls name their backend explicitly: they must work before it
    # has emitted its first heartbeat as well as while it is active.
    if action.startswith("focus_"):
        target = "focus"
    if action == "load":
        cmd["items"] = sources.normalize_list(cmd.get("items") or [])
        if cmd["items"]:
            target = cmd["items"][0]["playback"]["backend"]
        if get_backend(target) is None:
            return 200, {"ok": False, "unsupported": True, "accepted": False,
                         "error": f"no {target!r} player", "backend": target, "delivered": 0}
    elif action not in APP_ACTIONS and NEEDS.get(action, action) not in now["capabilities"]:
        return 200, {"ok": False, "unsupported": True, "accepted": False, "action": action,
                     "backend": target, "capabilities": now["capabilities"],
                     "error": f"{action} isn't possible on what's playing", "delivered": 0}
    cmd["backend"] = target
    backend = get_backend(target)
    # Page-level actions (settings, sleep, queue edits, skin...) belong to the page even
    # while a remote service (Spotify) is what's playing; only playback goes remote.
    if backend is not None and backend.remote and (action == "load" or action not in APP_ACTIONS):
        try:
            extra = backend.command(cmd) or {}
        except Exception as exc:  # the remote service failed: say so
            return 200, {"ok": False, "accepted": False, "backend": target,
                         "error": str(exc), "delivered": 0}
        return 200, {"ok": True, "accepted": True, "applied": bool(extra.pop("applied", True)),
                     "backend": target, "delivered": 1, **extra}
    delivered = broadcast("command", cmd)
    # accepted: sent to the page; applied is confirmed by its next report.
    return 200, {"ok": True, "accepted": delivered > 0, "applied": False,
                 "backend": target, "delivered": delivered}
