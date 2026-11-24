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
    monkeypatch.setattr(diaglog, "_client_count", None)
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


def test_stop_ends_the_sampler_and_closes_the_handler(isolated_diaglog):
    """An upgrade hand-over keeps the process alive as the port watcher: it must
    stop writing health lines and let go of the rotating file, so the new server's
    rotator can rename it."""
    diaglog.setup()
    logging.getLogger("revamp").info("before stop")
    diaglog.start_sampler()
    assert [t for t in threading.enumerate() if t.name == "revamp-health"]

    diaglog.stop()

    assert not [t for t in threading.enumerate() if t.name == "revamp-health"]
    assert diaglog._sampler_thread is None
    assert _file_handlers() == []
    logging.getLogger("revamp").info("after stop")
    text = (isolated_diaglog / "logs" / "revamp.log").read_text(encoding="utf-8")
    assert "before stop" in text
    assert "after stop" not in text


def test_setup_after_stop_reattaches_the_log(isolated_diaglog):
    """The watcher may take the port back: setup()/start_sampler() must work again."""
    diaglog.setup()
    diaglog.stop()
    diaglog.setup()
    diaglog.start_sampler()
    assert len(_file_handlers()) == 1
    assert [t for t in threading.enumerate() if t.name == "revamp-health"]
    logging.getLogger("revamp").info("serving again")
    for h in _file_handlers():
        h.flush()
    assert "serving again" in (isolated_diaglog / "logs" / "revamp.log").read_text(encoding="utf-8")


def test_upgrade_shutdown_stops_the_health_sampler_and_the_log(isolated_diaglog, monkeypatch):
    """The process stays alive as the port watcher after giving the port away; its
    sampler and rotator must stop with the server (a second rotator on the same
    file renames one the old process still has open)."""
    from revamp_core import server as srv_mod

    class FakeSrv:
        revamp_owner = "plugin"
        server_address = ("127.0.0.1", 59999)

        def shutdown(self):
            self.stopped = True

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(srv_mod, "_watch_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(srv_mod, "_started", True)
    monkeypatch.setattr(srv_mod, "_owned", True)
    diaglog.setup()
    diaglog.start_sampler()
    fake = FakeSrv()

    srv_mod._shutdown_for_upgrade(fake)

    assert fake.stopped and fake.closed
    assert not [t for t in threading.enumerate() if t.name == "revamp-health"]
    assert _file_handlers() == []


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


def test_standalone_server_samples_its_own_listeners(isolated_diaglog, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    from revamp_core import server

    # Like python -m, execute the server in a second module namespace.
    spec = importlib.util.spec_from_file_location("revamp_core.standalone", server.__file__)
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    monkeypatch.setattr(standalone, "ThreadingHTTPServer", lambda *args: SimpleNamespace())
    standalone.build_server(8114, build="test", owner="service")
    listener = standalone.HUB.register()
    try:
        assert diaglog.sample()["sse_clients"] == 1
    finally:
        standalone.HUB.unregister(listener)
