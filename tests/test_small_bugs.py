"""Small backlog bugs: import name limit, non-finite numbers in state."""

from __future__ import annotations

import pytest


def test_import_rejects_a_too_long_name(server):
    c = server.client
    st, _, _ = c.post("/api/playlists/import", {"name": "x" * 201, "format": "m3u", "text": "#EXTM3U\nhttp://a/b.mp3\n"})
    assert st == 400
    st, _, out = c.post("/api/playlists/import", {"name": "x" * 200, "format": "m3u", "text": "#EXTM3U\nhttp://a/b.mp3\n"})
    assert st == 201, out


def test_a_long_playlist_name_keeps_a_short_file_name(server):
    """A 200-character name made a 200-character file name; with the save's temp prefix the path
    passed Windows' 260-character limit and the create/import answered 500."""
    from revamp_core import playlists
    c = server.client
    long_name = "My favourite late night driving songs for the long road home " * 4
    long_name = long_name[:200]
    st, _, out = c.post("/api/playlists", {"name": long_name, "items": []})
    assert st in (200, 201), out
    assert len(out["id"]) <= playlists.SLUG_MAX
    assert out["name"] == long_name.strip()                     # the name itself is kept whole
    st, _, again = c.post("/api/playlists", {"name": long_name, "items": []})
    assert st in (200, 201) and again["id"] != out["id"]        # same name, its own file
    st, _, got = c.get(f"/api/playlists/{out['id']}")
    assert st == 200 and got["name"] == long_name.strip()


def test_a_playlist_saved_with_an_old_long_file_name_still_opens(server):
    """Files written before the cap keep their long names; they still open by id and by name."""
    import json
    from revamp_core import playlists
    # Just over the cap: long enough to differ from today's slug, short enough that the test's own
    # deep temp path stays under Windows' limit.
    name = "an old playlist whose file was named in full before the cap came in, many years ago"
    slug = "-".join(name.replace(",", "").split())   # what slugify returned before the cap
    assert len(slug) > playlists.SLUG_MAX
    (playlists.playlists_dir() / f"{slug}.m3u8").write_text(
        f"#EXTM3U\n#EXTREVAMP-NAME:{json.dumps(name)}\n", encoding="utf-8")
    assert playlists.get_playlist(slug)["id"] == slug
    assert playlists.get_playlist(name)["id"] == slug


@pytest.mark.parametrize("name,expected", [
    ("Rock!", "rock"), ("", "playlist"), ("x" * 200, "x" * 80),
    ("word " * 40, ("word-" * 16).rstrip("-")),
])
def test_slugify_is_bounded_and_ends_on_a_word(name, expected):
    from revamp_core import playlists
    assert playlists.slugify(name) == expected


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
