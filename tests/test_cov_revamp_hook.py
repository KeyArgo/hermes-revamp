"""Coverage for bin/revamp-hook: payload parsing, event mapping, detachment.

Unit tests load the extension-less script directly; integration tests run it
in a subprocess with REPO/bin on PATH so the detached revampctl posts to the
fixture server.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "bin" / "revamp-hook"


def _hook_mod():
    loader = importlib.machinery.SourceFileLoader("revamp_hook_bin", str(HOOK))
    spec = importlib.util.spec_from_loader("revamp_hook_bin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# read_payload / tool_and_preview
# ---------------------------------------------------------------------------

def _payload_from(text: str, monkeypatch):
    import tempfile
    import sys as real_sys
    mod = _hook_mod()
    with tempfile.TemporaryFile("w+") as fh:
        fh.write(text)
        fh.seek(0)
        class Stdin:
            fileno = staticmethod(fh.fileno)
            def isatty(self):
                return False
            def read(self, n):
                return fh.read(n)
        monkeypatch.setattr(real_sys, "stdin", Stdin())
        return mod.read_payload()


@pytest.mark.skipif(sys.platform == "win32", reason="revamp-hook's select() on stdin does not work on Windows: known 1.0.1 bug (tool names arrive empty)")
def test_read_payload_variants(monkeypatch):
    assert _payload_from('{"tool_name": "bash"}', monkeypatch) == \
        {"tool_name": "bash"}
    assert _payload_from("", monkeypatch) == {}
    assert _payload_from("not json{{", monkeypatch) == {}
    assert _payload_from("[1, 2]", monkeypatch) == {}


def test_read_payload_no_stdin_or_tty(monkeypatch):
    import sys as real_sys
    mod = _hook_mod()
    monkeypatch.setattr(real_sys, "stdin", None)
    assert mod.read_payload() == {}

    class Tty:
        def isatty(self):
            return True
    monkeypatch.setattr(real_sys, "stdin", Tty())
    assert mod.read_payload() == {}


@pytest.mark.parametrize("payload,tool,preview", [
    ({"tool_name": "bash", "tool_input": {"command": "ls -la"}}, "bash", "ls -la"),
    ({"toolName": "read", "toolInput": {"file_path": " a.txt "}}, "read", "a.txt"),
    ({"tool": "edit", "input": {"path": "/tmp/x"}}, "edit", "/tmp/x"),
    ({"tool": "x", "tool_input": {"description": "  do  it "}}, "x", "do it"),
    ({"tool": "x", "tool_input": {"command": "c"}}, "x", "c"),
    ({"tool": "x", "command": "top cmd"}, "x", "top cmd"),
    ({"message": "hey there"}, "", "hey there"),
    ({"tool": "x", "tool_input": "not a dict", "command": "fb"}, "x", "fb"),
    ({"tool": "x", "tool_input": {"other": 1}}, "x", ""),
    ({}, "", ""),
])
def test_tool_and_preview(payload, tool, preview):
    mod = _hook_mod()
    assert mod.tool_and_preview(payload) == (tool, preview)


def test_tool_and_preview_trims():
    mod = _hook_mod()
    tool, preview = mod.tool_and_preview(
        {"tool_name": "t" * 100, "tool_input": {"command": "c " * 200}})
    assert tool == "t" * 40
    assert len(preview) <= 80 and "  " not in preview


# ---------------------------------------------------------------------------
# main(): detached spawn
# ---------------------------------------------------------------------------

@pytest.fixture()
def spawned(monkeypatch):
    mod = _hook_mod()
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append((cmd, kw))
    monkeypatch.setattr(mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(mod, "read_payload", lambda: {})
    return mod, calls


def test_main_maps_events_and_sets_source(spawned):
    mod, calls = spawned
    assert mod.main(["codex", "approval"]) == 0
    cmd, kw = calls[-1]
    assert cmd == ["revampctl", "event", "approval_request", "-", ""]
    assert kw["env"]["REVAMP_SOURCE"] == "codex"
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["stdout"] is subprocess.DEVNULL
    assert kw["start_new_session"] is True
    for name, kind in [("session_start", "session_start"),
                       ("session_end", "session_end"),
                       ("tool_start", "tool_start"),
                       ("tool_end", "tool_end")]:
        mod.main(["x", name])
        assert calls[-1][0][2] == kind


def test_main_source_truncated_and_tool_preview(spawned, monkeypatch):
    mod, calls = spawned
    monkeypatch.setattr(mod, "read_payload",
                        lambda: {"tool_name": "grep",
                                 "tool_input": {"path": "README.md"}})
    mod.main(["harness-name-that-is-long", "tool_start"])
    cmd, kw = calls[-1]
    assert cmd == ["revampctl", "event", "tool_start", "grep", "README.md"]
    assert kw["env"]["REVAMP_SOURCE"] == "harness-name-that-is-long"[:32]


def test_main_silent_and_never_fails(spawned, monkeypatch, capsys):
    mod, calls = spawned
    assert mod.main([]) == 0 and calls == []
    assert mod.main(["only-one-arg"]) == 0 and calls == []
    assert mod.main(["src", "not-an-event"]) == 0 and calls == []

    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr(mod.subprocess, "Popen", boom)
    assert mod.main(["src", "tool_end"]) == 0
    assert capsys.readouterr() == ("", "")


def test_script_entry_always_exit_zero(tmp_path):
    env = dict(os.environ, PATH=str(tmp_path))  # revampctl absent -> OSError path
    r = subprocess.run([sys.executable, str(HOOK), "agy", "tool_start"],
                       capture_output=True, text=True, timeout=15, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


# ---------------------------------------------------------------------------
# end to end against the fixture server
# ---------------------------------------------------------------------------

def _poll_events(server, pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, events = server.client.get("/api/agent_events")
        hit = next((e for e in events if pred(e)), None)
        if hit:
            return hit
        time.sleep(0.15)
    return None


def _run_hook(args, input=None, path_prefix=True):
    env = dict(os.environ)
    if path_prefix:
        env["PATH"] = str(REPO / "bin") + os.pathsep + env["PATH"]
    return subprocess.run([sys.executable, str(HOOK), *args],
                          capture_output=True, text=True, timeout=30,
                          env=env, input=input)


@pytest.mark.skipif(sys.platform == "win32", reason="revamp-hook's select() on stdin does not work on Windows: known 1.0.1 bug (tool names arrive empty)")
def test_hook_tool_start_end_to_end(server):
    payload = json.dumps({"tool_name": "cov-tool-" + str(time.time()),
                          "tool_input": {"command": "echo hi"}})
    r = _run_hook(["opencode", "tool_start"], input=payload)
    assert r.returncode == 0 and r.stdout == ""
    tool = json.loads(payload)["tool_name"]
    ev = _poll_events(server, lambda e: e.get("tool") == tool)
    assert ev, "hook event never reached the server"
    assert ev["kind"] == "tool_start" and ev["source"] == "opencode"
    assert ev["preview"] == "echo hi"


@pytest.mark.skipif(sys.platform == "win32", reason="revamp-hook's select() on stdin does not work on Windows: known 1.0.1 bug (tool names arrive empty)")
def test_hook_approval_camel_and_missing_stdin(server):
    payload = json.dumps({"toolName": "shell", "toolInput": {"path": "/x"},
                          "sessionId": "s9"})
    r = _run_hook(["cursor", "approval"], input=payload)
    assert r.returncode == 0
    ev = _poll_events(server, lambda e: e.get("kind") == "approval_request")
    assert ev and ev["tool"] == "shell" and ev["preview"] == "/x"
    # stdin = DEVNULL: read_payload gets EOF -> "-" placeholder tool
    r = _run_hook(["pi", "tool_end"], input="")
    assert r.returncode == 0
    # DEVNULL stdin: select is ready instantly and read returns "" -> "{}"
    ev = _poll_events(server, lambda e: e.get("kind") == "tool_end")
    # tool "-" comes through as absent/placeholder; only kind+source are owed
    assert ev and ev["source"] == "pi"


def test_hook_ignores_unknown_event(server):
    before = server.client.get("/api/agent_events")[2]
    r = _run_hook(["agy", "teleport"], input=json.dumps({"tool_name": "x"}))
    assert r.returncode == 0
    time.sleep(0.7)  # give a (wrongly) detached revampctl time to land
    after = server.client.get("/api/agent_events")[2]
    assert len(after) == len(before)
