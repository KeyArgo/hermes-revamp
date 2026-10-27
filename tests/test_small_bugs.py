"""Small backlog bugs: import name limit, non-finite numbers in state."""

from __future__ import annotations

import pytest


def test_import_rejects_a_too_long_name(server):
    c = server.client
    st, _, _ = c.post("/api/playlists/import", {"name": "x" * 201, "format": "m3u", "text": "#EXTM3U\nhttp://a/b.mp3\n"})
    assert st == 400
    st, _, out = c.post("/api/playlists/import", {"name": "x" * 200, "format": "m3u", "text": "#EXTM3U\nhttp://a/b.mp3\n"})
    assert st == 201, out


@pytest.mark.parametrize("partial", [{"volume": float("inf")}, {"balance": float("-inf")}, {"volume": float("nan")},
                                     {"resume": {"t1": float("inf")}}, {"settings": {"bookRate": float("nan")}},
                                     {"playlist": [{"duration": float("inf")}]}])
def test_state_rejects_non_finite_numbers_anywhere(server, partial):
    c = server.client
    before = c.get("/api/state")[2]
    st, _, _ = c.put("/api/state", partial)
    assert st == 400
    assert c.get("/api/state")[2].get("volume") == before.get("volume")


def test_state_still_accepts_normal_numbers(server):
    st, _, out = server.client.put("/api/state", {"volume": 55, "resume": {"t1": 12.5}})
    assert st == 200 and out["volume"] == 55
