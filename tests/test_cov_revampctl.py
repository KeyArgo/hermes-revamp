"""CLI + MCP coverage for bin/revampctl, driven as real subprocesses.

The server fixture boots revamp_core in-process, so monkeypatching
revamp_core.radio also steers the subprocess's HTTP requests.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CTL = REPO / "bin" / "revampctl"


def run_ctl(args, env_extra=None, stdin=None, timeout=30):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CTL), *map(str, args)],
                          capture_output=True, text=True, timeout=timeout, env=env,
                          input=stdin)


def run_ctl_with_sse(server, args):
    """Run the CLI while listening to SSE; returns (result, command cmds)."""
    import threading
    events: list = []

    def run():
        events.extend(server.sse_lines(seconds=6.0, want=1))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.6)  # let the SSE subscription register before the CLI posts
    r = run_ctl(args)
    t.join(timeout=8)
    return r, [d for ev, d in events if ev == "command"]


def _poll_events(server, pred, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, events = server.client.get("/api/agent_events")
        hit = next((e for e in events if pred(e)), None)
        if hit:
            return hit
        time.sleep(0.15)
    return None


# ---------------------------------------------------------------------------
# plain commands
# ---------------------------------------------------------------------------

def test_ctl_usage_and_unknown(server):
    r = run_ctl([])
    assert r.returncode == 2 and "revampctl" in r.stdout.lower()
    r = run_ctl(["frobnicate"])
    assert r.returncode == 2 and "unknown command" in r.stderr


def test_ctl_url_and_state_and_status(server):
    r = run_ctl(["url"])
    assert r.returncode == 0 and r.stdout.strip() == server.base + "/"
    r = run_ctl(["state"])
    assert r.returncode == 0 and "volume" in json.loads(r.stdout)
    r = run_ctl(["status"])
    assert r.stdout.startswith("Revamp: no player open")


def test_ctl_transport_and_volume_deliver(server):
    for cmd in ("play", "pause", "toggle", "stop", "next", "prev"):
        r = run_ctl([cmd])
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith(f"{cmd}: delivered to")
    r = run_ctl(["volume", "42"])
    assert r.returncode == 0 and r.stdout.startswith("volume 42%: delivered to")


def test_ctl_volume_garbage_is_graceful(server):
    r = run_ctl(["volume", "loud"])
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_ctl_search_and_queue(server):
    r = run_ctl(["search", "song"])
    assert r.returncode == 0
    tracks = json.loads(r.stdout)["tracks"]
    assert len(tracks) >= 2
    # The page persists the playlist; the server only broadcasts, so watch SSE.
    r, cmds = run_ctl_with_sse(server, ["queue", "song"])
    assert r.returncode == 0 and r.stdout.startswith("playing 2 track(s), first:")
    load = next(c for c in cmds if c.get("action") == "load")
    assert load["mode"] == "replace" and load["play"] is True
    assert [i["playback"]["url"] for i in load["items"]] == \
        [f"/api/media/{t['id']}" for t in tracks]
    assert load["items"][0]["kind"] == "music"
    r = run_ctl(["queue", "zzz-nothing-here"])
    assert r.returncode == 1 and "no library tracks match" in r.stdout


def test_ctl_radio_preset_and_fallback_and_miss(server, monkeypatch):
    # Offline (no Radio Browser hits): the preset that names the query plays. No real network.
    monkeypatch.setattr("revamp_core.radio.search_radiobrowser", lambda **kw: [])
    r, cmds = run_ctl_with_sse(server, ["radio", "drone"])
    assert r.returncode == 0 and "tuned to SomaFM Drone Zone" in r.stdout
    load = next(c for c in cmds if c.get("action") == "load")
    assert load["items"][0]["kind"] == "stream"
    assert load["items"][0]["playback"]["stream"].startswith("http")
    calls = []

    def fake_search(q="", tag="", limit=30, offset=0):
        calls.append((q, limit))
        return [{"id": "rb1", "name": "Fake Lounge", "url": "http://s/1"}]
    monkeypatch.setattr("revamp_core.radio.search_radiobrowser", fake_search)
    r = run_ctl(["radio", "lounge stuff"])
    assert r.returncode == 0 and "tuned to Fake Lounge" in r.stdout
    assert calls and calls[0][1] == 10
    monkeypatch.setattr("revamp_core.radio.search_radiobrowser",
                        lambda **kw: [])
    r = run_ctl(["radio", "zzz-nothing"])
    assert r.returncode == 1 and "no stations match" in r.stdout


def test_ctl_event_carries_source_and_fields(server):
    tag = "cov-event-unique"
    r = run_ctl(["event", "tool_start", tag, "x" * 200, "1"],
                env_extra={"REVAMP_SOURCE": "codex-test"})
    assert r.returncode == 0
    ev = _poll_events(server, lambda e: e.get("tool") == tag)
    assert ev, "agent event never landed"
    assert ev["kind"] == "tool_start" and ev["source"] == "codex-test"
    assert ev["ok"] is True and ev["preview"] == "x" * 80
    r = run_ctl(["event", "session_start"])  # bare kind, no source env
    assert r.returncode == 0
    ev = _poll_events(server, lambda e: e.get("kind") == "session_start")
    assert ev and "source" not in ev


def test_ctl_unreachable_server_is_exit_3(server):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    r = run_ctl(["state"], env_extra={"REVAMP_PORT": str(dead_port)})
    assert r.returncode == 3 and "not reachable" in r.stderr


def test_ctl_no_token_file_still_works_for_open_routes(tmp_path):
    env = {"REVAMP_HOME": str(tmp_path / "nope")}  # token() -> ""
    r = subprocess.run([sys.executable, str(CTL), "url"],
                       capture_output=True, text=True, timeout=15, env=dict(os.environ, **env))
    assert r.returncode == 0 and r.stdout.strip().endswith("/")


# ---------------------------------------------------------------------------
# MCP server mode
# ---------------------------------------------------------------------------

def _mcp(lines):
    # dicts are sent as JSON; plain strings go out verbatim (garbage lines)
    script = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n"
                     for m in lines)
    return run_ctl(["mcp"], stdin=script)


def _by_id(replies, mid):
    return next(r for r in replies if r.get("id") == mid)


def _replies(stdout):
    return [json.loads(x) for x in stdout.splitlines() if x.strip()]


def test_mcp_initialize_ping_tools_list(server):
    r = _mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": None, "method": "notifications/initialized"},
        "",
        "[]",
        "{not json",
    ])
    assert r.returncode == 0
    got = _replies(r.stdout)
    assert len(got) == 4  # notification, blank line and non-dict get no reply
    init = _by_id(got, 1)
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert init["result"]["serverInfo"]["name"] == "revamp"
    assert _by_id(got, 2) == {"jsonrpc": "2.0", "id": 2, "result": {}}
    names = [t["name"] for t in _by_id(got, 3)["result"]["tools"]]
    assert names == ["revamp_now_playing", "revamp_control", "revamp_volume",
                     "revamp_play_music", "revamp_play_radio"]
    assert _by_id(got, None)["error"]["code"] == -32700


def test_mcp_unknown_method_and_bad_tool(server):
    r = _mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "sudo_rm"}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call"},
    ])
    got = _replies(r.stdout)
    assert _by_id(got, 1)["error"]["code"] == -32601
    call = _by_id(got, 2)["result"]
    assert call["isError"] is True and "unknown tool" in call["content"][0]["text"]
    missing = _by_id(got, 3)["result"]
    assert missing["isError"] is True and "unknown tool 'None'" in \
        missing["content"][0]["text"]


def test_mcp_now_playing_control_volume(server):
    r = _mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "revamp_now_playing"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "revamp_control", "arguments": {"action": "play"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "revamp_control", "arguments": {"action": "rock-and-roll"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "revamp_volume", "arguments": {"percent": 33}}},
    ])
    got = _replies(r.stdout)
    np = _by_id(got, 1)["result"]
    assert np["isError"] is False and "no player open" in np["content"][0]["text"]
    ctl = _by_id(got, 2)["result"]
    assert ctl["isError"] is False and "delivered" in ctl["content"][0]["text"]
    bad = _by_id(got, 3)["result"]
    assert bad["isError"] is True and "action must be" in bad["content"][0]["text"]
    vol = _by_id(got, 4)["result"]
    assert vol["isError"] is False and "volume 33%" in vol["content"][0]["text"]


def test_mcp_volume_garbage_is_error_not_crash(server):
    r = _mcp([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "revamp_volume", "arguments": {"percent": "loud"}}}])
    assert r.returncode == 0
    got = _replies(r.stdout)
    assert _by_id(got, 1)["result"]["isError"] is True


def test_mcp_play_music_and_radio(server, monkeypatch):
    monkeypatch.setattr("revamp_core.radio.search_radiobrowser",
                        lambda **kw: [{"id": "rb1", "name": "Fake Lounge",
                                       "url": "http://s/1"}])
    r = _mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "revamp_play_music",
                    "arguments": {"query": "song"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "revamp_play_music",
                    "arguments": {"query": "zzz-nothing"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "revamp_play_radio",
                    "arguments": {"query": "lounge"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "revamp_play_radio",
                    "arguments": {"query": "drone"}}},
    ])
    got = _replies(r.stdout)
    music = _by_id(got, 1)["result"]
    assert music["isError"] is False and "playing" in music["content"][0]["text"]
    miss = _by_id(got, 2)["result"]
    assert miss["isError"] is True and "no library tracks match" in \
        miss["content"][0]["text"]
    lounge = _by_id(got, 3)["result"]
    assert "tuned to Fake Lounge" in lounge["content"][0]["text"]
    # Search-first: the (stubbed) Radio Browser answer wins for 'drone' too; the
    # preset fallback only runs when the search misses or fails.
    preset = _by_id(got, 4)["result"]
    assert "tuned to Fake Lounge" in preset["content"][0]["text"]


def test_mcp_tool_error_when_server_down():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    r = run_ctl(["mcp"], stdin=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "revamp_now_playing"}}) + "\n",
        env_extra={"REVAMP_PORT": str(dead_port)})
    assert r.returncode == 0
    res = _by_id(_replies(r.stdout), 1)["result"]
    assert res["isError"] is True
    assert "not reachable" in res["content"][0]["text"]
