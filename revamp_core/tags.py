"""Audio tag / probe layer for the Revamp library.

Priority: ``mutagen`` (optional import) -> ``ffprobe`` (subprocess, JSON,
5 s timeout) -> pure-Python header parsers (MP3, FLAC, MP4/M4A/M4B, Ogg
Vorbis/Opus, WAV) -> filename parsing. No third-party packages. Audiobook
chapters still need ``ffprobe``. Parsers read headers (and a short Ogg tail),
never the whole file, and a bad file cannot fail a scan.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    # isascii: "²".isdigit() is True but int("²") raises, which aborted the whole scan.
    if len(digits) == 2 and digits[0].rstrip(".").isascii() and digits[0].rstrip(".").isdigit() and len(digits[0]) <= 3:
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
        # ffprobe writes "N/A" for numbers it doesn't know: one of those used to throw away
        # every tag of the file.
        "bitrate_kbps": int(round((_int_or_none(fmt.get("bit_rate")) or 0) / 1000)) or None,
        "sample_rate": _int_or_none(audio.get("sample_rate")) or None,
        "channels": _int_or_none(audio.get("channels")) or None,
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


# The real ffprobe function, so probe() can tell a test double from this one.
_FFPROBE_IMPL = _probe_ffprobe
_ffprobe_ok: Optional[bool] = None


def _ffprobe_available() -> bool:
    """Cached ``which ffprobe``. A missing binary must not cost a process per file."""
    global _ffprobe_ok
    if _ffprobe_ok is None:
        _ffprobe_ok = shutil.which("ffprobe") is not None
    return _ffprobe_ok


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
        # "3/12" is how most taggers write track and disc numbers.
        "track_no": _int_or_none(str(first("tracknumber") or "").split("/")[0]),
        "disc_no": _int_or_none(str(first("discnumber") or "").split("/")[0]),
        "year": _int_or_none((first("date", "year") or "")[:4]),
        "genre": first("genre"),
    }
    if out["bitrate_kbps"] == 0:
        out["bitrate_kbps"] = None
    return out


def has_art(path: str) -> bool:
    """Embedded art (mutagen or the stdlib parsers) or a cover image next to the file."""
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
    try:
        if _stdlib_has_art(path):
            return True
    except Exception:
        pass
    folder = Path(path).parent
    for name in ("cover", "folder", "front"):
        for ext in (".jpg", ".jpeg", ".png"):
            if (folder / (name + ext)).exists():
                return True
    return False


def read_cover(path: str) -> Optional[bytes]:
    """The cover art for *path*: embedded (mutagen or stdlib) or cover/folder/front file."""
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
    try:
        embedded = _stdlib_cover(path)
        if embedded:
            return embedded
    except Exception:
        pass
    folder = Path(path).parent
    for name in ("cover", "folder", "front"):
        for ext in (".jpg", ".jpeg", ".png"):
            p = folder / (name + ext)
            if p.exists():
                try:
                    if p.stat().st_size > _MAX_ART:  # a stray multi-GB file is not cover art
                        continue
                    return p.read_bytes()
                except OSError:
                    pass
    return None


_META_KEYS = (
    "title", "artist", "album", "album_artist", "track_no", "disc_no",
    "year", "genre", "duration", "bitrate_kbps", "sample_rate", "channels", "codec",
)


def _metadata_present(meta: Dict[str, Any]) -> bool:
    return any(meta.get(k) is not None for k in _META_KEYS)


def probe(path: str) -> Dict[str, Any]:
    """Full metadata for *path*.

    mutagen -> ffprobe (when that binary is the real one and it is installed,
    or when a test replaced ``_probe_ffprobe``) -> stdlib header parsers ->
    filename heuristics. Always returns the tag keys; callers fill identity
    fields (id/path/size/...) themselves.
    """
    p = Path(path)
    meta: Dict[str, Any] = {k: None for k in _META_KEYS}
    try:
        got = _tags_mutagen(path)
        if got:
            meta.update(got)
    except Exception:
        pass
    if not _metadata_present(meta):
        try:
            # Skip the subprocess when ffprobe is not installed. A replaced
            # _probe_ffprobe (tests) still runs, even on a machine without it.
            if _probe_ffprobe is not _FFPROBE_IMPL or _ffprobe_available():
                got = _probe_ffprobe(path)
                if got:
                    meta.update(got)
        except Exception:
            pass
    if not _metadata_present(meta):
        got = _probe_stdlib(path)
        if got:
            meta.update({k: v for k, v in got.items() if k in _META_KEYS})
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


# ---------------------------------------------------------------------------
# Stdlib tag / duration parsers.
# Each one is wrapped so a corrupt file returns nothing instead of failing a scan.
# Reads are bounded: ID3 text frames, container headers, and an Ogg tail.
# ---------------------------------------------------------------------------

_MAX_TEXT = 8 * 1024
_MAX_ART = 8 * 1024 * 1024
_MAX_TAG_BODY = 2 * 1024 * 1024
_MP3_SCAN = 256 * 1024
_OGG_HEAD = 64 * 1024
_OGG_TAIL = 128 * 1024

# (mpeg version 1 or 2, layer 1/2/3) -> bitrate kbps by index 0..15. Index 0 and 15 are invalid.
_MP3_BITRATE = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
}
_MP3_RATE = {
    1: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    25: (11025, 12000, 8000),
}
_MP3_SAMPLES = {
    (1, 1): 384, (1, 2): 1152, (1, 3): 1152,
    (2, 1): 384, (2, 2): 1152, (2, 3): 576,
    (25, 1): 384, (25, 2): 1152, (25, 3): 576,
}

_ID3_TEXT = {
    b"TIT2": "title", b"TPE1": "artist", b"TPE2": "album_artist", b"TALB": "album",
    b"TCON": "genre", b"TYER": "year", b"TDRC": "year",
}

_MP4_TEXT = {
    b"\xa9nam": "title", b"\xa9ART": "artist", b"\xa9alb": "album", b"aART": "album_artist",
    b"\xa9day": "year", b"\xa9gen": "genre",
}
_MP4_NUM = {b"trkn": "track_no", b"disk": "disc_no"}
_MP4_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"ilst", b"edts"}
# iTunes file tags. A chapter or text trak repeats ©nam under trak/udta; that name is not the work.
_MP4_FILE_ILST = (b"moov", b"udta", b"meta", b"ilst")


def _synchsafe(raw: bytes) -> int:
    n = 0
    for byte in raw[:4]:
        n = (n << 7) | (byte & 0x7F)
    return n


def _year_of(text: Any) -> Optional[int]:
    raw = str(text or "").strip()
    if len(raw) >= 4 and raw[:4].isdigit():
        year = int(raw[:4])
        if 1000 <= year <= 2999:
            return year
    return None


def _leading_int(text: Any) -> Optional[int]:
    raw = str(text or "").strip().split("/", 1)[0].strip()
    n = _int_or_none(raw)
    if n is None or n <= 0:
        return None
    return n


def _fill(meta: Dict[str, Any], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    if meta.get(key) in (None, ""):
        meta[key] = value


def _finish_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only real values. Duration is seconds, rounded to milliseconds."""
    out: Dict[str, Any] = {}
    dur = meta.get("duration")
    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
        if dur > 0 and dur == dur and dur != float("inf"):
            out["duration"] = round(float(dur), 3)
    for key in ("title", "artist", "album", "album_artist", "genre", "codec"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    for key in ("track_no", "disc_no", "year", "bitrate_kbps", "sample_rate", "channels"):
        val = meta.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if int(val) > 0:
            out[key] = int(val)
    if meta.get("_has_art"):
        out["_has_art"] = True
    return out


def _decode_utf16(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    # Encoding 1 should carry a BOM; little-endian is the common file without one.
    return data.decode("utf-16-le", "replace")


def _decode_id3_string(raw: bytes) -> str:
    """ID3 text: byte 0 is the encoding (latin-1, UTF-16+BOM, UTF-16BE, UTF-8)."""
    if not raw:
        return ""
    enc, data = raw[0], raw[1:]
    try:
        if enc == 0:
            text = data.split(b"\x00", 1)[0].decode("latin-1", "replace")
        elif enc == 1:
            text = _decode_utf16(data)
        elif enc == 2:
            text = data.decode("utf-16-be", "replace")
        elif enc == 3:
            text = data.split(b"\x00", 1)[0].decode("utf-8", "replace")
        else:
            text = data.split(b"\x00", 1)[0].decode("latin-1", "replace")
    except Exception:
        return ""
    return text.split("\x00", 1)[0].strip()


def _frame_bytes(payload: bytes, version: int, flags: bytes) -> Optional[bytes]:
    """Strip ID3 frame flag prefixes. Compressed or encrypted frames are skipped."""
    if len(flags) < 2:
        return payload
    i = 0
    if version >= 4:
        status = flags[1]
        if status & 0x0C:  # compression or encryption
            return None
        if status & 0x40 and len(payload) > i:
            i += 1
        if status & 0x01:
            i += 4
        data = payload[i:]
        if status & 0x02:
            data = data.replace(b"\xff\x00", b"\xff")
        return data
    status = flags[1]
    if status & 0xC0:  # v2.3 compression or encryption
        return None
    if status & 0x20 and payload:
        i = 1
    return payload[i:]


def _apply_id3_text(fid: bytes, text: str, meta: Dict[str, Any]) -> None:
    if not text:
        return
    if fid in (b"TRCK", b"TPOS"):
        _fill(meta, "track_no" if fid == b"TRCK" else "disc_no", _leading_int(text))
        return
    key = _ID3_TEXT.get(fid)
    if key == "year":
        _fill(meta, "year", _year_of(text))
    elif key:
        _fill(meta, key, text)


def _apic_image(payload: bytes, version: int, flags: bytes) -> Optional[bytes]:
    data = _frame_bytes(payload, version, flags)
    if not data or len(data) < 4:
        return None
    enc = data[0]
    mime_end = data.find(b"\x00", 1)
    if mime_end < 0:
        return None
    desc = mime_end + 2  # skip the NUL and the picture-type byte
    if desc > len(data):
        return None
    if enc in (0, 3):
        end = data.find(b"\x00", desc)
        img = data[end + 1:] if end >= 0 else b""
    else:
        # UTF-16 description, terminated by a 16-bit NUL aligned to its start.
        img = b""
        i = desc
        while i + 1 < len(data):
            if data[i:i + 2] == b"\x00\x00":
                img = data[i + 2:]
                break
            i += 2
    return img or None


def _id3_frames(body: bytes, version: int, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    meta: Dict[str, Any] = {}
    art: Optional[bytes] = None
    i, n = 0, len(body)
    while i + 10 <= n and body[i] != 0:
        fid = body[i:i + 4]
        if any(b < 32 or b > 126 for b in fid):
            break
        fsize = _synchsafe(body[i + 4:i + 8]) if version >= 4 else int.from_bytes(body[i + 4:i + 8], "big")
        flags = body[i + 8:i + 10]
        i += 10
        if fsize < 0 or i + fsize > n:
            break
        payload = body[i:i + fsize]
        i += fsize
        if fid == b"APIC":
            meta["_has_art"] = True
            if want_image and art is None and len(payload) <= _MAX_ART:
                art = _apic_image(payload, version, flags)
            continue
        if fsize > _MAX_TEXT or fid not in _ID3_TEXT and fid not in (b"TRCK", b"TPOS"):
            continue
        raw = _frame_bytes(payload, version, flags)
        if raw is not None:
            _apply_id3_text(fid, _decode_id3_string(raw), meta)
    return meta, art


def _id3v2(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes], int]:
    """Parse a leading ID3v2.3/2.4 tag. Returns (meta, image or None, bytes to skip)."""
    start = f.tell()
    hdr = f.read(10)
    if len(hdr) < 10 or hdr[:3] != b"ID3" or hdr[3] not in (2, 3, 4):
        f.seek(start)
        return {}, None, 0
    version, flags = hdr[3], hdr[5]
    tag_size = _synchsafe(hdr[6:10])
    body_end = start + 10 + tag_size
    footer = 10 if version == 4 and flags & 0x10 else 0
    consumed = 10 + tag_size + footer
    # v2.2 uses 6-byte frames. Skip the tag so the audio header is still found.
    if version == 2:
        f.seek(start + consumed)
        return {}, None, consumed
    if flags & 0x40:
        raw = f.read(4)
        ext = _synchsafe(raw) if version >= 4 else int.from_bytes(raw or b"\x00\x00\x00\x00", "big")
        # v2.4's size counts its own 4 bytes; v2.3's does not.
        skip = ext - 4 if version >= 4 else ext
        if skip > 0:
            f.seek(skip, os.SEEK_CUR)
    if flags & 0x80:
        if tag_size > _MAX_TAG_BODY:
            f.seek(start + consumed)
            return {}, None, consumed
        body = f.read(max(0, body_end - f.tell())).replace(b"\xff\x00", b"\xff")
        meta, art = _id3_frames(body, version, want_image)
        f.seek(start + consumed)
        return meta, art, consumed
    meta: Dict[str, Any] = {}
    art: Optional[bytes] = None
    while f.tell() + 10 <= body_end:
        fh = f.read(10)
        if len(fh) < 10 or fh[0] == 0:
            break
        fid = fh[:4]
        if any(b < 32 or b > 126 for b in fid):
            break
        fsize = _synchsafe(fh[4:8]) if version >= 4 else int.from_bytes(fh[4:8], "big")
        frame_flags = fh[8:10]
        payload_at = f.tell()
        if fsize < 0 or payload_at + fsize > body_end:
            break
        if fid == b"APIC":
            meta["_has_art"] = True
            if want_image and art is None and fsize <= _MAX_ART:
                art = _apic_image(f.read(fsize), version, frame_flags)
        elif fsize <= _MAX_TEXT and (fid in _ID3_TEXT or fid in (b"TRCK", b"TPOS")):
            raw = _frame_bytes(f.read(fsize), version, frame_flags)
            if raw is not None:
                _apply_id3_text(fid, _decode_id3_string(raw), meta)
        f.seek(payload_at + fsize)
    f.seek(start + consumed)
    return meta, art, consumed


def _id3v1(raw: bytes) -> Dict[str, Any]:
    if len(raw) < 128 or not raw.startswith(b"TAG"):
        return {}
    def field(blob: bytes) -> str:
        return blob.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
    meta: Dict[str, Any] = {}
    _fill(meta, "title", field(raw[3:33]))
    _fill(meta, "artist", field(raw[33:63]))
    _fill(meta, "album", field(raw[63:93]))
    _fill(meta, "year", _year_of(field(raw[93:97])))
    comment = raw[97:127]
    if len(comment) >= 30 and comment[28] == 0 and comment[29] != 0:
        _fill(meta, "track_no", int(comment[29]))
    return meta


def _mp3_header(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) < 4 or raw[0] != 0xFF or raw[1] & 0xE0 != 0xE0:
        return None
    ver_bits = (raw[1] >> 3) & 0x3
    layer_bits = (raw[1] >> 1) & 0x3
    if ver_bits == 1 or layer_bits == 0:
        return None
    br_idx = (raw[2] >> 4) & 0xF
    sr_idx = (raw[2] >> 2) & 0x3
    padding = (raw[2] >> 1) & 0x1
    if br_idx in (0, 15) or sr_idx == 3:
        return None
    version = {3: 1, 2: 2, 0: 25}[ver_bits]
    layer = {3: 1, 2: 2, 1: 3}[layer_bits]
    bitrate = _MP3_BITRATE[(1 if version == 1 else 2, layer)][br_idx] * 1000
    rate = _MP3_RATE[version][sr_idx]
    if not bitrate or not rate:
        return None
    if layer == 1:
        frame_len = ((12 * bitrate // rate) + padding) * 4
    elif version == 1 or layer != 3:
        frame_len = (144 * bitrate // rate) + padding
    else:
        frame_len = (72 * bitrate // rate) + padding
    if frame_len < 4:
        return None
    channels = 1 if (raw[3] >> 6) & 0x3 == 3 else 2
    protected = (raw[1] & 0x1) == 0
    side = (32 if channels == 2 else 17) if version == 1 else (17 if channels == 2 else 9)
    return {
        "bitrate": bitrate, "sample_rate": rate, "channels": channels,
        "samples": _MP3_SAMPLES[(version, layer)], "frame_len": frame_len,
        "xing_at": 4 + (2 if protected else 0) + side,
    }


def _xing_frames(frame: bytes, at: int) -> Optional[int]:
    if at + 8 <= len(frame) and frame[at:at + 4] in (b"Xing", b"Info"):
        flags = int.from_bytes(frame[at + 4:at + 8], "big")
        if flags & 0x1 and at + 12 <= len(frame):
            frames = int.from_bytes(frame[at + 8:at + 12], "big")
            return frames or None
    # VBRI sits 32 bytes after the frame header.
    if len(frame) >= 54 and frame[36:40] == b"VBRI":
        frames = int.from_bytes(frame[50:54], "big")
        return frames or None
    return None


def _first_mp3_frame(buf: bytes) -> Optional[Dict[str, Any]]:
    limit = len(buf) - 4
    i = 0
    while i < limit:
        if buf[i] != 0xFF:
            i += 1
            continue
        info = _mp3_header(buf[i:i + 4])
        if not info:
            i += 1
            continue
        nxt = i + info["frame_len"]
        if nxt > len(buf):
            i += 1
            continue
        if nxt + 4 <= len(buf) and _mp3_header(buf[nxt:nxt + 4]) is None:
            i += 1
            continue
        frame = buf[i:nxt]
        info["xing_frames"] = _xing_frames(frame, info["xing_at"])
        return info
    return None


def _parse_mp3(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    f.seek(0)
    meta, art, id3_len = _id3v2(f, want_image)
    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    id3v1 = False
    if file_size >= 128:
        f.seek(file_size - 128)
        tail = f.read(128)
        if tail.startswith(b"TAG"):
            id3v1 = True
            for key, val in _id3v1(tail).items():
                _fill(meta, key, val)
    audio_end = file_size - (128 if id3v1 else 0)
    f.seek(id3_len)
    window = f.read(min(_MP3_SCAN, max(0, audio_end - id3_len)))
    info = _first_mp3_frame(window)
    if info:
        _fill(meta, "sample_rate", info["sample_rate"])
        _fill(meta, "channels", info["channels"])
        _fill(meta, "codec", "mp3")
        frames = info.get("xing_frames")
        if frames and info["sample_rate"]:
            meta["duration"] = frames * info["samples"] / info["sample_rate"]
        elif info["bitrate"]:
            audio_bytes = audio_end - id3_len
            if audio_bytes > 0:
                meta["duration"] = audio_bytes * 8 / info["bitrate"]
        if info["bitrate"]:
            _fill(meta, "bitrate_kbps", info["bitrate"] // 1000)
    return _finish_meta(meta), art


def _vorbis_comments(data: bytes) -> Dict[str, str]:
    if len(data) < 4:
        return {}
    vlen = int.from_bytes(data[0:4], "little")
    pos = 4 + vlen
    if vlen < 0 or pos + 4 > len(data) or pos > len(data):
        return {}
    count = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    out: Dict[str, str] = {}
    for _ in range(min(count, 256)):
        if pos + 4 > len(data):
            break
        clen = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        if clen < 0 or pos + clen > len(data):
            break
        raw = data[pos:pos + clen]
        pos += clen
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", "replace")
        if "=" not in text:
            continue
        key, val = text.split("=", 1)
        key, val = key.strip().upper(), val.strip().strip("\x00")
        if key and val and key not in out:
            out[key] = val
    return out


def _apply_comments(comments: Dict[str, str], meta: Dict[str, Any]) -> None:
    _fill(meta, "title", comments.get("TITLE"))
    _fill(meta, "artist", comments.get("ARTIST"))
    _fill(meta, "album", comments.get("ALBUM"))
    _fill(meta, "album_artist", comments.get("ALBUMARTIST") or comments.get("ALBUM ARTIST"))
    _fill(meta, "genre", comments.get("GENRE"))
    _fill(meta, "track_no", _leading_int(comments.get("TRACKNUMBER")))
    _fill(meta, "disc_no", _leading_int(comments.get("DISCNUMBER")))
    _fill(meta, "year", _year_of(comments.get("DATE") or comments.get("YEAR") or ""))


def _parse_flac(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    if f.read(4) != b"fLaC":
        return {}, None
    meta: Dict[str, Any] = {"codec": "flac"}
    art: Optional[bytes] = None
    for _ in range(64):
        hdr = f.read(4)
        if len(hdr) < 4:
            break
        last = bool(hdr[0] & 0x80)
        kind = hdr[0] & 0x7F
        size = int.from_bytes(hdr[1:4], "big")
        if kind == 0 and size >= 18:
            block = f.read(min(size, 34))
            if len(block) >= 18:
                word = int.from_bytes(block[10:18], "big")
                rate = (word >> 44) & 0xFFFFF
                channels = ((word >> 41) & 0x7) + 1
                samples = word & 0xFFFFFFFFF
                _fill(meta, "sample_rate", rate)
                _fill(meta, "channels", channels)
                if rate and samples:
                    meta["duration"] = samples / rate
            if size > len(block):
                f.seek(size - len(block), os.SEEK_CUR)
        elif kind == 4 and 0 < size <= _MAX_TAG_BODY:
            _apply_comments(_vorbis_comments(f.read(size)), meta)
        elif kind == 6 and size > 0:
            meta["_has_art"] = True
            if want_image and art is None and size <= _MAX_ART:
                art = _flac_picture(f.read(size))
            else:
                f.seek(size, os.SEEK_CUR)
        else:
            f.seek(size, os.SEEK_CUR)
        if last or kind > 126:
            break
    return _finish_meta(meta), art


def _flac_picture(block: bytes) -> Optional[bytes]:
    # type(4) mime_len(4) mime desc_len(4) desc width height depth colors data_len data
    if len(block) < 32:
        return None
    pos = 4
    mime_len = int.from_bytes(block[pos:pos + 4], "big")
    pos += 4 + mime_len
    if mime_len < 0 or pos + 4 > len(block):
        return None
    desc_len = int.from_bytes(block[pos:pos + 4], "big")
    pos += 4 + desc_len + 16
    if desc_len < 0 or pos + 4 > len(block):
        return None
    data_len = int.from_bytes(block[pos:pos + 4], "big")
    pos += 4
    if data_len <= 0 or pos + data_len > len(block):
        return None
    return block[pos:pos + data_len]


def _ogg_pages(buf: bytes) -> list:
    pages = []
    i, n = 0, len(buf)
    while i < n - 27:
        j = buf.find(b"OggS", i)
        if j < 0 or j + 27 > n:
            break
        if buf[j + 4] != 0:
            i = j + 1
            continue
        nseg = buf[j + 26]
        header = 27 + nseg
        if j + header > n:
            break
        segs = list(buf[j + 27:j + header])
        body_len = sum(segs)
        if j + header + body_len > n:
            i = j + 1
            continue
        granule = int.from_bytes(buf[j + 6:j + 14], "little", signed=True)
        pages.append((granule, buf[j + 5], segs, buf[j + header:j + header + body_len]))
        i = j + header + body_len
    return pages


def _ogg_packets(pages: list) -> list:
    packets = []
    cur = b""
    for _gran, _flags, segs, body in pages:
        pos = 0
        for seg in segs:
            cur += body[pos:pos + seg]
            pos += seg
            if seg < 255:
                packets.append(cur)
                cur = b""
    return packets


def _parse_ogg(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    del want_image  # Ogg art lives in later pages; chapters/art stay out of this pass
    f.seek(0, os.SEEK_END)
    size = f.tell()
    if size < 27:
        return {}, None
    f.seek(0)
    head = f.read(min(size, _OGG_HEAD))
    if size > len(head):
        f.seek(max(0, size - _OGG_TAIL))
        tail = f.read(_OGG_TAIL)
    else:
        tail = head
    packets = _ogg_packets(_ogg_pages(head))
    meta: Dict[str, Any] = {}
    codec = None
    rate = 0
    preskip = 0
    if packets:
        first = packets[0]
        if len(first) >= 16 and first[0] == 1 and first[1:7] == b"vorbis":
            codec = "vorbis"
            _fill(meta, "channels", first[11])
            rate = int.from_bytes(first[12:16], "little")
            _fill(meta, "sample_rate", rate)
        elif first.startswith(b"OpusHead") and len(first) >= 16:
            codec = "opus"
            _fill(meta, "channels", first[9])
            preskip = int.from_bytes(first[10:12], "little")
            rate = 48000
            _fill(meta, "sample_rate", rate)
        if codec:
            _fill(meta, "codec", codec)
    for packet in packets[1:4]:
        if packet.startswith(b"\x03vorbis"):
            _apply_comments(_vorbis_comments(packet[7:]), meta)
            break
        if packet.startswith(b"OpusTags"):
            _apply_comments(_vorbis_comments(packet[8:]), meta)
            break
    granule = None
    for gran, _flags, _segs, _body in _ogg_pages(tail):
        if gran >= 0:
            granule = gran
    if codec == "vorbis" and granule and rate:
        meta["duration"] = granule / rate
    elif codec == "opus" and granule is not None:
        samples = granule - preskip
        if samples > 0:
            meta["duration"] = samples / 48000.0
    return _finish_meta(meta), None


def _mp4_mvhd(f, payload: int, box_end: int, meta: Dict[str, Any]) -> None:
    if meta.get("duration") or box_end - payload < 20:
        return
    f.seek(payload)
    version = f.read(1)[0]
    f.read(3)
    if version == 1:
        if box_end - f.tell() < 28:
            return
        f.seek(16, os.SEEK_CUR)
        scale = int.from_bytes(f.read(4), "big")
        dur = int.from_bytes(f.read(8), "big")
    else:
        if box_end - f.tell() < 12:
            return
        f.seek(8, os.SEEK_CUR)
        scale = int.from_bytes(f.read(4), "big")
        dur = int.from_bytes(f.read(4), "big")
    if scale > 0 and dur > 0:
        meta["duration"] = dur / scale


def _mp4_value(f, payload: int, box_end: int, typ: bytes, meta: Dict[str, Any],
               want_image: bool) -> Optional[bytes]:
    art = None
    f.seek(payload)
    while f.tell() + 8 <= box_end:
        pos = f.tell()
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size = int.from_bytes(hdr[:4], "big")
        if size < 8 or pos + size > box_end:
            break
        if hdr[4:8] == b"data" and size >= 16:
            if typ == b"covr" and not want_image:
                meta["_has_art"] = True
                f.seek(pos + size)
                continue
            flags = int.from_bytes(f.read(4), "big") & 0xFFFFFF
            f.read(4)
            left = pos + size - f.tell()
            if typ == b"covr" and left > _MAX_ART:
                # Too big to show: a truncated image is a corrupt one, so skip it.
                meta["_has_art"] = True
                f.seek(pos + size)
                continue
            blob = f.read(min(left, _MAX_ART))
            if typ == b"covr":
                meta["_has_art"] = True
                if want_image and art is None and blob:
                    art = blob
            elif typ in _MP4_NUM and len(blob) >= 4:
                number = int.from_bytes(blob[2:4], "big") if len(blob) >= 6 else int.from_bytes(blob[:2], "big")
                _fill(meta, _MP4_NUM[typ], number or None)
            else:
                key = _MP4_TEXT.get(typ)
                if key:
                    try:
                        if blob.startswith((b"\xff\xfe", b"\xfe\xff")):
                            text = blob.decode("utf-16", "replace")
                        elif flags == 2:
                            text = blob.decode("utf-16-be", "replace")
                        else:
                            text = blob.decode("utf-8", "replace")
                    except Exception:
                        text = ""
                    text = text.strip("\x00").strip()
                    if key == "year":
                        _fill(meta, "year", _year_of(text))
                    else:
                        _fill(meta, key, text)
        f.seek(pos + size)
    return art


def _mp4_walk(f, end: int, meta: Dict[str, Any], want_image: bool, depth: int = 0,
              path: Tuple[bytes, ...] = ()) -> Optional[bytes]:
    art = None
    if depth > 12:
        return None
    while f.tell() + 8 <= end:
        # A crafted file of millions of tiny boxes stalled the scan: real files have
        # a few hundred, so stop counting well past that.
        budget = meta.get("_mp4_boxes", 0) + 1
        meta["_mp4_boxes"] = budget
        if budget > 20000:
            break
        pos = f.tell()
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size = int.from_bytes(hdr[:4], "big")
        typ = hdr[4:8]
        header = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size = int.from_bytes(ext, "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            break
        box_end = pos + size
        if box_end > end:
            break
        payload = pos + header
        try:
            if typ in (b"mvhd", b"mdhd"):
                # mdhd repeats the timescale/duration pair; mvhd is preferred when both exist.
                _mp4_mvhd(f, payload, box_end, meta)
            elif path == _MP4_FILE_ILST and (typ in _MP4_TEXT or typ in _MP4_NUM or typ == b"covr"):
                found = _mp4_value(f, payload, box_end, typ, meta, want_image)
                if found and art is None:
                    art = found
            elif typ == b"meta":
                f.seek(payload + 4)
                found = _mp4_walk(f, box_end, meta, want_image, depth + 1, path + (typ,))
                if found and art is None:
                    art = found
            elif typ in _MP4_CONTAINERS:
                f.seek(payload)
                found = _mp4_walk(f, box_end, meta, want_image, depth + 1, path + (typ,))
                if found and art is None:
                    art = found
        except Exception:
            pass
        f.seek(box_end)
    return art


def _parse_mp4(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    # An MPEG-4 file starts with a box; 'ftyp' usually occupies the first one.
    if size < 8:
        return {}, None
    meta: Dict[str, Any] = {"codec": "mp4"}
    art = _mp4_walk(f, size, meta, want_image)
    return _finish_meta(meta), art


def _parse_wav(f, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    del want_image
    head = f.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return {}, None
    meta: Dict[str, Any] = {"codec": "pcm"}
    rate = channels = byte_rate = 0
    data_size = None
    for _ in range(64):
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid = hdr[:4]
        size = int.from_bytes(hdr[4:8], "little")
        if cid == b"fmt " and size >= 16:
            fmt = f.read(16)
            if len(fmt) >= 16:
                _fmt, channels, rate, byte_rate, _align, _bits = struct.unpack("<HHIIHH", fmt)
                _fill(meta, "channels", channels)
                _fill(meta, "sample_rate", rate)
            rest = size - 16
            if rest > 0:
                f.seek(rest, os.SEEK_CUR)
            if size & 1:
                f.seek(1, os.SEEK_CUR)
        elif cid == b"data":
            data_size = size
            # The size field is the duration. Seeking past the samples does not read them.
            f.seek(size + (size & 1), os.SEEK_CUR)
        else:
            f.seek(size + (size & 1), os.SEEK_CUR)
    if data_size and byte_rate:
        meta["duration"] = data_size / byte_rate
    elif data_size and rate and channels:
        bits = meta.get("bits") or 16
        meta["duration"] = data_size / (rate * channels * (bits / 8))
    if byte_rate:
        _fill(meta, "bitrate_kbps", int(round(byte_rate * 8 / 1000)))
    return _finish_meta(meta), None


_PARSERS = {
    ".mp3": _parse_mp3,
    ".flac": _parse_flac,
    ".m4a": _parse_mp4, ".m4b": _parse_mp4, ".mp4": _parse_mp4,
    ".ogg": _parse_ogg, ".oga": _parse_ogg, ".opus": _parse_ogg,
    ".wav": _parse_wav,
}


def _read_container(path: str, want_image: bool) -> Tuple[Dict[str, Any], Optional[bytes]]:
    parser = _PARSERS.get(Path(path).suffix.lower())
    if parser is None:
        return {}, None
    try:
        with open(path, "rb") as fh:
            try:
                meta, art = parser(fh, want_image)
            except Exception:
                return {}, None
    except OSError:
        return {}, None
    if not isinstance(meta, dict):
        return {}, None
    return meta, art if isinstance(art, (bytes, bytearray)) else None


def _probe_stdlib(path: str) -> Dict[str, Any]:
    """Tags and duration with no ffprobe and no mutagen. Never raises."""
    try:
        meta, _art = _read_container(path, False)
    except Exception:
        return {}
    return {k: v for k, v in meta.items() if k in _META_KEYS and v is not None}


def _stdlib_has_art(path: str) -> bool:
    try:
        meta, _art = _read_container(path, False)
    except Exception:
        return False
    return bool(meta.get("_has_art"))


def _stdlib_cover(path: str) -> Optional[bytes]:
    try:
        _meta, art = _read_container(path, True)
    except Exception:
        return None
    return bytes(art) if art else None
