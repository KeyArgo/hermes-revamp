"""Audiobook chapter lists via ffprobe (m4b and friends).

Pure stdlib: shells out to ``ffprobe -show_chapters`` (20 s timeout) and
normalises the result. Never raises; caches per (realpath, size, mtime).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from typing import Any, Dict, List

FFPROBE_TIMEOUT = 20.0
MAX_CACHE = 512

_CACHE: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
# key -> threading.Event for in-flight probes (concurrent-miss dedup)
_INFLIGHT: Dict[tuple, threading.Event] = {}


def _normalise(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_chapters = raw.get("chapters") if isinstance(raw, dict) else None
    if not isinstance(raw_chapters, list):
        return []
    parsed: List[Dict[str, Any]] = []
    for ch in raw_chapters:
        if not isinstance(ch, dict):
            continue
        try:
            start = round(float(ch.get("start_time", 0) or 0), 3)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = round(float(ch.get("end_time", 0) or 0), 3)
        except (TypeError, ValueError):
            end = start
        tags = ch.get("tags")
        if tags is not None and not isinstance(tags, dict):
            continue
        title = ""
        raw_title = tags.get("title") or tags.get("TITLE") if tags else None
        if isinstance(raw_title, str):
            title = raw_title.strip()
        parsed.append({"title": title, "start": start, "end": end})
    parsed.sort(key=lambda c: c["start"])
    chapters: List[Dict[str, Any]] = []
    for i, ch in enumerate(parsed):
        title = ch["title"] or f"Chapter {i + 1}"
        chapters.append({"index": i, "title": title,
                         "start": ch["start"], "end": ch["end"]})
    return chapters


def chapters_for(path: str) -> List[Dict[str, Any]]:
    """Chapter list for *path*, ``[]`` when there are none / anything fails."""
    try:
        real = os.path.realpath(os.path.abspath(path))
        st = os.stat(real)
        key = (real, st.st_size, st.st_mtime)
    except (TypeError, ValueError, OSError):
        return []
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
        done = _INFLIGHT.get(key)
        if done is None:
            done = threading.Event()
            _INFLIGHT[key] = done
            leader = True
        else:
            leader = False
    if not leader:
        done.wait(timeout=FFPROBE_TIMEOUT + 5)
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        return hit if hit is not None else []
    chapters: List[Dict[str, Any]] = []
    probed = False  # only a probe that ran is worth remembering; a failure is retried later
    try:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            chapters = []
        else:
            cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
                   "-show_chapters", real]
            try:
                proc = subprocess.run(cmd, capture_output=True,
                                      timeout=FFPROBE_TIMEOUT, check=False)
            except (OSError, ValueError, subprocess.SubprocessError):
                proc = None
            if proc is None or proc.returncode != 0:
                chapters = []
            else:
                try:
                    data = json.loads(proc.stdout or b"{}")
                except (TypeError, ValueError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                chapters = _normalise(data)
                probed = True
    finally:
        with _CACHE_LOCK:
            if probed:
                _CACHE[key] = chapters
                _CACHE.move_to_end(key)
                while len(_CACHE) > MAX_CACHE:
                    _CACHE.popitem(last=False)
            evt = _INFLIGHT.pop(key, None)
        if evt is not None:
            evt.set()
    return chapters
