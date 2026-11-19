"""Tests for revamp_core.diaglog: the private rotating log and the health sampler."""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from revamp_core import diaglog


@pytest.fixture()
def isolated_diaglog(tmp_path, monkeypatch):
    """A throwaway data dir and a clean diaglog state (handler, sampler, history)."""
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REVAMP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("REVAMP_TRACEMALLOC", raising=False)
    logger = logging.getLogger("revamp")
    old_level = logger.level
    # Retire a sampler an earlier test (ensure_server) may have started.
    if diaglog._sampler_thread is not None and diaglog._sampler_thread.is_alive():
        diaglog._stop.set()
        diaglog._sampler_thread.join(3)
    for h in list(logger.handlers):
        if isinstance(h, diaglog._PrivateRotatingFileHandler):
            logger.removeHandler(h)
            h.close()
    monkeypatch.setattr(diaglog, "_handler", None)
    monkeypatch.setattr(diaglog, "_sampler_thread", None)
    monkeypatch.setattr(diaglog, "_last", None)
    diaglog._history.clear()
    diaglog._stop.clear()
    yield tmp_path / "data"
    diaglog._stop.set()
    if diaglog._sampler_thread is not None:
        diaglog._sampler_thread.join(3)
    for h in list(logger.handlers):
        if isinstance(h, diaglog._PrivateRotatingFileHandler):
            logger.removeHandler(h)
            h.close()
    logger.setLevel(old_level)


def _file_handlers():
    return [h for h in logging.getLogger("revamp").handlers
            if isinstance(h, diaglog._PrivateRotatingFileHandler)]


def test_logger_writes_to_the_file(isolated_diaglog):
    diaglog.setup()
    logging.getLogger("revamp").info("diaglog probe %s", 42)
    for h in _file_handlers():
        h.flush()
    path = isolated_diaglog / "logs" / "revamp.log"
    assert path.exists()
    assert "diaglog probe 42" in path.read_text(encoding="utf-8")
    assert diaglog.log_path() == path


def test_rotation_config(isolated_diaglog):
    diaglog.setup()
    handlers = _file_handlers()
    assert len(handlers) == 1
    h = handlers[0]
    assert isinstance(h, logging.handlers.RotatingFileHandler)
    assert h.maxBytes == 1024 * 1024
    assert h.backupCount == 3


@pytest.mark.skipif(os.name != "posix", reason="posix file modes only")
def test_file_mode_0600_even_after_rollover(isolated_diaglog):
    diaglog.setup()
    path = isolated_diaglog / "logs" / "revamp.log"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    h = _file_handlers()[0]
    h.doRollover()  # the reopened file must stay owner-only too
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_setup_is_idempotent(isolated_diaglog):
    diaglog.setup()
    diaglog.setup()
    diaglog.setup()
    assert len(_file_handlers()) == 1


def test_setup_never_raises_on_a_broken_data_dir(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not a directory")
    monkeypatch.setenv("REVAMP_HOME", str(blocker / "sub"))
    logger = diaglog.setup()  # must not raise
    assert logger.name == "revamp"


def test_env_level(isolated_diaglog, monkeypatch):
    monkeypatch.setenv("REVAMP_LOG_LEVEL", "DEBUG")
    diaglog.setup()
    assert logging.getLogger("revamp").level == logging.DEBUG
    assert _file_handlers()[0].level == logging.DEBUG


def test_sample_has_the_keys(isolated_diaglog):
    s = diaglog.sample()
    for key in ("rss_mb", "peak_rss_mb", "threads", "open_fds",
                "sse_clients", "gc_counts", "uptime_s"):
        assert key in s, key
    assert isinstance(s["threads"], int) and s["threads"] >= 1
    assert isinstance(s["gc_counts"], list) and len(s["gc_counts"]) == 3
    assert s["uptime_s"] >= 0
    if os.path.exists("/proc/self/status"):  # Linux: RSS must be readable
        assert s["rss_mb"] is not None and s["rss_mb"] > 0
        assert s["open_fds"] is not None and s["open_fds"] > 0


def test_warning_on_faked_rss_jump():
    now = time.monotonic()
    history: list = [(now - 300, 100.0)]  # 100 MB five minutes ago
    s = {"rss_mb": 250.0, "threads": 5, "open_fds": 10}
    warns = diaglog._warnings_for(s, history, now=now)
    assert len(warns) == 1
    assert "RSS" in warns[0] and "150" in warns[0]
    # A small growth, or no history yet, is quiet.
    assert diaglog._warnings_for({"rss_mb": 150.0}, history, now=now) == []
    assert diaglog._warnings_for(s, [], now=now) == []


def test_warning_on_thread_and_fd_limits():
    s = {"rss_mb": 10.0, "threads": 201, "open_fds": 1001}
    warns = diaglog._warnings_for(s, [], now=time.monotonic())
    assert any("threads" in w for w in warns)
    assert any("file descriptors" in w for w in warns)
    quiet = {"rss_mb": 10.0, "threads": 200, "open_fds": 1000}
    assert diaglog._warnings_for(quiet, [], now=time.monotonic()) == []


def test_sampler_thread_is_daemon_and_started_once(isolated_diaglog):
    diaglog.start_sampler()
    threads = [t for t in threading.enumerate() if t.name == "revamp-health"]
    assert len(threads) == 1
    assert threads[0].daemon is True
    assert threads[0].is_alive()
    diaglog.start_sampler()  # idempotent: no second thread
    assert len([t for t in threading.enumerate() if t.name == "revamp-health"]) == 1


def test_tick_logs_one_line_and_updates_latest(isolated_diaglog):
    diaglog.setup()
    diaglog._tick()
    for h in _file_handlers():
        h.flush()
    text = (isolated_diaglog / "logs" / "revamp.log").read_text(encoding="utf-8")
    health_lines = [ln for ln in text.splitlines() if "revamp: health" in ln]
    assert len(health_lines) == 1
    assert "rss_mb=" in health_lines[0] and "uptime_s=" in health_lines[0]
    assert diaglog.latest() is not None
    latest = diaglog.latest()
    assert latest is not None and "rss_mb" in latest


def test_diagnostics_route(server):
    code, _, body = server.client.get("/api/diagnostics")
    assert code == 200
    assert body["log_file"].endswith(os.path.join("logs", "revamp.log"))
    for key in ("rss_mb", "peak_rss_mb", "threads", "open_fds",
                "sse_clients", "gc_counts", "uptime_s"):
        assert key in body["sample"], key
    # Token-protected like every other /api route.
    code, _, _ = server.client.get("/api/diagnostics", headers={"X-Revamp-Token": "wrong"})
    assert code == 401


def test_now_json_failure_warns_once(monkeypatch, caplog):
    from revamp_core import store

    def broken_path():
        raise OSError("disk full (fake)")

    monkeypatch.setattr(store, "_now_path", broken_path)
    monkeypatch.setattr(store, "_now_written", (None, 0.0))
    monkeypatch.setattr(store, "_now_write_warned", False)
    with caplog.at_level(logging.WARNING, logger="revamp.store"):
        store._publish_now({"now_playing": {"title": "a"}})
        store._publish_now({"now_playing": {"title": "b"}})  # a second beat
    warnings = [r for r in caplog.records
                if r.name == "revamp.store" and "now.json" in r.getMessage()]
    assert len(warnings) == 1
