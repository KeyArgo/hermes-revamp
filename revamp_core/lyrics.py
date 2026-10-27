"""Synced lyrics from LRCLIB (https://lrclib.net) — free, no API key.

The server fetches, parses (LRC) and caches lyrics: in memory for the
process lifetime and on disk in ``<data_dir>/lyrics-cache.json`` so hits
survive restarts. Hits are cached forever, misses (404) for 7 days; the
on-disk cache is capped at 5000 entries. Network errors are never cached.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import data_dir

LRCLIB_URL = "https://lrclib.net/api/get"
UA = "Revamp (Hermes plugin; https://github.com/KeyArgo/hermes-revamp)"
TIMEOUT = 8
CACHE_NAME = "lyrics-cache.json"
MISS_TTL = 7 * 24 * 3600.0
MAX_ENTRIES = 5000

_LOCK = threading.RLock()
_MEM: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
_CACHE: Dict[str, Dict[str, Any]] = {}
_loaded = False


class LyricsError(Exception):
    pass


# ---------------------------------------------------------------------------
# LRC parsing
# ---------------------------------------------------------------------------

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_META_TAG = re.compile(r"^\[([A-Za-z#][^:\]]*):(.*)\]\s*$")


def _stamp_seconds(minutes: str, seconds: str, frac: str) -> float:
    frac = frac or ""
    frac_val = (int(frac) / (10 ** len(frac))) if frac else 0.0
    return int(minutes) * 60 + int(seconds) + frac_val


def parse_lrc(text: str) -> List[Dict[str, Any]]:
    """Parse LRC text into ``[{"t": seconds, "line": str}, ...]`` sorted by t.

    Several timestamps on one line each produce an entry; metadata tags such
    as ``[ar:...]`` are ignored, but ``[offset:+ms]`` applies document-wide
    (positive makes lines earlier, never below 0), including lines that
    precede the tag. Timestamped lines with no text are kept as ``""``
    (instrumental gaps); unparseable lines are skipped.
    """
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    offset = 0.0
    for line in lines:
        if not line:
            continue
        if not _TIME_TAG.search(line):
            meta = _META_TAG.match(line)
            if meta and meta.group(1).strip().lower() == "offset":
                try:
                    offset = float(meta.group(2).strip())
                except ValueError:
                    continue
    out: List[Dict[str, Any]] = []
    for line in lines:
        if not line:
            continue
        stamps = _TIME_TAG.findall(line)
        if not stamps:
            continue  # metadata / unparseable
        content = _TIME_TAG.sub("", line).strip()
        for mm, ss, frac in stamps:
            t = max(0.0, _stamp_seconds(mm, ss, frac) - offset / 1000.0)
            out.append({"t": round(t, 3), "line": content})
    out.sort(key=lambda e: e["t"])
    return out


# ---------------------------------------------------------------------------
# LRCLIB fetch
# ---------------------------------------------------------------------------

def _fetch(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """GET LRCLIB ``/api/get``; returns the parsed JSON dict.

    404 (no lyrics for this query) returns ``None``. Any other HTTP error or
    network problem raises :class:`LyricsError`.
    """
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    url = LRCLIB_URL + "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise LyricsError(f"lrclib HTTP {exc.code}") from exc
    except LyricsError:
        raise
    except Exception as exc:  # timeouts, URLError, JSON problems
        raise LyricsError(f"lrclib unreachable: {exc}") from exc


# ---------------------------------------------------------------------------
# Cache (memory + <data_dir>/lyrics-cache.json)
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    return data_dir() / CACHE_NAME


def _key(artist: str, title: str, album: str, duration: Optional[float]) -> Tuple[str, str, str, int]:
    return (artist.strip().lower(), title.strip().lower(), (album or "").strip().lower(),
            int(round(duration or 0)))


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _CACHE.update({key: record for key, record in raw.items()
                           if isinstance(record, dict)})
    except (OSError, ValueError):
        pass
    if len(_CACHE) > MAX_ENTRIES:  # a file from an older or edited cache: hold the cap
        for key in list(_CACHE)[:len(_CACHE) - MAX_ENTRIES]:
            del _CACHE[key]
        _save()


def _save() -> None:
    try:
        _cache_path().write_text(json.dumps(_CACHE), encoding="utf-8")
    except OSError:
        pass


def _cache_put(key: Tuple[str, str, str, int], value: Dict[str, Any], hit: bool) -> None:
    record = {"hit": hit, "ts": time.time(), "data": value}
    cache_key = json.dumps(key, ensure_ascii=False)
    _CACHE[cache_key] = record
    dropped: List[str] = []
    if len(_CACHE) > MAX_ENTRIES:
        now = time.time()
        dropped = [k for k, v in _CACHE.items()
                   if not v.get("hit") and now - v.get("ts", 0) > MISS_TTL]
        for k in dropped:
            del _CACHE[k]
    while len(_CACHE) > MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k].get("ts", 0))
        dropped.append(oldest)
        del _CACHE[oldest]
    for k in dropped:  # keep the memory cache within the same bound
        try:
            mem_key = tuple(json.loads(k))
            del _MEM[mem_key]
        except (ValueError, TypeError, KeyError):
            continue
    if len(_MEM) > MAX_ENTRIES:
        for mem_key in sorted(_MEM, key=lambda k: _MEM[k].get("ts", 0))[
                :len(_MEM) - MAX_ENTRIES]:
            del _MEM[mem_key]
    _save()


def _cache_get(key: Tuple[str, str, str, int]) -> Optional[Dict[str, Any]]:
    record = _CACHE.get(json.dumps(key, ensure_ascii=False))
    if not isinstance(record, dict):
        return None
    if not record.get("hit") and time.time() - record.get("ts", 0) > MISS_TTL:
        return None
    return record.get("data")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_TITLE_NOISE = re.compile(
    r"(\s*\((?:remastered|re-mastered|remaster|deluxe|expanded|edition|version|"
    r"bonus|single|radio|album|live)[^)]*\)\s*|\s+-[^-|]*(?:remaster|deluxe|"
    r"expanded|edition|version|remix)[^-|]*$)", re.IGNORECASE)
_FEAT = re.compile(r"\s*(?:feat\.?|featuring|ft\.?|with)\s+.*$", re.IGNORECASE)


def _title_variants(title: str) -> List[str]:
    """The title as-is, then cleaned of remaster/feat suffixes."""
    seen: List[str] = []
    t = title.strip()
    if t:
        seen.append(t)
    stripped = _FEAT.sub("", t).strip(" -–—")
    for pattern in (r"\s*\((?:remaster|re-master)[^)]*\)\s*",):
        stripped = re.sub(pattern, " ", stripped, flags=re.IGNORECASE).strip()
    stripped = re.sub(r"\s+-.*remaster.*$", " ", stripped,
                      flags=re.IGNORECASE).strip(" -–—")
    stripped = _FEAT.sub("", stripped).strip(" -–—")
    if stripped and stripped.lower() != t.lower() and stripped not in seen:
        seen.append(stripped)
    return seen


def lyrics_for(artist: str, title: str, album: str = "",
               duration: Optional[float] = None) -> Dict[str, Any]:
    """Lyrics for a track: ``{found, synced, plain, instrumental, source}``.

    Cached in memory and on disk (keyed by normalized artist/title/album/
    duration); misses live 7 days, hits forever. Network errors return
    ``found False`` plus an ``error`` message and are not cached.
    """
    with _LOCK:
        _load()
    artist = (artist or "").strip()
    title = (title or "").strip()
    album = (album or "").strip()
    dur = int(round(duration)) if duration else None
    key = _key(artist, title, album, dur)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    with _LOCK:
        cached = _cache_get(key)
        mem = _MEM.get(key)
        if cached is None and _MEM.get(key) is not None:
            # an expired miss in the disk cache also invalidates the memory copy
            del _MEM[key]
            mem = None
    hit = cached if cached is not None else mem
    if hit is not None:
        result = dict(hit)
        result["found"] = bool(result.get("plain") or result.get("synced")
                               or result.get("instrumental"))
        result.setdefault("source", "lrclib")
    elif artist and title:
        for probe_title in _title_variants(title):
            params: Dict[str, Any] = {"artist_name": artist, "track_name": probe_title}
            if album:
                params["album_name"] = album
            if dur:
                params["duration"] = dur
            try:
                data = _fetch(params)
            except LyricsError as exc:
                error = str(exc)
                break
            if isinstance(data, dict) and (data.get("syncedLyrics") or data.get("plainLyrics")
                                           or data.get("instrumental")):
                payload = {
                    "synced": parse_lrc(data.get("syncedLyrics") or ""),
                    "plain": str(data.get("plainLyrics") or ""),
                    "instrumental": bool(data.get("instrumental")),
                }
                with _LOCK:
                    _MEM[key] = dict(payload)
                    _cache_put(key, dict(payload), hit=True)
                return {"found": True, **payload, "source": "lrclib"}
        if error is None:
            miss = {"synced": [], "plain": "", "instrumental": False}
            with _LOCK:
                _MEM[key] = dict(miss)
                _cache_put(key, dict(miss), hit=False)
    if result is None:
        result = {"found": False, "synced": [], "plain": "", "instrumental": False,
                  "source": "lrclib"}
        if error:
            result["error"] = error
    return result
