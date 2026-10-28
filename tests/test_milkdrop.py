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


def test_docked_milkdrop_has_its_own_preference_and_uses_main_menu():
    player = Path("web/player.js").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    assert 'visualizerPlacement === "docked") return "milkdropOpenDocked"' in player
    assert "function installMilkdropMenuKeyboardAccess()" in app
    assert 'document.querySelector("#main-window #option")' in app
    assert 'e.key.toLowerCase() !== "m"' in app
    assert 'id = "docked-milkdrop"' not in app
    assert 'id = "float-milkdrop"' not in app
    assert "np-visualizer" not in Path("web/views/npextras.js").read_text(encoding="utf-8")
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
