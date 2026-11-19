"""Swallowed errors reach the private REVamp log instead of vanishing."""

from __future__ import annotations

import logging
import time

from revamp_core import library, tags


def _wait_for(predicate, timeout=2.0):
    """Give the background scan thread a bounded chance to log."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_library_scan_warns_when_the_scan_raises(monkeypatch, caplog):
    """A failed background scan leaves a warning with the traceback."""

    def boom(roots=None, gen=None):
        raise RuntimeError("scan exploded")

    gens = []
    real_reserve = library._reserve

    def reserve():
        gen = real_reserve()
        gens.append(gen)
        return gen

    monkeypatch.setattr(library, "scan_sync", boom)
    monkeypatch.setattr(library, "_reserve", reserve)
    try:
        with caplog.at_level(logging.WARNING, logger="revamp"):
            library.scan_async(["/nonexistent/revamp-log-test"])
            found = _wait_for(
                lambda: any(
                    r.name == "revamp" and r.levelno == logging.WARNING
                    and r.getMessage() == "library scan failed"
                    for r in caplog.records
                )
            )
        assert found, "a failed library scan must leave a warning, not vanish"
        record = next(r for r in caplog.records if r.getMessage() == "library scan failed")
        assert record.exc_info[1].args == ("scan exploded",)
    finally:
        if gens:
            library._finish(gens[0])


def test_per_file_tag_failure_logs_at_debug(monkeypatch, caplog):
    """One unreadable file leaves a debug line naming the file, not a warning."""
    def explode(*args, **kwargs):
        raise RuntimeError("bad audio file")

    broken = type("M", (), {"File": staticmethod(explode)})
    monkeypatch.setattr(tags, "mutagen", broken, raising=False)
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", True)

    with caplog.at_level(logging.DEBUG, logger="revamp"):
        assert tags._tags_mutagen("/music/broken.mp3") == {}

    records = [
        r for r in caplog.records
        if r.name == "revamp" and r.levelno == logging.DEBUG
        and "could not read tags" in r.getMessage()
        and "/music/broken.mp3" in r.getMessage()
    ]
    assert records, "a bad file must leave a debug line naming the file"
    assert any(r.exc_info for r in records), "the debug line must carry the traceback"
