"""The canonical queue item, shared by the server, both players and saved state.

One shape for everything a queue can hold, whatever source it came from::

    {
      "id": "local:track:<tid>",      # stable, source-qualified media id
      "source": "local",               # which source owns it (sources/<id>)
      "kind": "music",                 # music | audiobook | stream | focus | ...
      "title": ..., "artist": ..., "album": ..., "duration": ..., "art": ...,
      "playback": {"backend": "webamp", "url": "/api/media/<tid>"},
      "resumeKey": "local:track:<tid>",   # only for items that remember a place
      "capabilities": ["seek", "resume"],
    }

``kind`` is item metadata. It is set by the source, never guessed from the URL
or the backend. Older saved queues/playlists (``{kind: "track"|"stream", url,
track_id, book}``) are upgraded by :func:`normalize`; ``web/items.js`` is the
same function for the browser, keep the two in step.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional

KINDS = ("music", "audiobook", "stream", "focus")
BACKENDS = ("webamp", "focus", "spotify")
#: Legacy items without a ``book`` flag: longer than this was treated as a book.
LEGACY_BOOK_SECS = 20 * 60
_LOCAL_ID = re.compile(r"^[0-9a-f]{16}$")
_TEXT = ("title", "artist", "album", "art")


def _hash(s: str) -> str:
    """FNV-1a 32 of the UTF-8 bytes: web/items.js computes the same id."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def local_id(track_id: str) -> str:
    return f"local:track:{track_id}"


def local_track_id(item_id: Any) -> Optional[str]:
    """The library track id inside a local item id (or a bare legacy id)."""
    s = str(item_id or "")
    if s.startswith("local:track:"):
        s = s[len("local:track:"):]
    return s if _LOCAL_ID.match(s) else None


def radio_stream_url(url: str) -> str:
    return "/api/radio/stream?url=" + urllib.parse.quote(url, safe="")


def default_capabilities(kind: str) -> List[str]:
    """What an item of *kind* allows when its source says nothing."""
    if kind == "audiobook":
        return ["seek", "resume"]
    if kind == "music":
        return ["seek"]
    return []  # live streams can't seek; generated audio declares its own


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f >= 0 else None  # NaN / negative -> unknown


def _legacy(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``{kind: track|stream, url, track_id, station_id, book}`` -> canonical."""
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        return None
    duration = _num(raw.get("duration"))
    if raw.get("kind") == "stream":
        sid = raw.get("station_id")
        return {
            "id": f"radio:station:{sid}" if sid else f"radio:url:{_hash(url)}",
            "source": "radio", "kind": "stream",
            "title": raw.get("title"), "artist": raw.get("artist"),
            "duration": None,
            "playback": {"backend": "webamp", "url": radio_stream_url(url), "stream": url},
        }
    tid = raw.get("track_id") or (url.rsplit("/", 1)[-1] if url.startswith("/api/media/") else None)
    book = raw.get("book")
    if book is None:  # what the old player did when it didn't know
        kind = "audiobook" if (duration or 0) > LEGACY_BOOK_SECS else "music"
    else:
        kind = "audiobook" if book else "music"
    if tid and local_track_id(tid):
        return {
            "id": local_id(tid), "source": "local", "kind": kind,
            "title": raw.get("title"), "artist": raw.get("artist"), "album": raw.get("album"),
            "duration": duration,
            "art": f"/api/art/{tid}",
            "playback": {"backend": "webamp", "url": url},
        }
    # A plain URL (not in the library): play it as it is.
    return {
        "id": f"url:{_hash(url)}", "source": "url", "kind": kind,
        "title": raw.get("title"), "artist": raw.get("artist"), "album": raw.get("album"),
        "duration": duration,
        "playback": {"backend": "webamp", "url": url},
    }


def is_canonical(raw: Any) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("id"), str) and bool(raw.get("id")) \
        and isinstance(raw.get("playback"), dict)


def normalize(raw: Any) -> Optional[Dict[str, Any]]:
    """Any queue item (canonical or legacy) -> a clean canonical item, or None."""
    if not isinstance(raw, dict):
        return None
    item = dict(raw) if is_canonical(raw) else _legacy(raw)
    if item is None:
        return None
    pb = dict(item.get("playback") or {})
    backend = pb.get("backend") if isinstance(pb.get("backend"), str) and pb.get("backend") else "webamp"
    pb["backend"] = backend
    if backend == "webamp" and not (isinstance(pb.get("url"), str) and pb.get("url")):
        return None  # a URL backend item with nothing to fetch
    item["playback"] = pb
    iid = str(item["id"])
    item["id"] = iid
    src = item.get("source")
    item["source"] = src if isinstance(src, str) and src else (iid.split(":", 1)[0] if ":" in iid else "url")
    kind = item.get("kind")
    item["kind"] = kind if isinstance(kind, str) and kind and kind not in ("track",) else "music"
    for k in _TEXT:
        v = item.get(k)
        item[k] = v if isinstance(v, str) and v else None
    item["duration"] = _num(item.get("duration"))
    caps = item.get("capabilities")
    if not isinstance(caps, list):
        caps = default_capabilities(item["kind"])
    item["capabilities"] = [c for c in caps if isinstance(c, str)]
    if "resume" in item["capabilities"]:
        rk = item.get("resumeKey")
        item["resumeKey"] = rk if isinstance(rk, str) and rk else iid
    else:
        item.pop("resumeKey", None)
    # Legacy-only keys (and the listing-only "resume" hint) never survive.
    for k in ("url", "track_id", "station_id", "book", "resume"):
        item.pop(k, None)
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


def legacy_kind(item: Dict[str, Any]) -> str:
    """The old ``track|stream`` kind, still reported in heartbeats for older UIs."""
    return "stream" if item.get("kind") in ("stream", "focus") else "track"


def legacy_url(item: Dict[str, Any]) -> Optional[str]:
    """What old UIs compare against: a station's own URL, else the playback URL."""
    pb = item.get("playback") or {}
    return pb.get("stream") or pb.get("url")


def migrate_resume(resume: Any) -> Dict[str, Any]:
    """Old resume maps were keyed by bare library track id; key them by item id."""
    if not isinstance(resume, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in resume.items():
        key = local_id(k) if isinstance(k, str) and _LOCAL_ID.match(k) else k
        if key in out and k != key:
            continue  # a value already saved under the new key is newer
        out[key] = v
    return out
