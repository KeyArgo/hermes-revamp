"""MilkDrop visualizer: the server accepts the command that shows/hides it."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize("cmd", [
    {"action": "visualizer"},
    {"action": "visualizer", "open": True},
    {"action": "visualizer", "open": False},
])
def test_visualizer_command_accepted(server, cmd):
    st, _, res = server.client.post("/api/command", cmd)
    assert st == 200 and res["ok"] is True, res


def test_docked_milkdrop_has_its_own_preference_menu_and_plain_language_toggle():
    player = Path("web/player.js").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    assert 'visualizerPlacement === "docked") return "milkdropOpenDocked"' in player
    assert "function installMilkdropMenuKeyboardAccess()" in app
    assert 'document.querySelector("#main-window #option")' in app
    assert 'e.key.toLowerCase() !== "m"' in app
    assert 'id = "docked-milkdrop"' not in app
    assert 'id = "float-milkdrop"' not in app
    extras = Path("web/views/npextras.js").read_text(encoding="utf-8")
    # The classic menu remains available, but full-page users should not need to
    # know Winamp conventions to find MilkDrop.
    assert 'className = "btn sm np-visualizer"' in extras
    assert 'textContent = "Visualizer"' in extras
    assert 'api.toggleVisualizer?.()' in extras
    assert 'api.visualizerOpen?.()' in extras
    css = Path("web/app.css").read_text(encoding="utf-8")
    assert "float-milkdrop" not in css
    assert "body.mode-winamp #webamp-host { min-height: 0; background: transparent !important; }" in css


def test_approval_ducking_only_targets_actionable_desktop_prompts():
    player = Path("web/player.js").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    assert 'saved.settings.duck_approval_scope === "all" ? "all" : "app"' in player
    assert 'ev?.approval_surface === "cli"' in player
    assert 'approvalSession(ev)' in player
    assert 'unduck(null, approvalSession(ev))' in player
    assert 'setApprovalWaiting(actionable, actionable ? duckReason(ev) : "")' in player
    assert 'export function setApprovalWaiting(on, reason = "")' in app


def test_fullscreen_milkdrop_draws_at_screen_size_on_a_gpu():
    # User report 11-02 ("I see shit graphics" in the pop-out): REVamp made MilkDrop's window
    # element full screen itself, so Webamp kept drawing its ~256x314 picture and the browser
    # stretched it ~7x. Webamp's own full screen (SET_MILKDROP_FULLSCREEN) draws at screen size.
    # Under software WebGL the cheap stretched picture stays (every pixel costs CPU there).
    text = (Path(__file__).resolve().parents[1] / "web" / "player.js").read_text(encoding="utf-8")
    fs = text[text.index("api.fullscreenVisualizer = () => {"):text.index('document.addEventListener("dblclick"')]
    gpu = fs[fs.index("if (!softwareGL) {"):fs.index("// Software WebGL")]
    assert 'dispatchStore({ type: "SET_MILKDROP_FULLSCREEN", enabled: !on });' in gpu
    assert "requestFullscreen" not in gpu
    assert "win.requestFullscreen" in fs[fs.index("// Software WebGL"):]
