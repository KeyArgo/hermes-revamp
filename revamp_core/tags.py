"""Audio tag / probe layer for the Revamp library.

Priority: ``mutagen`` (optional import) -> ``ffprobe`` (subprocess, JSON,
5 s timeout) -> filename parsing. Pure stdlib when mutagen is absent.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

try:  # optional, per SPEC
    import mutagen  # type: ignore
    _HAVE_MUTAGEN = True
except Exception:  # pragma: no cover - depends on env
    _HAVE_MUTAGEN = False

FFPROBE_TIMEOUT = 5.0

AUDIO_EXTS = (
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav", ".webm",
)

#: extension -> Content-Type served by /api/media
CONTENT_TYPES = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
    ".m4b": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/x-wav",
    ".webm": "audio/webm",
}


def content_type_for(path: str | os.PathLike) -> str:
    ext = Path(str(path)).suffix.lower()
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _humanise(stem: str) -> tuple[str, str, Optional[int]]:
    """'Artist - 07 - Title' -> (title, artist, track_no) best effort."""
    title, artist, track_no = stem, "", None
    parts = [p.strip() for p in stem.split(" - ", 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        artist, title = parts
    # Leading track number in the title ("07 Title" / "07. Title").
    digits = title.split(" ", 1)
    if len(digits) == 2 and digits[0].rstrip(".").isdigit() and len(digits[0]) <= 3:
        track_no = int(digits[0].rstrip("."))
        title = digits[1].lstrip("- ")
    return title, artist, track_no


def _background_prefix() -> list:
    """Run tag reads at the lowest CPU and disk priority: a library scan of a big NAS folder
    spawns thousands of ffprobes, and it must only ever use spare capacity."""
    prefix = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix


def _probe_ffprobe(path: str) -> Dict[str, Any]:
    cmd = _background_prefix() + [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=FFPROBE_TIMEOUT, check=False
        ).stdout
        data = json.loads(out or b"{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {}) if streams else {}
    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    duration = None
    try:
        duration = float(fmt.get("duration") or audio.get("duration") or 0) or None
    except (TypeError, ValueError):
        pass
    return {
        "duration": round(duration, 3) if duration else None,
        "bitrate_kbps": int(round(int(fmt.get("bit_rate") or 0) / 1000)) or None,
        "sample_rate": int(audio.get("sample_rate") or 0) or None,
        "channels": int(audio.get("channels") or 0) or None,
        "codec": (audio.get("codec_name") or fmt.get("format_name") or "").split(",")[0] or None,
        "title": tags.get("title"),
        "artist": tags.get("artist"),
        "album": tags.get("album"),
        "album_artist": tags.get("album_artist"),
        "track_no": _int_or_none((tags.get("track") or "").split("/")[0]),
        "disc_no": _int_or_none((tags.get("disc") or "").split("/")[0]),
        "year": _int_or_none((tags.get("date") or tags.get("year") or "")[:4]),
        "genre": tags.get("genre"),
    }


def _int_or_none(raw: Any) -> Optional[int]:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _tags_mutagen(path: str) -> Dict[str, Any]:
    """Tag extraction via mutagen; returns {} when mutagen is unavailable."""
    if not _HAVE_MUTAGEN:
        return {}
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if f is None:
        return {}

    def first(*keys: str) -> Optional[str]:
        for k in keys:
            try:
                v = getattr(f, k, None) or (f.tags.get(k) if f.tags else None)
            except Exception:
                v = None
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            if v:
                return str(v)
        return None

    info = getattr(f, "info", None)
    out: Dict[str, Any] = {
        "duration": round(getattr(info, "length", 0) or 0, 3) or None,
        "bitrate_kbps": int(round((getattr(info, "bitrate", 0) or 0) / 1000)) or None,
        "sample_rate": int(getattr(info, "sample_rate", 0) or 0) or None,
        "channels": int(getattr(info, "channels", 0) or 0) or None,
        "codec": type(info).__name__.lower().replace("info", "") or None,
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "album_artist": first("albumartist", "album artist"),
        "track_no": _int_or_none(first("tracknumber")),
        "disc_no": _int_or_none(first("discnumber")),
        "year": _int_or_none((first("date", "year") or "")[:4]),
        "genre": first("genre"),
    }
    if out["bitrate_kbps"] == 0:
        out["bitrate_kbps"] = None
    return out


def has_art(path: str) -> bool:
    """Embedded art (mutagen) or cover image next to the file."""
    if _HAVE_MUTAGEN:
        try:
            f = mutagen.File(path)
            if f is not None and getattr(f, "pictures", None):
                return True
            tags = getattr(f, "tags", None)
            if tags is not None:
                for key in ("covr", "metadata"):
                    try:
                        if tags.get(key):
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
    folder = Path(path).parent
    for name in ("cover", "folder", "front"):
        for ext in (".jpg", ".jpeg", ".png"):
            if (folder / (name + ext)).exists():
                return True
    return False


def read_cover(path: str) -> Optional[bytes]:
    """The cover art for *path*: embedded (mutagen) or cover/folder/front file."""
    if _HAVE_MUTAGEN:
        try:
            f = mutagen.File(path)
            pics = getattr(f, "pictures", None) or []
            if pics:
                return bytes(pics[0].data)
            tags = getattr(f, "tags", None)
            if tags is not None:
                for key in ("covr", "metadata"):
                    try:
                        val = tags.get(key)
                    except Exception:
                        val = None
                    if val:
                        if isinstance(val, list):
                            val = val[0]
                        data = getattr(val, "data", val)
                        if isinstance(data, (bytes, bytearray)):
                            return bytes(data)
        except Exception:
            pass
    folder = Path(path).parent
    for name in ("cover", "folder", "front"):
        for ext in (".jpg", ".jpeg", ".png"):
            p = folder / (name + ext)
            if p.exists():
                try:
                    return p.read_bytes()
                except OSError:
                    pass
    return None


def probe(path: str) -> Dict[str, Any]:
    """Full metadata for *path* (mutagen -> ffprobe -> filename heuristics).

    Always returns the tag keys with best-effort values; callers fill
    identity fields (id/path/size/...) themselves.
    """
    p = Path(path)
    meta: Dict[str, Any] = {
        "title": None, "artist": None, "album": None, "album_artist": None,
        "track_no": None, "disc_no": None, "year": None, "genre": None,
        "duration": None, "bitrate_kbps": None, "sample_rate": None,
        "channels": None, "codec": None,
    }
    meta.update(_tags_mutagen(path))
    if not any(v is not None for v in meta.values()):
        meta.update(_probe_ffprobe(path))
    # Filename fallbacks.
    title, artist, track_no = _humanise(p.stem)
    if not meta["title"]:
        meta["title"] = title
    if not meta["artist"]:
        meta["artist"] = artist or None
    if meta["track_no"] is None:
        meta["track_no"] = track_no
    if not meta["album"]:
        parent = p.parent.name
        meta["album"] = None if parent in ("", p.name) else parent
    return meta
