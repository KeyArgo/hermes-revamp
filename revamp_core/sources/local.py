"""The local library as a source (``local:track:<tid>``)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import items as items_mod
from .. import library
from . import Source, SourceError


def track_item(t: Dict[str, Any], kind: Optional[str] = None) -> Dict[str, Any]:
    """A library track -> canonical item (kind from its folder, like the views)."""
    kind = kind or library.kind_of(t)
    item = {
        "id": items_mod.local_id(t["id"]), "source": "local", "kind": kind,
        "title": t.get("title"), "artist": t.get("artist"), "album": t.get("album"),
        "duration": t.get("duration"),
        "art": f"/api/art/{t['id']}" if t.get("has_art", True) else None,
        "playback": {"backend": "webamp", "url": t.get("url") or f"/api/media/{t['id']}"},
        "capabilities": items_mod.default_capabilities(kind),
    }
    return items_mod.normalize(item) or item


class LocalSource(Source):
    id = "local"
    name = "Library"
    kinds = ("music", "audiobook")

    def _listing(self, q: str, params: Dict[str, str]) -> Dict[str, Any]:
        def num(k: str, d: int, hi: int) -> int:
            try:
                return max(0, min(hi, int(params.get(k) or d)))
            except ValueError:
                return d
        res = library.search(q=q, artist=params.get("artist") or "",
                             album=params.get("album") or "", kind=params.get("kind") or "",
                             limit=num("limit", 200, 10000), offset=num("offset", 0, 10_000_000),
                             sort=params.get("sort") or "artist")
        out = []
        for t in res["tracks"]:
            it = track_item(t, t.get("kind"))
            if t.get("resume"):
                it["resume"] = t["resume"]
            out.append(it)
        return {"total": res["total"], "items": out}

    def browse(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        if path in ("", "tracks"):
            return self._listing(params.get("q") or "", params)
        if path == "artists":
            return {"items": library.artists(kind=params.get("kind") or "")}
        if path == "albums":
            return {"items": library.albums(artist=params.get("artist"), kind=params.get("kind") or "")}
        raise SourceError(404, "unknown local path")

    def search(self, q: str, params: Dict[str, str]) -> Dict[str, Any]:
        return self._listing(q, params)

    def item(self, media_id: str) -> Optional[Dict[str, Any]]:
        tid = items_mod.local_track_id(media_id.rsplit(":", 1)[-1])
        t = library.get_track(tid) if tid else None
        return track_item(t) if t else None

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        # The library knows the real kind (its folder) and the current tags.
        tid = items_mod.local_track_id(item.get("id"))
        t = library.get_track(tid) if tid else None
        return track_item(t) if t else item

    def stream(self, key: str, request: Any) -> None:
        tid = items_mod.local_track_id(key)
        t = library.get_track(tid) if tid else None
        if not t:
            raise SourceError(404, "unknown track id")
        request.send_file(t["path"])
