"""MilkDrop visualizer: the server accepts the command that shows/hides it."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("cmd", [
    {"action": "visualizer"},
    {"action": "visualizer", "open": True},
    {"action": "visualizer", "open": False},
])
def test_visualizer_command_accepted(server, cmd):
    st, _, res = server.client.post("/api/command", cmd)
    assert st == 200 and res["ok"] is True, res
