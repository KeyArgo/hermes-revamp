"""Winamp Skin Museum client (skins.webamp.org) — browse, install, list, remove.

All network calls are stdlib ``urllib``; results are cached in-process for an
hour. Skin files are downloaded once into ``<data>/skins/museum/`` and served
from there by the HTTP server.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import io
import zipfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import data_dir

GRAPHQL = "https://api.webamp.org/graphql"
SKIN_URL = "https://r2.webampskins.org/skins/{md5}.wsz"
SHOT_URL = "https://r2.webampskins.org/screenshots/{md5}.png"
PAGE_URL = "https://skins.webamp.org/skin/{md5}"
BASE_MD5 = "5e4f10275dcb1fb211d4a8b4f1bda236"  # base-2.91.wsz: our Classic skin
MAX_BYTES = 10 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; Revamp Hermes plugin; +https://skins.webamp.org)"

TTL = 3600.0

_CACHE: Dict[Any, Any] = {}
_CACHE_LOCK = threading.Lock()
# index.json is read-modify-write: one writer at a time. (One Revamp server
# owns a data dir: ensure_server binds a single port per profile.)
_INDEX_LOCK = threading.Lock()
# Striped per-skin locks (fixed count, so it can't grow): a download only
# blocks skins that hash to the same stripe, never all of them.
_MD5_LOCKS = [threading.Lock() for _ in range(64)]

# A skin is unpacked in the renderer: refuse archives that would blow up there.
MAX_ENTRIES = 500
MAX_UNPACKED = 40 * 1024 * 1024


class MuseumError(Exception):
    """Network/API failure while talking to the skin museum."""


# ---------------------------------------------------------------------------
# Network primitives
# ---------------------------------------------------------------------------

def _graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise MuseumError(f"graphql failed: {exc}") from exc
    if not isinstance(body, dict) or body.get("errors"):
        raise MuseumError(f"graphql errors: {body!r}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise MuseumError("graphql: no data")
    return data


def _download(md5: str) -> bytes:
    # The museum's file host refuses urllib's default User-Agent (403).
    req = urllib.request.Request(SKIN_URL.format(md5=md5), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_BYTES + 1)
    except Exception as exc:
        raise MuseumError(f"download failed: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise MuseumError("skin larger than 10 MiB")
    return data


def pretty(filename: str) -> str:
    stem = re.sub(r"\.(wsz|zip)$", "", filename, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


# ---------------------------------------------------------------------------
# Browse (cached)
# ---------------------------------------------------------------------------

def _cached(key: Any, fn):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    value = fn()
    with _CACHE_LOCK:
        if len(_CACHE) >= 256:  # every search is a key: keep it bounded
            _CACHE.clear()
        _CACHE[key] = (time.time(), value)
    return value


def browse(q: str = "", offset: int = 0, limit: int = 24) -> Dict[str, Any]:
    limit = max(1, min(60, int(limit)))
    offset = max(0, min(100000, int(offset)))
    q = (q or "").strip()

    def fetch():
        if not q:
            query = ("query($first:Int,$offset:Int){ skins(first:$first, "
                     "offset:$offset, sort:MUSEUM){ count nodes{ md5 filename nsfw } } }")
            data = _graphql(query, {"first": limit, "offset": offset})
            conn = data.get("skins") or {}
            return conn.get("count"), conn.get("nodes") or []
        query = ("query($query:String!,$first:Int,$offset:Int,$nsfw:Boolean){ "
                 "search_classic_skins(query:$query, first:$first, offset:$offset, "
                 "nsfw:$nsfw){ md5 filename nsfw } }")
        data = _graphql(query, {"query": q, "first": limit, "offset": offset,
                                "nsfw": False})
        return None, data.get("search_classic_skins") or []

    count, nodes = _cached(("browse", q, offset, limit), fetch)
    skins = []
    for node in nodes:
        if node.get("nsfw"):
            continue
        md5 = node.get("md5")
        if not md5 or md5 == BASE_MD5:
            continue
        skins.append({
            "md5": md5,
            "id": f"museum:{md5}",
            "name": pretty(node.get("filename") or ""),
            "screenshot": SHOT_URL.format(md5=md5),
            "museum_url": PAGE_URL.format(md5=md5),
            "installed": _cached_file(md5) is not None,
        })
    return {"skins": skins, "total": count, "offset": offset,
            "next": offset + limit if len(nodes) == limit else None}


# ---------------------------------------------------------------------------
# Local cache / install / remove
# ---------------------------------------------------------------------------

def _dir() -> Path:
    d = data_dir() / "skins" / "museum"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _index_path() -> Path:
    return _dir() / "index.json"


def _load_index() -> Dict[str, Dict[str, Any]]:
    try:
        raw = json.loads(_index_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(data: Dict[str, Dict[str, Any]]) -> None:
    path = _index_path()
    _dir()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".index-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cached_file(md5: str) -> Optional[Path]:
    p = _dir() / f"{md5}.wsz"
    return p if p.is_file() else None


def check_skin(data: bytes) -> None:
    """Raise MuseumError unless *data* is a sane classic skin archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise MuseumError(f"not a skin archive: {exc}") from exc
    if len(infos) > MAX_ENTRIES:
        raise MuseumError(f"skin has too many files ({len(infos)})")
    if sum(max(0, i.file_size) for i in infos) > MAX_UNPACKED:
        raise MuseumError("skin unpacks too large")
    if not any(not i.is_dir() and i.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == "main.bmp"
               for i in infos):
        raise MuseumError("not a classic skin (no main.bmp)")


def valid(md5: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", md5 or ""))


def entry(md5: str) -> Dict[str, Any]:
    names = _load_index()
    name = ""
    if isinstance(names.get(md5), dict):
        name = str(names[md5].get("name") or "")
    if not name:
        name = md5
    return {"id": f"museum:{md5}", "name": name,
            "url": f"/skins/museum/{md5}.wsz", "builtin": False,
            "source": "museum", "screenshot": SHOT_URL.format(md5=md5)}


def installed() -> List[Dict[str, Any]]:
    out = []
    for p in _dir().glob("*.wsz"):
        md5 = p.stem
        if valid(md5):
            out.append(entry(md5))
    out.sort(key=lambda e: e["name"].lower())
    return out


def _md5_lock(md5: str) -> threading.Lock:
    return _MD5_LOCKS[int(md5[:4], 16) % len(_MD5_LOCKS)]


def _write_skin(md5: str, data: bytes) -> None:
    d = _dir()
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".skin-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, d / f"{md5}.wsz")
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _stored_name(md5: str) -> str:
    stored = _load_index().get(md5)
    return str(stored.get("name") or "") if isinstance(stored, dict) else ""


def install(md5: str, name: str = "") -> Dict[str, Any]:
    """Download (once), validate and cache a museum skin; returns its entry.

    A per-skin lock stops two clicks downloading the same skin twice; different
    skins download in parallel. _INDEX_LOCK is held only for the index update."""
    if not valid(md5):
        raise ValueError(f"bad md5 {md5!r}")
    with _md5_lock(md5):
        cached = _cached_file(md5)
        if cached is not None:
            try:  # a file cached by an older version is checked too
                check_skin(cached.read_bytes())
            except (MuseumError, OSError):
                cached.unlink(missing_ok=True)
                cached = None
        fresh = cached is None
        if cached is None:
            data = _download(md5)
            if data[:2] != b"PK":
                raise MuseumError("not a wsz/zip file")
            check_skin(data)
            try:
                _write_skin(md5, data)
            except OSError as exc:
                raise MuseumError(f"could not save the skin: {exc}") from exc

        final = (name or "").strip() or _stored_name(md5)
        if not final:
            try:
                found = _graphql(
                    "query($md5:String!){ fetch_skin_by_md5(md5:$md5){ md5 filename } }",
                    {"md5": md5})
                final = pretty(str((found.get("fetch_skin_by_md5") or {}).get("filename") or ""))
            except MuseumError:
                final = ""
        final = final or md5

        with _INDEX_LOCK:
            index = _load_index()
            index[md5] = {"name": final}
            try:
                _save_index(index)
            except OSError as exc:
                # Don't report a half-installed skin as installed; but a reinstall of a
                # skin that was already there must not delete it.
                if fresh:
                    (_dir() / f"{md5}.wsz").unlink(missing_ok=True)
                raise MuseumError(f"could not save the skin list: {exc}") from exc
        return entry(md5)


def remove(md5: str) -> bool:
    if not valid(md5):
        return False
    with _md5_lock(md5):
        p = _dir() / f"{md5}.wsz"
        if not p.is_file():
            return False
        try:
            p.unlink()
        except OSError:
            return False
        with _INDEX_LOCK:
            index = _load_index()
            if index.pop(md5, None) is not None:
                try:
                    _save_index(index)
                except OSError:
                    pass  # the skin is gone; a leftover name is harmless
        return True
