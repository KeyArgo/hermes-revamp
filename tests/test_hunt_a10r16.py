"""Round 16 fixes: the Windows port lock, the bind loser, and the self-restart's working folder.

Each test names the reviewer and what a person would have hit.
"""
import logging
import os
import shutil
import sys

from revamp_core import server


def _reset_server_globals(monkeypatch):
    server._watch_gen += 1
    for name, val in (("_started", False), ("_watching", False), ("_owned", False)):
        monkeypatch.setattr(server, name, val)


class _FakeMsvcrt:
    """msvcrt refuses to lock a byte of an empty file (errno 13), exactly like the real one."""
    LK_LOCK = 2

    def __init__(self):
        self.calls = []

    def locking(self, fd, mode, count):
        size = os.fstat(fd).st_size
        self.calls.append((size, mode, count))
        if size < 1:
            raise OSError(13, "Permission denied")


def test_the_windows_lock_file_is_never_empty(monkeypatch, tmp_path):
    # Grok a10r16 (high): the lock file was created empty and msvcrt cannot lock a byte of it, so
    # two profiles passed the port check together and the loser showed the winner's library.
    fake = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setitem(sys.modules, "fcntl", None)  # ImportError, like Windows
    path = tmp_path / "port.lock"
    with open(path, "a+b") as fh:
        server._lock_fh(fh)
    assert fake.calls == [(1, fake.LK_LOCK, 1)], "the byte msvcrt locks must exist before locking"
    assert path.stat().st_size == 1


def test_a_lock_that_cannot_be_taken_is_reported_not_passed_off_as_held(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(server, "_lease_path", lambda port: tmp_path / f"{port}.lease")

    def refuse(fh):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(server, "_lock_fh", refuse)
    entered = []
    with caplog.at_level(logging.ERROR, logger=server.logger.name):
        with server._port_lock(18131):
            entered.append(1)
    assert entered == [1], "an unlocked decision still has to be made (best effort)"
    assert "could not serialise the port decision" in caplog.text


def test_the_bind_loser_decides_again_instead_of_showing_the_winners_library(monkeypatch, caplog):
    # Grok a10r16: the losing profile logged "cannot bind", returned None and its page then showed
    # whatever the winner served (often an empty library).
    _reset_server_globals(monkeypatch)
    monkeypatch.setattr(server, "get_token", lambda: "t")
    monkeypatch.setattr(server, "_foreign_holder", lambda port: False)
    monkeypatch.setattr(server, "_may_take_port", lambda port, was_ours: True)
    monkeypatch.setattr(server, "_watch_owner", lambda port, **kw: None)
    health = {"app": "revamp", "owner": "plugin", "version": server.VERSION, "build": "b", "instance": "X"}
    probes = iter([None, health])
    monkeypatch.setattr(server, "_probe_health", lambda port: next(probes, health))
    monkeypatch.setattr(server.proof, "listener_identity", lambda base, token, timeout=2.0: False)
    calls = []

    def build(port, **kw):
        calls.append(port)
        if len(calls) == 1:
            raise OSError(98, "Address already in use")
        raise AssertionError("the second attempt must not bind over the winner")

    monkeypatch.setattr(server, "build_server", build)
    # The retry takes the port lock again; entering it while still inside would deadlock
    # (flock is not re-entrant), which is what codex a10r17 caught.
    import contextlib
    depth = {"n": 0}

    @contextlib.contextmanager
    def guard(port):
        assert depth["n"] == 0, "the port lock was re-entered: this deadlocks on a real flock"
        depth["n"] += 1
        try:
            yield
        finally:
            depth["n"] -= 1

    monkeypatch.setattr(server, "_port_lock", guard)
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        code = server.ensure_server(18132, owner="plugin", version=server.VERSION, build="b")
    assert code == 18132, "the loser keeps serving on the port it decided on (never None)"
    assert len(calls) == 1, "decided again by the ownership rules instead of binding"
    assert depth["n"] == 0, "the lock was left before deciding again"
    assert "cannot bind" not in caplog.text


def test_the_restart_re_enters_the_install_folder_by_name(tmp_path):
    # Claude a10r16 (medium): Hermes swaps the plugin folder on update. A process whose working
    # folder follows the old inode sits in a folder that is renamed away and then deleted, so
    # `python -m revamp_core.server` finds no package and the service dies on restart.
    root = tmp_path / "plugins" / "revamp"
    root.mkdir(parents=True)
    (root / "marker.txt").write_text("old")
    here = os.getcwd()
    try:
        os.chdir(root)
        start_dir = os.getcwd()
        staged = tmp_path / "staged"
        staged.mkdir()
        (staged / "marker.txt").write_text("new")
        aside = tmp_path / "revamp.previous-abc"
        os.replace(root, aside)   # what Hermes does: old copy aside, new copy at the old name
        os.replace(staged, root)
        shutil.rmtree(aside)      # ...then the old copy is deleted
        server._reenter_install_dir(start_dir)
        assert open("marker.txt").read() == "new", "the working folder is the swapped-in copy"
        assert os.path.realpath(os.getcwd()) == os.path.realpath(root)
    finally:
        os.chdir(here)


def test_re_entering_the_same_folder_does_nothing(tmp_path):
    # The restart runs in the normal case too: it must not move a healthy process anywhere.
    here = os.getcwd()
    server._reenter_install_dir(here)
    assert os.getcwd() == here
    server._reenter_install_dir("")  # nothing recorded: no change, no error


def test_the_self_restart_remembers_the_folder_it_started_in(monkeypatch):
    import threading as _t
    monkeypatch.setattr(server, "UPDATE_CHECK", 0.01)
    monkeypatch.setattr(server, "VERSION", "1.0.0-alpha.9")
    monkeypatch.setattr(server, "_installed_version", lambda: "1.0.0-alpha.10")
    monkeypatch.setattr(server, "_quiet_for_restart", lambda: True)
    seen = {}
    monkeypatch.setattr(server, "_reenter_install_dir", lambda d: seen.setdefault("dir", d))
    done = _t.Event()

    def fake_exec(exe, argv):
        done.set()
        raise SystemExit  # end the thread instead of replacing the test process

    monkeypatch.setattr(server.os, "execv", fake_exec)
    started_in = os.getcwd()
    server._restart_when_updated()
    assert done.wait(3)
    assert seen["dir"] == started_in, "the path name, recorded before any swap"


def test_giving_the_port_away_leaves_a_watcher_behind(monkeypatch):
    # Claude a10r19: _shutdown_for_upgrade closed the socket but left _started/_owned set and
    # started no watcher, so when the new owner later stopped nothing served the port again.
    server._watch_gen += 1
    monkeypatch.setattr(server, "_started", True)
    monkeypatch.setattr(server, "_owned", True)
    watched = []
    monkeypatch.setattr(server, "_watch_owner", lambda port, **kw: watched.append((port, kw)))
    monkeypatch.setattr(server, "_lock", __import__("threading").RLock())

    class Fake:
        revamp_owner = "plugin"
        server_address = ("127.0.0.1", 18999)

        def shutdown(self):
            pass

        def server_close(self):
            pass

    server._shutdown_for_upgrade(Fake())
    assert server._started is False and server._owned is False
    assert watched == [(18999, {"owner": "plugin", "ours": True, "as_owner": "plugin"})]
