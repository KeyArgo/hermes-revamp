"""Source registry: where queue items come from.

A *source* owns a catalogue (browse/search), turns its own media ids into
canonical items (see ``revamp_core/items.py``), can serve audio bytes under
``/api/sources/<id>/stream/<key>``, and may own playback progress (a Plex
server remembers where you stopped; the local library keeps it in
``state.json``). New sources subclass :class:`Source` and call
:func:`register`; the server's generic ``/api/sources/*`` routes and the
Desktop proxy reach them without new routes. See ``docs/SOURCES.md``.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional

from .. import items as items_mod


class SourceError(Exception):
    """A request a source can't serve; ``status`` becomes the HTTP code."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Source:
    """Base class. Override what the source supports; the rest answers 404.

    ``id`` is the *instance* id and prefixes every item id it makes
    (``local:track:<tid>``, ``plex@<machineId>:track/5531``): two Plex servers
    are two instances of one ``type``."""

    type: str = ""                 # plex, jellyfin, local... (defaults to the id before '@')
    id: str = ""                   # instance id: [a-z0-9_-]+ optionally @<instance>
    name: str = ""
    kinds: tuple = ()              # item kinds this source yields
    features: tuple = ()           # extra features for UIs: playlists_read, chapters, lyrics...
    owns_progress: bool = False    # True: progress is also pushed to/read from the source

    # -- catalogue ------------------------------------------------------------

    def browse(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        """``{"items": [canonical...], "total": n}`` (plus any extra keys)."""
        raise SourceError(404, f"{self.id} can't browse")

    def search(self, q: str, params: Dict[str, str]) -> Dict[str, Any]:
        raise SourceError(404, f"{self.id} can't search")

    def item(self, media_id: str) -> Optional[Dict[str, Any]]:
        """The canonical item for ``<source>:<media_id>``, or None."""
        return None

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Refresh a saved item from the catalogue (title, kind...). Default: as is."""
        return item

    def resolve(self, item_id: str) -> Optional[Dict[str, Any]]:
        """The ``playback`` for an item saved without one (a bare ref). The
        default points Webamp at this source's own stream route, so upstream
        URLs and tokens never reach the browser."""
        if not self.has("stream"):
            return None
        key = item_id.split(":", 1)[1] if ":" in item_id else item_id
        return {"backend": "webamp", "url": stream_url(self.id, key)}

    # -- audio + progress -------------------------------------------------------

    def stream(self, key: str, request: Any) -> None:
        """Answer ``GET /api/sources/<id>/stream/<key>`` on *request* (the HTTP
        handler: ``request.send_file(path)``, ``request.pipe(upstream)``,
        ``request.headers`` for Range)."""
        raise SourceError(404, f"{self.id} has no streams")

    def get_progress(self, key: str) -> Any:
        """Only with ``owns_progress``: seconds, or ``(seconds, updated_at)``, or None."""
        raise SourceError(404, f"{self.id} keeps no progress")

    def set_progress(self, key: str, position: Optional[int]) -> None:
        """Only with ``owns_progress``: push a place upstream. It is already saved
        locally; debounce and retry here, never block (raise = try later)."""
        raise SourceError(404, f"{self.id} keeps no progress")

    # -- sign-in (Plex PIN, Jellyfin Quick Connect, Spotify PKCE) -----------------

    def login_start(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """``{"code", "url", "poll_after"}`` for a source sign-in flow."""
        raise SourceError(404, f"{self.id} has no sign-in")

    def login_poll(self) -> Dict[str, Any]:
        """``{"state": "pending"|"done"|"expired", ...}``."""
        raise SourceError(404, f"{self.id} has no sign-in")

    # -- anything else (settings, sign-in, ...) ---------------------------------

    def route(self, method: str, path: str, params: Dict[str, str],
              body: Dict[str, Any]) -> Any:
        """Other ``/api/sources/<id>/<path>`` calls; return JSON-able data."""
        raise SourceError(404, "unknown route")

    def has(self, name: str) -> bool:
        return getattr(type(self), name) is not getattr(Source, name)

    def describe(self) -> Dict[str, Any]:
        feats = [f for f in ("browse", "search", "stream") if self.has(f)]
        if self.has("login_start"):
            feats.append("login")
        if self.owns_progress:
            feats.append("progress_sync")
        feats += [f for f in self.features if f not in feats]
        return {
            "id": self.id, "type": self.type or self.id.split("@", 1)[0],
            "name": self.name or self.id, "kinds": list(self.kinds), "features": feats,
        }


def stream_url(instance: str, key: str) -> str:
    """The Revamp URL a webamp item plays from (the token is added by the page)."""
    import urllib.parse
    return f"/api/sources/{instance}/stream/" + urllib.parse.quote(key, safe="")


_LOCK = threading.RLock()
_SOURCES: Dict[str, Source] = {}


_INSTANCE = re.compile(r"^[a-z0-9_-]+(@[A-Za-z0-9._-]+)?$")


def register(source: Source) -> Source:
    """Add (or replace) a source instance. Its id prefixes the item ids it makes."""
    if not _INSTANCE.match(source.id or ""):
        raise ValueError(f"bad source id {source.id!r}")
    with _LOCK:
        _SOURCES[source.id] = source
    return source


def unregister(source_id: str) -> None:
    with _LOCK:
        _SOURCES.pop(source_id, None)


def get(source_id: str) -> Optional[Source]:
    _ensure_builtin()
    with _LOCK:
        return _SOURCES.get(source_id)


def all_sources() -> List[Source]:
    _ensure_builtin()
    with _LOCK:
        return list(_SOURCES.values())


def for_key(key: str) -> Optional[Source]:
    """The source owning an item id / resume key (``<source>:...``)."""
    return get(str(key).split(":", 1)[0]) if ":" in str(key) else None


# -- normalising with the catalogue ------------------------------------------------

def normalize(raw: Any) -> Optional[Dict[str, Any]]:
    """Canonical item, refreshed by its source when that source is registered.

    A bare ref (``{"id": "plex@x:track/1", ...}`` without ``playback``) is
    resolved by its source here, at load time, not when it was saved."""
    if isinstance(raw, dict) and isinstance(raw.get("id"), str) and ":" in raw["id"] \
            and not isinstance(raw.get("playback"), dict) and "url" not in raw:
        src = for_key(raw["id"])
        try:
            pb = src.resolve(raw["id"]) if src is not None else None
        except Exception:
            pb = None
        if not pb:
            return None
        raw = dict(raw, playback=pb, source=raw.get("source") or src.id)
    item = items_mod.normalize(raw)
    if item is None:
        return None
    src = get(item["source"])
    if src is not None:
        try:
            item = items_mod.normalize(src.enrich(item)) or item
        except Exception:  # a source's catalogue being down must not drop the item
            pass
    return item


def normalize_list(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        n = normalize(it)
        if n is not None:
            out.append(n)
    return out


# -- progress (resume) ----------------------------------------------------------------

def get_progress(key: str) -> Optional[int]:
    """The saved place: local first; a source that owns progress wins when newer."""
    from .. import store
    pos, at = store.get_resume_entry(key)
    src = for_key(key)
    if src is not None and src.owns_progress:
        try:
            remote = src.get_progress(key)
        except Exception:  # offline: the local place still plays
            remote = None
        if isinstance(remote, (tuple, list)) and len(remote) >= 2:
            if remote[0] is not None and float(remote[1] or 0) >= at:
                pos = int(remote[0]) or None
        elif remote is not None and not at:
            pos = int(remote) or None
    return pos


def set_progress(key: str, position: Optional[int]) -> None:
    """Save (None/0 clears) a place locally at once, then offer it to its source."""
    from .. import store
    src = for_key(key)
    if src is None:
        raise SourceError(404, "no source for that key")
    store.set_resume(key, position)
    if src.owns_progress:
        try:
            src.set_progress(key, position)
        except Exception:  # never block playback; the source retries itself
            pass


_BUILTIN_DONE = False


def _ensure_builtin() -> None:
    # Lazy: local imports library, which imports store; register on first use.
    global _BUILTIN_DONE
    if _BUILTIN_DONE:
        return
    with _LOCK:
        if _BUILTIN_DONE:
            return
        _BUILTIN_DONE = True
        from .local import LocalSource
        from .radio import RadioSource, UrlSource
        for s in (LocalSource(), RadioSource(), UrlSource()):
            _SOURCES.setdefault(s.id, s)
