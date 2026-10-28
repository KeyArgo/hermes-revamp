"""Thread-safe persistent state for Revamp.

Holds everything the web UI remembers between sessions (volume, EQ, queue,
skin, resume positions, settings, agent-event ring buffer, live player
status) in ``state.json`` with atomic writes. ``live`` is deliberately
volatile and never persisted.
"""

from __future__ import annotations

from . import redact
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import items as items_mod
from .config import data_dir

AGENT_RING_SIZE = 200
HEARTBEAT_WINDOW = 6.0  # seconds a heartbeat stays "live"

_LOCK = threading.RLock()
_STATE: Optional[Dict[str, Any]] = None


class StoreWriteError(OSError):
    """State could not be safely persisted."""

#: Top-level keys a client may PUT; everything else is ours.
MERGEABLE_KEYS = (
    "volume", "balance", "eq", "skin", "playlist", "current_index",
    "shuffle", "repeat", "layout", "resume", "settings",
    "handoff",  # where playback was when it moved between Hermes and the desktop window
    "resume_at",
    "focus",  # Focus drone settings (web/focus/adapter.js)
)


#: Keys only the server changes (a client PUT of the whole state must not undo a like).
INTERNAL_KEYS = ("likes",)


def _defaults() -> Dict[str, Any]:
    return {
        "volume": 80,
        "balance": 0,
        "eq": {"on": False, "preamp": 0.0, "bands": [0.0] * 10},
        "skin": "classic",
        "playlist": [],
        "current_index": -1,
        "shuffle": False,
        "repeat": False,
        "layout": None,
        "resume": {},       # resumeKey (item id, e.g. local:track:<tid>) -> seconds
        "resume_at": {},    # resumeKey -> when it was saved (newest place wins)
        "likes": [],        # liked local track ids, oldest first (only the /api/likes routes change it)
        "settings": {"duck_on_approval": False, "duck_chime": True, "duck_dip_seconds": 2,
                     "duck_approval_scope": "app", "duck_level": 0.25, "ticker": True},
        "live": {
            "player_connected": False,
            "now_playing": {
                "title": None, "artist": None, "kind": None, "url": None,
                "position": None, "duration": None, "status": "stopped",
            },
            "playback": None,
        },
    }


def _path() -> Path:
    return data_dir() / "state.json"


def _load() -> Dict[str, Any]:
    base = _defaults()
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k == "live":
                    continue  # live is volatile; rebuilt from heartbeats
                base[k] = v
    except (OSError, ValueError):
        pass
    # Ensure nested defaults survive partial files.
    base["resume"] = items_mod.migrate_resume(base.get("resume"))
    base["resume_at"] = items_mod.migrate_resume(base.get("resume_at"))
    # Queues saved before canonical items existed are upgraded as they load.
    base["playlist"] = items_mod.normalize_list(base.get("playlist"))
    saved_settings = base.get("settings")
    # Merge newly introduced controls without replacing a user's existing choices.
    base["settings"] = {**_defaults()["settings"], **(saved_settings if isinstance(saved_settings, dict) else {})}
    eq = base.setdefault("eq", _defaults()["eq"])
    if not isinstance(eq.get("bands"), list) or len(eq["bands"]) != 10:
        eq["bands"] = [0.0] * 10
    return base


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_state() -> Dict[str, Any]:
    """The full state, merged with live info."""
    with _LOCK:
        st = _load() if _STATE is None else dict(_STATE)
        live = st.get("live") or {}
        with_live = dict(st)
        with_live["live"] = _live_view(live)
        return with_live


def _live_view(live: Dict[str, Any]) -> Dict[str, Any]:
    np = dict(live.get("now_playing") or {})
    connected = bool(live.get("player_connected"))
    ts = float(live.get("last_heartbeat") or 0)
    if ts and time.time() - ts > HEARTBEAT_WINDOW:
        connected = False
    pb = live.get("playback")
    return {"player_connected": connected, "now_playing": np,
            "playback": dict(pb, connected=connected) if isinstance(pb, dict) else None}


def update(partial: Dict[str, Any], persist: bool = True,
           _internal: bool = False) -> Dict[str, Any]:
    """Merge *partial* (top-level keys only; ``live`` ignored).

    ``settings`` is a patchable dictionary: supplied keys update the saved
    dictionary and ``None`` removes one key, so independent controls do not
    erase each other's preferences.
    """
    global _STATE
    with _LOCK:
        st = _load() if _STATE is None else dict(_STATE)
        for k, v in (partial or {}).items():
            if k in MERGEABLE_KEYS or (_internal and k in INTERNAL_KEYS):
                if k == "resume":
                    v = items_mod.migrate_resume(v)
                elif k == "playlist":
                    v = items_mod.normalize_list(v)
                elif k == "settings" and isinstance(v, dict):
                    old_settings = st.get("settings")
                    settings = dict(old_settings) if isinstance(old_settings, dict) else {}
                    for setting, value in v.items():
                        if value is None:
                            settings.pop(setting, None)
                        else:
                            settings[setting] = value
                    v = settings
                st[k] = v
        if persist:
            out = {k: v for k, v in st.items() if k != "live"}
            try:
                _atomic_write(_path(), out)
            except OSError as exc:
                raise StoreWriteError(f"could not save state: {exc}") from exc
        _STATE = st
        return get_state()


def get_resume_entry(key: str) -> tuple:
    """``(seconds or None, saved_at)`` for one resumeKey (legacy bare ids too)."""
    st = get_state()
    resume, at = st.get("resume") or {}, st.get("resume_at") or {}
    tid = items_mod.local_track_id(key)
    keys = [key] + ([items_mod.local_id(tid), tid] if tid else [])
    for k in keys:
        if k in resume:
            try:
                pos = int(float(resume[k]))
            except (TypeError, ValueError):
                pos = 0
            try:
                when = float(at.get(k) or 0)
            except (TypeError, ValueError):
                when = 0.0
            return (pos or None, when)
    return (None, 0.0)


def get_resume(key: str) -> Optional[int]:
    return get_resume_entry(key)[0]


def set_resume(key: str, position: Optional[int]) -> None:
    """Save (or, with None, clear) one place without losing the others'."""
    with _LOCK:  # re-entrant: update() takes it too
        st = get_state()
        resume = dict(st.get("resume") or {})
        at = dict(st.get("resume_at") or {})
        if position:
            resume[key] = int(position)
            at[key] = time.time()
        else:
            resume.pop(key, None)
            at.pop(key, None)
        update({"resume": resume, "resume_at": at})


#: Fields a player may report about what it is playing (see playback.py).
PLAYBACK_FIELDS = ("item_id", "source", "backend", "media_kind", "resume_key",
                   "capabilities", "title", "artist", "position", "duration", "status")


def _playback_from(np: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The normalized ``state.playback`` from a heartbeat's now_playing.

    Older pages send only the legacy fields; their backend is Webamp."""
    if not np:
        return None
    pb = {k: np.get(k) for k in PLAYBACK_FIELDS}
    pb["backend"] = pb["backend"] or "webamp"
    pb["kind"] = pb.pop("media_kind") or (
        "stream" if np.get("kind") == "stream" else ("music" if np.get("kind") else None))
    pb["status"] = str(pb["status"] or "stopped").lower()
    caps = pb["capabilities"]
    pb["capabilities"] = [c for c in caps if isinstance(c, str)] if isinstance(caps, list) else None
    pb["updated"] = time.time()
    return pb


def likes() -> List[str]:
    with _LOCK:
        return [x for x in (get_state().get("likes") or []) if isinstance(x, str)]


def set_liked(track_id: str, liked: bool) -> List[str]:
    """Like (append) or unlike one track id; returns the new list."""
    with _LOCK:
        cur = likes()
        if liked and track_id not in cur:
            cur.append(track_id)
        elif not liked:
            cur = [x for x in cur if x != track_id]
        update({"likes": cur}, _internal=True)
        return cur


def set_likes_order(ids: List[str], shown: Optional[List[str]] = None) -> List[str]:
    """The Liked editor's save: reorder hearts, and remove exactly the ones it took out.

    *shown* is what the editor displayed. Only ids in *shown* but missing from *ids* are removed;
    every other like is kept (appended after the reordered ones). The editor only lists liked
    songs the library knows right now, so during a rescan it shows a partial list: treating
    "not sent" as "removed" once wiped every hidden like. Without *shown* (an older page), nothing
    is removed. Never adds a heart."""
    with _LOCK:
        cur = likes()
        allowed = set(cur)
        removed = set(x for x in (shown or []) if isinstance(x, str)) - set(x for x in ids if isinstance(x, str))
        seen: set = set()
        new = []
        for x in ids:
            if isinstance(x, str) and x in allowed and x not in seen:
                seen.add(x)
                new.append(x)
        new += [x for x in cur if x not in seen and x not in removed]
        update({"likes": new}, _internal=True)
        return new


def reload() -> None:
    """Forget the cached state: the next read loads state.json from disk.

    Backup restore/import rewrites state.json under the server's feet; the
    cached copy in ``_STATE`` would keep serving stale values (and a later
    write would put them back on disk)."""
    global _STATE
    with _LOCK:
        _STATE = None


def run_locked(fn):
    """Hold the state lock while ``fn()`` runs, then drop the cache.

    Restore/import rewrite state.json; the lock keeps a concurrent state
    write from landing the stale cached copy back on disk in between
    (the cache reset makes the next read reload the restored files)."""
    with _LOCK:
        result = fn()
        global _STATE
        _STATE = None
        return result


def heartbeat(now_playing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Record a player heartbeat (live now_playing + connected flag)."""
    global _STATE
    with _LOCK:
        st = _load() if _STATE is None else dict(_STATE)
        live = dict(st.get("live") or {})
        live["player_connected"] = True
        live["last_heartbeat"] = time.time()
        live["now_playing"] = dict(now_playing or {})
        live["playback"] = _playback_from(live["now_playing"])
        st["live"] = live
        _STATE = st
        return get_state()


def report_playback(playback: Dict[str, Any]) -> None:
    """A server-side executor (a remote backend) reports what it plays."""
    global _STATE
    with _LOCK:
        st = _load() if _STATE is None else dict(_STATE)
        live = dict(st.get("live") or {})
        live["player_connected"] = True
        live["last_heartbeat"] = time.time()
        live["playback"] = dict(playback, updated=time.time())
        st["live"] = live
        _STATE = st


# ---------------------------------------------------------------------------
# Agent events (ring buffer, in-memory + persisted best effort)
# ---------------------------------------------------------------------------

def agent_events() -> List[Dict[str, Any]]:
    global _STATE
    with _LOCK:
        st = _load() if _STATE is None else _STATE
        return list(st.get("_agent_events") or [])


def add_agent_event(event: Dict[str, Any]) -> Dict[str, Any]:
    global _STATE
    with _LOCK:
        st = _load() if _STATE is None else dict(_STATE)
        ring = list(st.get("_agent_events") or [])
        event = redact.mask_event(dict(event))  # credentials never reach the state file or ticker
        # An id lets listeners pair a response with its request (ducking unducks
        # for the approval that was actually answered, not any).
        if "id" not in event:
            event["id"] = f"ev{time.time_ns()}"
        ring.append(event)
        if len(ring) > AGENT_RING_SIZE:
            ring = ring[-AGENT_RING_SIZE:]
        st["_agent_events"] = ring
        _STATE = st
        try:
            _atomic_write(_path(), {k: v for k, v in st.items() if k != "live"})
        except OSError:
            pass
    return event


def public_state_snapshot() -> Dict[str, Any]:
    """State without internal keys (for /api/state persistence parity)."""
    st = get_state()
    return {k: v for k, v in st.items() if not k.startswith("_") and k != "live"}
