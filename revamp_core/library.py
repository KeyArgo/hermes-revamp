"""Library scan, index and query layer for Revamp.

The index is a dict ``id -> Track`` persisted to ``<data>/library.json``.
Scan runs in a background thread; entries whose ``(size, mtime)`` are
unchanged are reused (incremental). Symlinked dirs are followed with a
loop guard (visited-realpath set).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import data_dir, library_folders
from . import store
from . import tags as tags_mod

INDEX_NAME = "library.json"

_LOCK = threading.RLock()
# Scans are numbered. Only the newest one may commit, so an older scan of
# folders the user just removed can't finish last and overwrite the index.
_GEN = 0          # number of the newest scan started
_DONE_GEN = 0     # newest scan that has finished (committed or superseded)
_LAST_SCAN: float = 0.0
# Tag reads are slow on network shares (~250 ms each via ffprobe), so a scan
# reads a few at once (at background priority, see tags.py) and publishes what it
# has every few seconds. 8 workers once pinned a desktop's CPU on a 10k-song NAS scan.
SCAN_WORKERS = 3
PUBLISH_EVERY = 2.0
# A long scan saves its progress this often, so a stopped or crashed scan resumes from the
# files already read (cached by mtime/size) instead of re-reading every file.
CHECKPOINT_EVERY = 60.0
_PROGRESS: Dict[str, int] = {"gen": 0, "done": 0, "total": 0}
_INDEX: Dict[str, Dict[str, Any]] = {}
_COMPLETE = True  # False while the saved index is a mid-scan checkpoint


# ---------------------------------------------------------------------------
# Identity + persistence
# ---------------------------------------------------------------------------

def track_id_for(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]


def _index_path() -> Path:
    return data_dir() / INDEX_NAME


def load_index() -> None:
    global _INDEX, _LAST_SCAN, _COMPLETE
    with _LOCK:
        try:
            raw = json.loads(_index_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _INDEX = {k: v for k, v in raw.get("tracks", {}).items()
                          if isinstance(v, dict)}
                _LAST_SCAN = float(raw.get("last_scan") or 0)
                _COMPLETE = raw.get("complete", True) is not False
        except (OSError, ValueError):
            _INDEX = {}


def save_index(complete: bool = True) -> None:
    """complete=False marks a mid-scan checkpoint: the next start finishes the scan."""
    import tempfile

    global _COMPLETE
    path = _index_path()
    with _LOCK:
        _COMPLETE = complete
        payload = {"tracks": _INDEX, "last_scan": _LAST_SCAN, "complete": complete}
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".lib-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _walk(root: Path, visited: set[str], out: List[str]) -> None:
    try:
        real = os.path.realpath(root)
        if real in visited:
            return
        visited.add(real)
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        # Hidden files and folders (.git, .cache, an app's .hermes...) are never music.
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir(follow_symlinks=True):
                _walk(Path(entry.path), visited, out)
            elif entry.is_file(follow_symlinks=True):
                if os.path.splitext(entry.name)[1].lower() in tags_mod.AUDIO_EXTS:
                    out.append(entry.path)
        except OSError:
            continue


def _entry_for(path: str, cached: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    size, mtime = st.st_size, st.st_mtime
    if cached and cached.get("size") == size and cached.get("mtime") == mtime \
            and cached.get("path") == path:
        return cached
    meta = tags_mod.probe(path)
    tid = track_id_for(path)
    entry: Dict[str, Any] = {
        "id": tid,
        "root": None,  # filled by caller
        "path": path,
        "title": meta.get("title") or Path(path).stem,
        "artist": meta.get("artist"),
        "album": meta.get("album"),
        "album_artist": meta.get("album_artist"),
        "track_no": meta.get("track_no"),
        "disc_no": meta.get("disc_no"),
        "year": meta.get("year"),
        "genre": meta.get("genre"),
        "duration": meta.get("duration"),
        "bitrate_kbps": meta.get("bitrate_kbps"),
        "sample_rate": meta.get("sample_rate"),
        "channels": meta.get("channels"),
        "codec": meta.get("codec"),
        "size": size,
        "mtime": mtime,
        "has_art": bool(tags_mod.has_art(path)),
        "url": f"/api/media/{tid}",
        "added": cached.get("added") if cached else time.time(),
    }
    return entry


def _superseded(gen: int) -> bool:
    return gen != _GEN


def _reserve() -> int:
    """Number a new scan; it is now the newest, and is_scanning() is true until it ends."""
    global _GEN
    with _LOCK:
        _GEN += 1
        return _GEN


def _finish(gen: int) -> None:
    global _DONE_GEN
    with _LOCK:
        _DONE_GEN = max(_DONE_GEN, gen)


def scan_sync(roots: Optional[List[str]] = None, gen: Optional[int] = None) -> int:
    """Blocking scan; returns the new track count.

    Every scan is numbered (a direct call takes the next number itself; scan_async
    passes the one it reserved). A scan gives up, without committing, once a newer
    one has started."""
    if gen is None:
        gen = _reserve()
    try:
        return _scan(roots, gen)
    finally:
        _finish(gen)


def _scan(roots: Optional[List[str]], gen: int) -> int:
    global _INDEX, _LAST_SCAN
    roots = [str(r) for r in (roots or []) if str(r).strip()]
    with _LOCK:
        if not _INDEX:
            load_index()
        cached_all = dict(_INDEX)
        if not _superseded(gen):
            _PROGRESS.update(gen=gen, done=0, total=0)
    work: List[tuple] = []
    root_names = set()
    for root in roots:
        r = Path(root).expanduser()
        root_names.add(str(r))
        files: List[str] = []
        if r.is_dir():
            _walk(r, set(), files)
        if _superseded(gen):
            return len(_INDEX)
        work.extend((path, str(r)) for path in files)
    with _LOCK:
        if _superseded(gen):
            return len(_INDEX)
        _PROGRESS["total"] = len(work)
    # Until this scan finishes, keep listing what the index already knew about
    # these folders; tracks under folders no longer configured drop out.
    known = {k: v for k, v in cached_all.items() if v.get("root") in root_names}
    found: Dict[str, Dict[str, Any]] = {}

    def read(item: tuple) -> Optional[Dict[str, Any]]:
        path, root = item
        if _superseded(gen):
            return None
        try:
            entry = _entry_for(path, cached_all.get(track_id_for(path)))
        except Exception:  # one unreadable file must not sink the whole scan
            return None
        if entry:
            entry = dict(entry, root=root)  # never mutate a cached entry in place
        return entry

    # A bounded queue, taken in completion order: one slow file never holds up
    # the rest, and a superseded scan returns without waiting on its reads.
    pool = ThreadPoolExecutor(SCAN_WORKERS, thread_name_prefix="revamp-tags")
    pending: set = set()
    queue = iter(work)
    done = 0
    last_publish = time.time()
    last_checkpoint = time.time()
    try:
        while True:
            while len(pending) < SCAN_WORKERS * 4:
                item = next(queue, None)
                if item is None:
                    break
                pending.add(pool.submit(read, item))
            if not pending:
                break
            finished, pending = wait(pending, timeout=PUBLISH_EVERY, return_when=FIRST_COMPLETED)
            if _superseded(gen):
                return len(_INDEX)
            for fut in finished:
                done += 1
                entry = fut.result()
                if entry:
                    found[entry["id"]] = entry
            with _LOCK:
                if _superseded(gen):
                    return len(_INDEX)
                _PROGRESS["done"] = done
                if time.time() - last_publish >= PUBLISH_EVERY:
                    last_publish = time.time()
                    _INDEX = {**known, **found}
                if time.time() - last_checkpoint >= CHECKPOINT_EVERY:
                    last_checkpoint = time.time()
                    _INDEX = {**known, **found}
                    save_index(complete=False)  # a stopped scan resumes from here on the next start
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    with _LOCK:
        if _superseded(gen):
            return len(_INDEX)
        # Files that vanished from disk are dropped: the index reflects the disk.
        _INDEX = found
        _LAST_SCAN = time.time()
        _PROGRESS["done"] = len(work)
        save_index()
        return len(_INDEX)


def scan_progress() -> Dict[str, int]:
    """Files read / found by the newest scan (meaningful while is_scanning())."""
    with _LOCK:
        return {"done": _PROGRESS["done"], "total": _PROGRESS["total"]} if _PROGRESS["gen"] == _GEN else {"done": 0, "total": 0}


def scan_async(roots: Optional[List[str]] = None) -> None:
    gen = _reserve()

    def run() -> None:
        try:
            scan_sync(roots, gen)
        except Exception:
            pass

    try:
        threading.Thread(target=run, name="revamp-scan", daemon=True).start()
    except RuntimeError:
        _finish(gen)  # never leave is_scanning() stuck on a scan that didn't start
        raise


def index_complete() -> bool:
    """False when the saved index is a checkpoint of a scan that never finished."""
    return _COMPLETE


def is_scanning() -> bool:
    """True until the newest scan has finished; superseded ones don't count."""
    with _LOCK:
        return _DONE_GEN < _GEN


def last_scan() -> float:
    with _LOCK:
        return _LAST_SCAN


def all_tracks() -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_INDEX.values())


def get_track(track_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _INDEX.get(track_id)


_MAX_LEARNED_DURATION = 48 * 3600


def set_learned_duration(track_id: str, duration: Any) -> Optional[float]:
    """Remember a duration the player measured while the file was open.

    Used when the file had no duration (no ffprobe, and the header parse missed).
    A value already within half a second is left alone. Returns the duration now
    stored, or None when the track is missing or the number is unusable.
    """
    if not isinstance(track_id, str) or not track_id:
        return None
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return None
    dur = float(duration)
    if dur != dur or dur in (float("inf"), float("-inf")) or dur <= 0 or dur > _MAX_LEARNED_DURATION:
        return None
    dur = round(dur, 3)
    with _LOCK:
        track = _INDEX.get(track_id)
        if not isinstance(track, dict):
            return None
        try:
            old = None if track.get("duration") is None else float(track.get("duration"))
        except (TypeError, ValueError):
            old = None
        if old is not None and old == old and abs(old - dur) < 0.5:
            return round(old, 3)
        track["duration"] = dur
    save_index()
    return dur


# ---------------------------------------------------------------------------
# Search / listing
# ---------------------------------------------------------------------------

_AUDIOBOOK_EXTS = {".m4b"}
_AUDIOBOOK_WORDS = ("audiobook", "audio book", "spoken", "speech",
                    "podcast", "book")
_LONG_TRACK_SECS = 1200


def _norm_root(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path))


def _kinds_map() -> Dict[str, str]:
    """Normalized folder path -> kind ("music" or "books")."""
    out: Dict[str, str] = {}
    for f in library_folders():
        out[_norm_root(str(f.get("path") or ""))] = str(f.get("kind") or "music")
    return out


def folder_of(path: str, folders) -> str:
    """The innermost of *folders* (normalized paths) holding *path*, or ''."""
    path = _norm_root(path)
    best = ""
    for folder in folders:
        if (path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)) and len(folder) > len(best):
            best = folder
    return best


def kind_of(track: dict, kinds: Optional[Dict[str, str]] = None) -> str:
    """Classify a track dict as ``audiobook`` or ``music``.

    The folder kind wins when the file is in a configured folder; otherwise guess."""
    # The innermost configured folder holding the file decides: a Books folder
    # inside a Music folder is still Books, whichever root the scan filed it under.
    path = _norm_root(str(track.get("path") or track.get("root") or ""))
    if kinds is None:
        kinds = _kinds_map()
    best = folder_of(path, kinds)
    folder_kind = kinds.get(best) if best else None
    if folder_kind == "music":
        return "music"
    if folder_kind == "books":
        return "audiobook"
    ext = os.path.splitext(str(track.get("path") or ""))[1].lower()
    if ext in _AUDIOBOOK_EXTS:
        return "audiobook"
    genre = str(track.get("genre") or "").strip().lower()
    if genre:
        if any(w in genre for w in _AUDIOBOOK_WORDS):
            return "audiobook"
        return "music"
    try:
        duration = float(track.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return "audiobook" if duration > _LONG_TRACK_SECS else "music"


# Scripts written without spaces between words: a query word matches anywhere in them.
_NO_SPACE_SCRIPTS = re.compile(
    r"[\u1100-\u11ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af\u0e00-\u0e7f\uf900-\ufaff]"
)


def _fold(text: str) -> str:
    """Lower-case, accents dropped ("Björk" -> "bjork"), every non-letter/digit a word break.
    Unicode-aware: Cyrillic, Greek, CJK... stay searchable (an ASCII-only fold once turned a
    Chinese query into an empty word that matched every track)."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", text).strip()


def _matches(entry: Dict[str, Any], words: List[str]) -> bool:
    hay = _fold(" ".join(
        str(entry.get(k) or "") for k in ("title", "artist", "album", "path")
    ))
    # Each query word must start a word in the haystack ("tone" matches
    # "Test Tone", not "Blackstone"); punctuation counts as a word break.
    padded = " " + hay
    tokens = [t for t in (_fold(w) for w in words) if t]
    # Only punctuation typed (e.g. "-"): no words to filter by, the same as an empty search.
    return all((t in hay) if _NO_SPACE_SCRIPTS.search(t) else (" " + t in padded) for t in tokens)


_SORTERS = {
    "artist": lambda t: ((t.get("artist") or "").lower(), (t.get("album") or "").lower(),
                         t.get("track_no") or 0, (t.get("title") or "").lower()),
    "album": lambda t: ((t.get("album") or "").lower(), t.get("disc_no") or 0,
                        t.get("track_no") or 0, (t.get("title") or "").lower()),
    "title": lambda t: (t.get("title") or "").lower(),
    "added": lambda t: float(t.get("added") or 0),
}


def search(q: str = "", artist: str = "", album: str = "", kind: str = "",
           limit: int = 200, offset: int = 0, sort: str = "artist") -> Dict[str, Any]:
    tracks = all_tracks()
    kinds = _kinds_map()
    if q:
        tracks = [t for t in tracks if _matches(t, q.split())]
    if artist:
        a = artist.casefold()
        tracks = [t for t in tracks
                  if a == (t.get("artist") or "").casefold()
                  or a == (t.get("album_artist") or "").casefold()]
    if album:
        al = album.casefold()
        tracks = [t for t in tracks if al == (t.get("album") or "").casefold()]
    if kind:
        k = kind.lower()
        tracks = [t for t in tracks if kind_of(t, kinds) == k]
    key = _SORTERS.get(sort, _SORTERS["artist"])
    tracks.sort(key=key)
    offset = max(0, int(offset or 0))
    limit = max(0, int(limit if limit is not None else 200))
    window = tracks[offset:offset + limit] if limit else tracks[offset:]
    return {"total": len(tracks), "tracks": _public(window, kinds)}


def _public(window: List[Dict[str, Any]], kinds: Dict[str, str]) -> List[Dict[str, Any]]:
    """Track records as the routes send them: kind, a book's place, liked."""
    state = store.get_state()
    resume = state.get("resume") or {}
    liked = set(state.get("likes") or [])
    out = []
    for t in window:
        rec = dict(t, kind=kind_of(t, kinds), liked=t.get("id") in liked)
        if rec["kind"] == "audiobook":
            # Keyed by item id (local:track:<id>); bare ids are from older state files.
            raw = resume.get("local:track:" + str(t.get("id")), resume.get(t.get("id")))
            try:
                pos = int(float(raw or 0))
            except (TypeError, ValueError):
                pos = 0
            if pos > 0:
                rec["resume"] = pos
        out.append(rec)
    return out


def tracks_by_ids(ids: List[str]) -> List[Dict[str, Any]]:
    """Public records for *ids* in that order, skipping ones no longer indexed."""
    found = [t for t in (get_track(i) for i in ids) if t]
    return _public(found, _kinds_map())


def _of_kind(kind: str = "") -> List[Dict[str, Any]]:
    """All tracks, or only those of *kind* ("music" / "audiobook")."""
    tracks = all_tracks()
    if not kind:
        return tracks
    kinds = _kinds_map()
    return [t for t in tracks if kind_of(t, kinds) == kind.lower()]


def artists(kind: str = "") -> List[Dict[str, Any]]:
    """[{name, albums, tracks}] sorted by name (albums = comma-joined distinct)."""
    agg: Dict[str, Dict[str, Any]] = {}
    for t in _of_kind(kind):
        name = t.get("artist") or "Unknown Artist"
        a = agg.setdefault(name, {"name": name, "albums": set(), "tracks": 0})
        if t.get("album"):
            a["albums"].add(t["album"])
        a["tracks"] += 1
    out = [{"name": a["name"], "albums": sorted(a["albums"]), "tracks": a["tracks"]}
           for a in agg.values()]
    out.sort(key=lambda a: a["name"].lower())
    return out


def albums(artist: Optional[str] = None, kind: str = "") -> List[Dict[str, Any]]:
    """[{album, artist, year, tracks, duration, art_track_id}] sorted by album."""
    agg: Dict[tuple, Dict[str, Any]] = {}
    for t in _of_kind(kind):
        name = t.get("album") or "Unknown Album"
        key = (name.lower(), (t.get("album_artist") or t.get("artist") or "").lower())
        a = agg.setdefault(key, {
            "album": name,
            "artist": t.get("album_artist") or t.get("artist") or "Unknown Artist",
            "year": t.get("year"), "tracks": 0, "duration": 0.0,
            "_ids": [],
        })
        a["tracks"] += 1
        try:
            a["duration"] += float(t.get("duration") or 0)
        except (TypeError, ValueError):
            pass
        if t.get("year") and not a["year"]:
            a["year"] = t["year"]
        if t.get("has_art"):
            a["art_track_id"] = t["id"]
    out = []
    for a in agg.values():
        if artist and artist.lower() not in (a["artist"] or "").lower():
            continue
        out.append({
            "album": a["album"], "artist": a["artist"], "year": a["year"],
            "tracks": a["tracks"], "duration": round(a["duration"], 2),
            "art_track_id": a.get("art_track_id"),
        })
    out.sort(key=lambda a: (a["album"] or "").lower())
    return out
