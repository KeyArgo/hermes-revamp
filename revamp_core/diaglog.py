"""Diagnostics for Revamp: a private rotating log and a cheap health sampler.

The "revamp" logger writes to ``<data dir>/logs/revamp.log`` (1 MB x 3 files,
owner-only). Setup is idempotent, never raises, and never touches the root
logger or the host's (Hermes's) handlers.

The sampler is one daemon thread, started once by the server start path. Every
60 s it logs ONE line (RSS, peak RSS, threads, open fds, SSE clients, gc counts,
uptime) and warns on a >100 MB RSS jump within 10 minutes, >200 threads or
>1000 open fds. It costs ~nothing: no tracemalloc unless REVAMP_TRACEMALLOC=1,
in which case a warning also lists the top 10 allocation sites.
"""

from __future__ import annotations

import gc
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import data_dir

LOG_NAME = "revamp.log"
MAX_BYTES = 1024 * 1024  # 1 MB per file
BACKUP_COUNT = 3
INTERVAL = 60.0  # seconds between health samples
WINDOW = 600.0  # the RSS-growth window (10 minutes)
RSS_JUMP_MB = 100.0
THREAD_LIMIT = 200
FD_LIMIT = 1000

_T0 = time.monotonic()  # process uptime baseline (module loads with the server)
_lock = threading.Lock()
_handler: Optional[logging.Handler] = None
_sampler_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_last: Optional[Dict[str, Any]] = None
_history: List[Tuple[float, Optional[float]]] = []  # (monotonic, rss_mb), pruned to WINDOW


def log_path() -> Path:
    return data_dir() / "logs" / LOG_NAME


def _level() -> int:
    name = (os.environ.get("REVAMP_LOG_LEVEL") or "INFO").strip().upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.INFO


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that keeps the file owner-only (0600), also after a
    rollover: doRollover() reopens through _open(), so one override covers both."""

    def _open(self):  # type: ignore[override]
        stream = super()._open()
        try:
            if os.name == "posix":
                os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def setup() -> logging.Logger:
    """Attach the rotating file handler to the "revamp" logger (idempotent).

    Never raises: diagnostics must not keep the player from playing. The root
    logger and any handlers the host installed are left exactly as they were.
    """
    global _handler
    logger = logging.getLogger("revamp")
    try:
        level = _level()
        logger.setLevel(level)
        if _handler is not None and _handler in logger.handlers:
            _handler.setLevel(level)
            return logger
        # A previous setup's file handler (e.g. another data dir in tests) is
        # replaced, never doubled. Only our own handler type is touched.
        for old in list(logger.handlers):
            if isinstance(old, _PrivateRotatingFileHandler):
                logger.removeHandler(old)
                try:
                    old.close()
                except Exception:
                    pass
        folder = log_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "posix":
                os.chmod(folder, 0o700)
        except OSError:
            pass
        handler = _PrivateRotatingFileHandler(
            str(log_path()), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
            encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        _handler = handler
    except Exception:
        pass  # a broken log must never break the server
    return logger


# Health sampling

def _rss_mb() -> Tuple[Optional[float], Optional[float]]:
    """(rss, peak) in MB. /proc/self/status on Linux; getrusage elsewhere."""
    try:
        if sys.platform.startswith("linux"):
            vals: Dict[str, float] = {}
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    key, raw = line.split(":", 1)
                    vals[key] = float(raw.split()[0]) / 1024.0  # kB -> MB
            if "VmRSS" in vals:
                return vals["VmRSS"], vals.get("VmHWM")
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource
        peak_kb_or_b = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kB, macOS bytes; anything else is treated as kB.
        peak = peak_kb_or_b / (1024.0 * 1024.0) if sys.platform == "darwin" \
            else peak_kb_or_b / 1024.0
        return peak, peak  # rusage has no current RSS: the peak is the best we have
    except Exception:
        return None, None


def sample() -> Dict[str, Any]:
    """One health sample. Every read is best-effort: a missing /proc (non-Linux)
    leaves that field None instead of failing the whole sample."""
    rss, peak = _rss_mb()
    fds: Optional[int] = None
    try:
        fds = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    sse: Optional[int] = None
    try:
        from . import server as _srv  # late import: server imports this module
        sse = _srv.HUB.count()
    except Exception:
        pass
    return {
        "ts": time.time(),
        "uptime_s": round(time.monotonic() - _T0, 1),
        "rss_mb": round(rss, 1) if rss is not None else None,
        "peak_rss_mb": round(peak, 1) if peak is not None else None,
        "threads": threading.active_count(),
        "open_fds": fds,
        "sse_clients": sse,
        "gc_counts": list(gc.get_count()),
    }


def _warnings_for(s: Dict[str, Any], history: List[Tuple[float, Optional[float]]],
                  now: Optional[float] = None) -> List[str]:
    """Warning lines for one sample against the (already pruned) history.
    Pure, so tests can fake an RSS jump without waiting ten minutes."""
    out: List[str] = []
    now = time.monotonic() if now is None else now
    rss = s.get("rss_mb")
    if rss is not None and history:
        base_t, base_rss = history[0]  # oldest sample still inside the window
        if base_rss is not None and rss - base_rss > RSS_JUMP_MB:
            out.append("revamp: RSS grew %.0f MB in %.0f s (%.0f -> %.0f MB)"
                       % (rss - base_rss, now - base_t, base_rss, rss))
    threads = s.get("threads") or 0
    if threads > THREAD_LIMIT:
        out.append("revamp: %s threads open (limit %s)" % (threads, THREAD_LIMIT))
    fds = s.get("open_fds")
    if fds is not None and fds > FD_LIMIT:
        out.append("revamp: %s open file descriptors (limit %s)" % (fds, FD_LIMIT))
    return out


def _tracemalloc_sites() -> str:
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return ""
        top = tracemalloc.take_snapshot().statistics("lineno")[:10]
        return "; ".join("%.1f MB at %s" % (st.size / (1024.0 * 1024.0), st.traceback)
                         for st in top)
    except Exception:
        return ""


def _tick() -> None:
    global _last
    s = sample()
    now = time.monotonic()
    _history[:] = [(t, r) for (t, r) in _history if now - t <= WINDOW]
    warns = _warnings_for(s, _history)
    _history.append((now, s.get("rss_mb")))
    _last = s
    logger = logging.getLogger("revamp")
    logger.info(
        "revamp: health rss_mb=%s peak_rss_mb=%s threads=%s open_fds=%s "
        "sse_clients=%s gc=%s uptime_s=%s",
        s["rss_mb"], s["peak_rss_mb"], s["threads"], s["open_fds"],
        s["sse_clients"], s["gc_counts"], s["uptime_s"])
    for msg in warns:
        logger.warning("%s", msg)
    if warns:
        sites = _tracemalloc_sites()
        if sites:
            logger.warning("revamp: top allocation sites: %s", sites)


def _run() -> None:
    # Daemon: dies with the process. A failed tick is logged, never fatal.
    while not _stop.wait(INTERVAL):
        try:
            _tick()
        except Exception:
            try:
                logging.getLogger("revamp").warning(
                    "revamp: health sampler tick failed", exc_info=True)
            except Exception:
                pass


def start_sampler() -> None:
    """Start the one health-sampler thread (idempotent, never raises)."""
    global _sampler_thread
    try:
        setup()
        with _lock:
            if _sampler_thread is not None and _sampler_thread.is_alive():
                return
            if (os.environ.get("REVAMP_TRACEMALLOC") or "").strip() == "1":
                import tracemalloc
                if not tracemalloc.is_tracing():
                    tracemalloc.start()
            _stop.clear()
            t = threading.Thread(target=_run, name="revamp-health", daemon=True)
            _sampler_thread = t
            t.start()
    except Exception:
        pass


def latest() -> Optional[Dict[str, Any]]:
    """The most recent sample, or None before the first tick."""
    return _last


def stop() -> None:
    """Stop the health sampler and close the private log handler.

    Called when this process gives the port away (an upgrade hand-over) but stays
    alive as the port watcher: the sampler would keep writing lines for a server
    that no longer serves, and a second rotator on the same file would rename one
    this process still holds open. A later ``setup()``/``start_sampler()`` (the
    watcher taking the port back) reattaches both. Never raises.
    """
    global _handler, _sampler_thread
    try:
        _stop.set()
        t = _sampler_thread
        _sampler_thread = None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(3)
    except Exception:
        pass
    try:
        with _lock:
            h, _handler = _handler, None
        if h is not None:
            try:
                logging.getLogger("revamp").removeHandler(h)
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass
    except Exception:
        pass
