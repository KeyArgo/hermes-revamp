"""Internet radio as a source (``radio:station:<id>``), plus bare URLs (``url:<hash>``)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import items as items_mod
from .. import radio
from . import Source, SourceError


def station_item(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A preset/favorite/Radio Browser station -> canonical item."""
    url = str(st.get("url") or "")
    if not url:
        return None
    item = items_mod.normalize({
        "kind": "stream", "url": url, "station_id": st.get("id"),
        "title": st.get("name"),
        "artist": ", ".join((st.get("tags") or [])[:2]) or st.get("country") or "",
    })
    if item is not None and st.get("favicon"):
        item["art"] = st["favicon"]
    return item


class RadioSource(Source):
    id = "radio"
    name = "Radio"
    kinds = ("stream",)
    features = ("favorites",)

    def browse(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        if path in ("", "presets"):
            stations = radio.presets()
        elif path == "favorites":
            stations = radio.favorites()
        else:
            raise SourceError(404, "unknown radio path")
        out = [i for i in map(station_item, stations) if i]
        return {"total": len(out), "items": out}

    def search(self, q: str, params: Dict[str, str]) -> Dict[str, Any]:
        try:
            limit = max(1, min(100, int(params.get("limit") or 30)))
        except ValueError:
            limit = 30
        try:
            stations = radio.search_radiobrowser(q=q, tag=params.get("tag") or "", limit=limit)
        except radio.RadioError as exc:
            raise SourceError(502, str(exc)) from exc
        out = [i for i in map(station_item, stations) if i]
        return {"total": len(out), "items": out}

    def item(self, media_id: str) -> Optional[Dict[str, Any]]:
        sid = media_id.split(":", 1)[1] if media_id.startswith("station:") else None
        for st in radio.presets() + radio.favorites():
            if sid and st.get("id") == sid:
                return station_item(st)
        return None


class UrlSource(Source):
    """Anything queued by URL alone (Add URL, an M3U line we don't know)."""
    id = "url"
    name = "URL"
    kinds = ("music",)
