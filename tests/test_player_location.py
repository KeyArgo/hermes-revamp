"""Player location (docked / floating / mini): server command + agent tool."""

from __future__ import annotations

import json

import pytest

from tests.test_revamp import _load_tools


@pytest.mark.parametrize("loc", ["docked", "floating", "mini"])
def test_player_location_command_accepted(server, loc):
    st, _, res = server.client.post("/api/command", {"action": "player_location", "location": loc})
    assert st == 200 and res["ok"] is True, res


def test_agent_tool_moves_the_player(server):
    plug = _load_tools(server)
    assert "location" in plug.PLAYER_ACTIONS
    props = plug.SCHEMA_PLAYER["parameters"]["properties"]
    assert props["location"]["enum"] == ["docked", "floating", "mini"]
    out = json.loads(plug._tool_player({"action": "location", "location": "floating"}))
    assert out["ok"] is True
    assert json.loads(plug._tool_player({"action": "location", "location": "sideways"}))["ok"] is False
    assert json.loads(plug._tool_player({"action": "location"}))["ok"] is False


def test_player_location_is_broadcast_on_sse(server):
    import threading
    import time

    events: list = []
    t = threading.Thread(target=lambda: events.extend(server.sse_lines(seconds=4.0, want=1)), daemon=True)
    t.start()
    time.sleep(0.6)
    st, _, res = server.client.post("/api/command", {"action": "player_location", "location": "mini"})
    t.join(timeout=6)
    assert st == 200 and res["delivered"] >= 1, res
    cmds = [data for ev, data in events if ev == "command"]
    assert any(c.get("action") == "player_location" and c.get("location") == "mini" for c in cmds), cmds
