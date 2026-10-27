"""Saved playlists for Revamp: ``<data>/playlists/<slug>.m3u8``.

Format (per SPEC)::

    #EXTM3U
    #EXTINF:<sec>,<Artist> - <Title>
    <abs path or url>

Track items are written as absolute file paths and mapped back to
``/api/media/<id>`` on read when the file is indexed. PLS import/export
supported alongside M3U8.

Revamp's own files also keep each canonical queue item (source, id, kind,
playback...) on a ``#EXTREVAMP:<json>`` line before its location, so items
from any source round-trip; other players ignore that comment, and exports
leave it out.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import items as items_mod
from .config import data_dir

_LOCK = threading.RLock()


def playlists_dir() -> Path:
    d = data_dir() / "playlists"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "playlist"


# ---------------------------------------------------------------------------
# Item <-> m3u line conversion
# ---------------------------------------------------------------------------

def item_path(item: Dict[str, Any]) -> str:
    """The location an Item refers to (for m3u lines): a library file's path,
    a station's own URL, else whatever the player fetches."""
    canon = items_mod.normalize(item) or {}
    tid = items_mod.local_track_id(canon.get("id")) if canon.get("source") == "local" else None
    if tid:
        from .library import get_track
        t = get_track(tid)
        if t:
            return t["path"]
    saved_path = canon.get("path")
    if isinstance(saved_path, str) and os.path.isabs(saved_path):
        return saved_path
    return str(items_mod.legacy_url(canon) or item.get("url") or "")


def _normalize(raw: Any) -> Optional[Dict[str, Any]]:
    from . import sources  # late: sources import the library
    return sources.normalize(raw)


def item_from_line(line: str, origin_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Map an m3u line back to an Item (track id resolved when indexed)."""
    raw = line.strip()
    if not raw:
        return None
    from .library import get_track, track_id_for
    url = raw
    track_id = None
    # A Windows path ("C:\\Music\\a.mp3") parses with scheme "c": a one-letter scheme is a drive.
    if (len(urlparse(raw).scheme) <= 1 or raw.startswith("file:")) and not raw.startswith("/api/"):
        if raw.startswith("file:"):
            from urllib.request import url2pathname
            raw = url2pathname(urlparse(raw).path)
        if origin_dir is not None and not os.path.isabs(raw):
            raw = str(origin_dir / raw)
        tid = track_id_for(raw)
        t = get_track(tid)
        if t:
            track_id = tid
            url = t["url"]
    elif raw.startswith("/api/media/"):
        tid = raw.rsplit("/", 1)[-1]
        if get_track(tid):
            track_id = tid
    item = _normalize({
        "kind": "track" if track_id else "stream",
        "title": None, "artist": None, "url": url, "duration": None,
        "track_id": track_id, "station_id": None,
    })
    if item is not None and os.path.isabs(raw):
        item["path"] = raw
    return item


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _meta_from_items(items: List[Dict[str, Any]]) -> tuple[int, float]:
    count = len(items)
    duration = 0.0
    for it in items:
        try:
            d = float(it.get("duration") or 0)
            duration += d
        except (TypeError, ValueError):
            pass
    return count, duration


def _read_m3u(path: Path, origin_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pending_meta: Optional[Dict[str, Any]] = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return items
    canon: Optional[Dict[str, Any]] = None
    for line in lines:
        s = line.strip()
        if s.startswith("#EXTREVAMP:"):
            try:
                canon = _normalize(json.loads(s[len("#EXTREVAMP:"):]))
            except ValueError:
                canon = None
            continue
        if s.startswith("#EXTINF:"):
            meta: Dict[str, Any] = {}
            body = s[len("#EXTINF:"):]
            secs, _, label = body.partition(",")
            try:
                meta["duration"] = float(secs) if secs else None
            except ValueError:
                meta["duration"] = None
            if " - " in label:
                artist, title = label.split(" - ", 1)
                meta["artist"], meta["title"] = artist.strip(), title.strip()
            elif label:
                meta["title"] = label.strip()
            pending_meta = meta
            continue
        if not s or s.startswith("#"):
            continue
        item, canon = (canon or item_from_line(s, origin_dir)), None
        if item is None:
            continue
        if pending_meta:
            for k in ("title", "artist", "duration"):
                if pending_meta.get(k) is not None and not item.get(k):
                    item[k] = pending_meta[k]
        items.append(item)
        pending_meta = None
    return items


def _stored_name(path: Path) -> str:
    """The user-facing name, kept separately from the filesystem-safe slug.

    Older files have no metadata line, so their filename remains a sensible
    fallback.  The marker is a comment and is deliberately left out of exports.
    """
    fallback = path.stem.replace("-", " ").title()
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#EXTREVAMP-NAME:"):
                name = json.loads(line[len("#EXTREVAMP-NAME:"):])
                if isinstance(name, str) and name.strip():
                    return name.strip()
    except (OSError, ValueError):
        pass
    return fallback


def _read_pls(path: Path, origin_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    # Very small INI-ish parse: File1=, Title1=, Length1=
    entries: Dict[int, Dict[str, str]] = {}
    order: List[int] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if "=" not in s:
                continue
            key, val = s.split("=", 1)
            key = key.strip()
            m = re.match(r"(File|Title|Length)(\d+)$", key, re.I)
            if not m:
                continue
            idx = int(m.group(2))
            if idx not in entries:
                entries[idx] = {}
                order.append(idx)
            entries[idx][m.group(1).lower()] = val.strip()
    except OSError:
        return []
    items: List[Dict[str, Any]] = []
    for idx in sorted(order):
        e = entries[idx]
        item = item_from_line(e.get("file", ""), origin_dir)
        if item is None:
            continue
        if e.get("title") and not item.get("title"):
            label = e["title"]
            if " - " in label:
                a, t = label.split(" - ", 1)
                item["artist"], item["title"] = a.strip(), t.strip()
            else:
                item["title"] = label
        if e.get("length"):
            try:
                item["duration"] = float(e["length"])
            except ValueError:
                pass
        items.append(item)
    return items


def _write_m3u(path: Path, name: str, items: List[Dict[str, Any]]) -> None:
    lines = ["#EXTM3U", "#EXTREVAMP-NAME:" + json.dumps(name, ensure_ascii=False)]
    for it in items:
        lines.append("#EXTREVAMP:" + json.dumps(it, ensure_ascii=False, separators=(",", ":")))
        try:
            secs = int(float(it.get("duration") or -1))
        except (TypeError, ValueError):
            secs = -1
        label = " - ".join(
            x for x in (it.get("artist"), it.get("title")) if x
        ) or str(it.get("title") or "Unknown")
        lines.append(f"#EXTINF:{secs},{label}")
        lines.append(item_path(it))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _unique_slug(base: str) -> str:
    slug = slugify(base)
    candidate, n = slug, 2
    while (playlists_dir() / f"{candidate}.m3u8").exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def list_playlists() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for p in sorted(playlists_dir().glob("*.m3u8")):
            pid = p.stem
            pl = get_playlist(pid)
            if pl is None:
                continue
            count, duration = _meta_from_items(pl["items"])
            out.append({"id": pid, "name": pl["name"], "count": count,
                        "duration": round(duration, 2)})
    return out


def get_playlist(pid: str) -> Optional[Dict[str, Any]]:
    """id | slug; name match fallback for friendlier lookups."""
    with _LOCK:
        p = playlists_dir() / f"{slugify(pid)}.m3u8"
        if not p.exists():
            for cand in playlists_dir().glob("*.m3u8"):
                if cand.stem == pid or _stored_name(cand) == pid:
                    p = cand
                    break
            else:
                return None
        items = _read_m3u(p)
        return {"id": p.stem, "name": _stored_name(p), "items": items}


def _write_and_return(slug: str, name: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = [n for n in map(_normalize, items) if n is not None]
    with _LOCK:
        p = playlists_dir() / f"{slug}.m3u8"
        _write_m3u(p, name, items)
    return {"id": slug, "name": name, "items": items}


def create_playlist(name: str, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    name = str(name or "").strip() or "Untitled"
    with _LOCK:
        slug = _unique_slug(name)
        return _write_and_return(slug, name, list(items or []))


def update_playlist(pid: str, name: Optional[str] = None,
                    items: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        existing = get_playlist(pid)
        if existing is None:
            return None
        new_name = str(name).strip() if name else existing["name"]
        new_items = list(items) if items is not None else existing["items"]
        # The id never changes: the display name is stored in the file, so a rename keeps
        # every link, open view and queue reference to this playlist working.
        return _write_and_return(existing["id"], new_name, new_items)


def delete_playlist(pid: str) -> bool:
    with _LOCK:
        existing = get_playlist(pid)
        if existing is None:
            return False
        try:
            (playlists_dir() / f"{existing['id']}.m3u8").unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

def import_playlist(name: str, fmt: str, text: str,
                    origin_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    fmt = (fmt or "").lower().lstrip(".")
    if fmt not in ("m3u", "m3u8", "pls"):
        return None
    with _LOCK:
        tmp = playlists_dir() / ".import-tmp"
        tmp.write_text(text, encoding="utf-8")
        try:
            origin = Path(origin_dir).expanduser() if origin_dir else None
            items = _read_pls(tmp, origin) if fmt == "pls" else _read_m3u(tmp, origin)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    if not items:
        return None
    return create_playlist(name or "Imported", items)


def export_playlist(pid: str, fmt: str) -> Optional[tuple[str, str, str]]:
    """(filename, body, content_type) or None."""
    with _LOCK:
        pl = get_playlist(pid)
    if pl is None:
        return None
    fmt = (fmt or "m3u8").lower().lstrip(".")
    if fmt == "pls":
        lines = ["[playlist]"]
        for i, it in enumerate(pl["items"], 1):
            lines.append(f"File{i}={item_path(it)}")
            label = " - ".join(x for x in (it.get("artist"), it.get("title")) if x)
            lines.append(f"Title{i}={label}")
            try:
                secs = int(float(it.get("duration") or 0))
            except (TypeError, ValueError):
                secs = 0
            lines.append(f"Length{i}={secs}")
        lines.append(f"NumberOfEntries={len(pl['items'])}")
        lines.append("Version=2")
        return (pl["id"] + ".pls", "\n".join(lines) + "\n", "audio/x-scpls")
    lines = ["#EXTM3U"]
    for it in pl["items"]:
        try:
            secs = int(float(it.get("duration") or -1))
        except (TypeError, ValueError):
            secs = -1
        label = " - ".join(x for x in (it.get("artist"), it.get("title")) if x) \
            or str(it.get("title") or "Unknown")
        lines.append(f"#EXTINF:{secs},{label}")
        lines.append(item_path(it))
    return (pl["id"] + ".m3u8", "\n".join(lines) + "\n", "audio/x-mpegurl")
