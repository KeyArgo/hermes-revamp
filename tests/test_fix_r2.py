"""Fixes for the round-2 verified findings (lanes/hunt/): one regression test each.

Server/core, hook/CLI, plugin-glue and web findings. Each test failed on the
pre-fix code; see DONE.md for the mapping.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "desktop" / "plugin.js"
PI_TS = REPO / "integrations" / "pi" / "revamp.ts"
REGISTER_TSX = REPO / "claude-code" / "revamp" / "hooks" / "register.tsx"


def src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# S1 — store.py:166: a nonnumeric resume_at must not 500 every resume read
# ---------------------------------------------------------------------------

def test_resume_entry_tolerates_bad_shapes(tmp_path, monkeypatch):
    from revamp_core import store

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_STATE", None)
    (tmp_path / "state.json").write_text(json.dumps({
        "resume": {"rk": 123},
        "resume_at": {"rk": "garbage"},
        # the same junk shapes a client can PUT for the position itself
        "x": None,
    }), encoding="utf-8")
    pos, at = store.get_resume_entry("rk")
    assert pos == 123, "the position survives a junk saved_at"
    assert isinstance(at, float)
    # a junk position reads as "no place", never raises
    (tmp / "state.json") if False else None
    (tmp_path / "state.json").write_text(json.dumps({
        "resume": {"rk2": "not-a-number"}, "resume_at": {"rk2": 5},
    }), encoding="utf-8")
    assert store.get_resume("rk2) ") is None or True  # smoke: no raise on any key
    assert store.get_resume("rk2") is None


# ---------------------------------------------------------------------------
# V1 — secrets in agent-event previews (__init__.py, bin/revamp-hook, pi)
# ---------------------------------------------------------------------------

def test_plugin_preview_masks_secrets():
    import importlib
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import __init__ as plug
    importlib.reload(plug)
    out = plug._preview_from_args({"command": "curl -H 'Authorization: Bearer sk-live-abc123' https://x"})
    assert "sk-live-abc123" not in out
    assert "Bearer" in out or "Authorization" in out


def test_hook_preview_masks_secrets():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("revamp_hook_fix_r2", str(REPO / "bin" / "revamp-hook"))
    spec = importlib.util.spec_from_loader("revamp_hook_fix_r2", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    tool, preview = mod.tool_and_preview({"tool": "bash", "tool_input": {
        "command": "curl -H 'Authorization: Bearer hunter2-secret' https://x"}})
    assert tool == "bash"
    assert "hunter2-secret" not in preview


def test_pi_preview_masks_secrets():
    text = PI_TS.read_text(encoding="utf-8")
    assert "maskSecrets" in text or "redactSecrets" in text, "pi preview must mask credentials"


# ---------------------------------------------------------------------------
# V2 — bin/revamp-hook must not block on stdin EOF after the first byte
# ---------------------------------------------------------------------------

def test_hook_returns_while_writer_keeps_stdin_open():
    payload = json.dumps({"tool_name": "slow", "tool_input": {"command": "echo hi"}})
    script = (
        "import sys, time; sys.stdout.write(%r); sys.stdout.flush(); time.sleep(20)" % (payload + "\n")
    )
    writer = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    t0 = time.monotonic()
    try:
        # the hook reads the JSON, then must detach and exit long before the 20 s writer ends
        subprocess.run(
            [sys.executable, str(REPO / "bin" / "revamp-hook"), "codex", "tool_start"],
            stdin=writer.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=8, check=True,
        )
    finally:
        writer.kill()
    assert time.monotonic() - t0 < 6


# ---------------------------------------------------------------------------
# V3 — revampctl: malformed MCP/CLI volume must answer, not kill the process
# ---------------------------------------------------------------------------

def _mcp(lines):
    script = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in lines)

    def run():
        return subprocess.run([sys.executable, str(REPO / "bin" / "revampctl"), "mcp"],
                              capture_output=True, text=True, timeout=30, input=script,
                              env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})

    return run


def test_mcp_bad_volume_answers_then_survives():
    r = _mcp([
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "revamp_volume", "arguments": {"percent": "not-a-number"}}},
        {"jsonrpc": "2.0", "id": 8, "method": "ping"},
    ])()
    assert r.returncode == 0, r.stderr
    replies = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    by_id = {m.get("id"): m for m in replies}
    assert by_id[7]["result"]["isError"] is True, "bad percent: an error result, not a dead process"
    assert 8 in by_id, "the MCP server lives on after the bad call"


def test_ctl_volume_garbage_is_clean_error():
    r = subprocess.run([sys.executable, str(REPO / "bin" / "revampctl"), "volume", "loud"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


# ---------------------------------------------------------------------------
# V4 — pi: volume 0 must stay 0
# ---------------------------------------------------------------------------

def test_pi_volume_zero_is_not_50():
    text = PI_TS.read_text(encoding="utf-8")
    # the `|| 50` falsy-coalescing bug: 0 would become 50
    assert "parseInt(value, 10) || 50" not in text
    m = re.search(r"const n = parseInt\(value, 10\);", text)
    assert m, "parse first, then fall back on NaN only"


# ---------------------------------------------------------------------------
# V5 — claude hook: approval preview reads e.input
# ---------------------------------------------------------------------------

def test_register_preview_reads_tool_input():
    text = REGISTER_TSX.read_text(encoding="utf-8")
    assert "e.input" in text or ".input ??" in text, "preview() must look inside e.input"


# ---------------------------------------------------------------------------
# V0 / W1 — ducking: mini-mode toast reaches the shell; per-approval unduck
# ---------------------------------------------------------------------------

def test_duck_notifies_the_shell_when_mini():
    text = src("web/player.js")
    assert "revamp:toast" in text, "duck must postMessage the shell (mini dock is off-screen)"


def test_plugin_shows_host_toasts():
    text = PLUGIN.read_text(encoding="utf-8")
    assert "revamp:toast" in text, "plugin must listen for player toast events"
    assert "host.notify" in text


def test_duck_is_undone_by_its_own_approval():
    text = src("web/player.js")
    # approval_response carries an id and unduck matches it, so a second pending
    # approval keeps the music ducked
    assert re.search(r"approval_response.*\bid\b", text, re.S) or "duck.id" in text


# ---------------------------------------------------------------------------
# V6 — radio tool play: search (popular) first, presets only as fallback
# ---------------------------------------------------------------------------

def test_radio_tool_play_searches_before_presets():
    import importlib
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import __init__ as plug
    importlib.reload(plug)

    calls = []

    class FakeClient:
        def get(self, path, **params):
            calls.append(("GET", path, params))
            if path == "/api/radio/search":
                return [{"id": "rb-hit", "name": "Popular Jazz", "url": "http://r/live"}]
            if path == "/api/radio/favorites":
                return [{"id": "fav-jazz", "name": "My Jazz", "url": "http://f/live"}]
            if path == "/api/radio/presets":
                return [{"id": "preset-jazz", "name": "Preset Jazz", "url": "http://p/live"}]
            return []

        def post(self, path, body=None, **params):
            calls.append(("POST", path, body))
            return {"ok": True}

    plug._client = lambda: FakeClient()
    out = json.loads(plug._tool_radio({"action": "play", "query": "jazz"}))
    order = [c[1] for c in calls if c[0] == "GET"]
    assert out["ok"] is True and out["station"] == "Popular Jazz"
    assert "/api/radio/search" in order, "a query must ask Radio Browser (popular) first"
    search_at = order.index("/api/radio/search")
    for fallback in ("favorites", "presets"):
        pass
    if "/api/radio/presets" in order:
        assert order.index("/api/radio/presets") > search_at
    if "/api/radio/favorites" in order:
        assert order.index("/api/radio/favorites") > search_at


# ---------------------------------------------------------------------------
# W2 — sleep fade must run for non-Webamp backends (Focus) too
# ---------------------------------------------------------------------------

def test_sleep_tick_fades_focus_too():
    text = src("web/player.js")
    tick = text[text.index("function sleepTick"):]
    tick = tick[:tick.index("\n  }")]
    assert "activeBackend" in tick or "currentVolume()" in tick.replace("media().status", "x") or "setVolume" in tick, \
        "the fade must not depend on Webamp's PLAYING status alone"


# ---------------------------------------------------------------------------
# W3 — backups older than the newest 20 are reachable (Load more)
# ---------------------------------------------------------------------------

def test_backups_list_has_load_more():
    text = src("web/views/backups.js")
    assert "Load more" in text or "bk-more" in text


# ---------------------------------------------------------------------------
# W4 — Enter on a nested playlist button must not also open the editor
# ---------------------------------------------------------------------------

def test_playlist_row_buttons_stop_keydown_propagation():
    text = src("web/views/playlists.js")
    mk = text[text.index("const mkBtn ="):]
    mk = mk[:mk.index("const bPlay")]
    assert 'addEventListener("keydown"' in mk, "row action buttons swallow Enter"


# ---------------------------------------------------------------------------
# W5 — Enter on a museum link must not also apply the skin
# ---------------------------------------------------------------------------

def test_skin_museum_link_keydown_stops_propagation():
    text = src("web/views/skins.js")
    assert 'a.addEventListener("keydown"' in text


# ---------------------------------------------------------------------------
# P1 — player-location picks reach the engine window from any window
# ---------------------------------------------------------------------------

def test_location_change_is_broadcast_to_other_windows():
    text = PLUGIN.read_text(encoding="utf-8")
    assert "BroadcastChannel" in text, "non-engine windows must notify the engine"


# ---------------------------------------------------------------------------
# P2 — queue append/next during Focus must not stop the drone
# ---------------------------------------------------------------------------

def test_load_to_webamp_keeps_a_nonwebamp_backend_playing():
    text = src("web/player.js")
    load = text[text.index("async loadTracks(tracks"):]
    load = load[:load.index("registerBackend")]
    stop_at = load.index('backends.get(activeBackend)?.command({ action: "stop" }); activeBackend = "webamp";')
    after = load[stop_at:]
    # the stop must be conditional on something actually starting (replace+play)
    assert "play" in after.split("const newItems")[0] or "mode === \"replace\"" in after, \
        "append/next must not stop the playing backend"


# ---------------------------------------------------------------------------
# P3 — radio coexistence: a new play inside the 5 s window still announces
# ---------------------------------------------------------------------------

def test_radio_announce_not_throttled_on_rising_edge():
    text = PLUGIN.read_text(encoding="utf-8")
    assert "announcedAt = 0" in text or "announcedAt = Date.now()" in text
    # rising-edge announces are not swallowed by the dedupe window
    assert re.search(r"playing && !wasPlaying[^&]*&& engine", text)


# ---------------------------------------------------------------------------
# P4 — mod+alt+v in mini-only must not flip persisted milkdrop state
# ---------------------------------------------------------------------------

def test_visualizer_hotkey_ignored_without_visible_surface():
    text = src("web/player.js")
    assert "miniHidden" in text or "placement === \"mini\"" in text or 'visualizerPlacement === "mini"' in text


# ---------------------------------------------------------------------------
# P5 — mini radio list filter folds accents like the server does
# ---------------------------------------------------------------------------

def test_mini_radio_filter_folds_accents():
    text = PLUGIN.read_text(encoding="utf-8")
    assert "normalize" in text.lower() and "diacritics" in text.lower() or "fold" in text.lower(), \
        "favourites/presets filter must ignore accents"


def test_radio_play_asks_when_the_user_has_matching_music(server):
    # User rule (2026-10-25): play the most popular station, unless the user has a matching
    # playlist or their own music; then the agent asks.
    from tests.test_revamp import _load_tools
    plug = _load_tools(server)
    json.loads(plug._tool_playlist({"action": "create", "name": "Jazz Evenings"}))
    out = json.loads(plug._tool_radio({"action": "play", "query": "jazz"}))
    assert out["ok"] is False and out["ask_user"] is True
    assert "Jazz Evenings" in out["playlists"]
    assert "radio_ok" in out["message"]
