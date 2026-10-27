"""Bug hunt 2026-10-24: hidden folders, search folding, the docked pane's name."""

from __future__ import annotations

from pathlib import Path

from revamp_core import library
from revamp_core.library import _matches

PLUGIN = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"


def test_scanner_skips_hidden_files_and_folders(tmp_path):
    (tmp_path / ".hermes" / "tools").mkdir(parents=True)
    (tmp_path / ".hermes" / "tools" / "voice.wav").write_bytes(b"x")
    (tmp_path / ".hidden.mp3").write_bytes(b"x")
    (tmp_path / "Album").mkdir()
    (tmp_path / "Album" / "song.mp3").write_bytes(b"x")
    out: list = []
    library._walk(tmp_path, set(), out)
    assert [Path(p).name for p in out] == ["song.mp3"]


def test_search_ignores_accents_and_keeps_word_starts():
    assert _matches({"artist": "Björk"}, ["bjork"])
    assert _matches({"artist": "Bjork"}, ["björk"])
    assert _matches({"title": "Test Tone"}, ["tone"]) and not _matches({"title": "Blackstone"}, ["tone"])
    assert _matches({"artist": "AC/DC"}, ["ac/dc"])


def test_non_latin_titles_are_findable():
    assert _matches({"title": "Кино — Группа крови"}, ["группа"])
    assert not _matches({"title": "Кино"}, ["группа"])
    assert _matches({"title": "千と千尋の神隠し"}, ["千尋"])  # no spaces between words: substring
    assert _matches({"title": "Ελλάδα"}, ["ελλαδα"])


def test_punctuation_only_search_is_the_same_as_an_empty_one():
    assert _matches({"title": "Anything"}, ["-"]) == _matches({"title": "Anything"}, [])


def test_docked_pane_has_a_real_name():
    src = PLUGIN.read_text(encoding="utf-8")
    assert "(spike)" not in src and "title: 'REVamp Player'" in src


def test_liked_editor_never_drops_likes_it_did_not_show(server):
    """The Liked list only shows liked songs the library knows; during a rescan that is a partial list.
    Saving an edit from it once wiped every hidden like."""
    c = server.client
    ids = [t["id"] for t in c.get("/api/library/tracks?limit=3")[2]["tracks"]]
    hidden = ["not-in-library-1", "not-in-library-2"]
    from revamp_core import store
    for x in ids:
        c.post("/api/likes", {"track_id": x})
    store.update({"likes": store.likes() + hidden}, _internal=True)
    # the editor showed only the 3 known songs; the user removed the first and reordered the rest
    st, _, out = c.put("/api/likes", {"ids": [ids[2], ids[1]], "shown": ids})
    assert st == 200
    assert out["ids"][:2] == [ids[2], ids[1]] and ids[0] not in out["ids"]
    assert set(hidden) <= set(out["ids"]), "likes the editor never showed were removed"
    # an older page (no "shown") can reorder but never removes
    st, _, out = c.put("/api/likes", {"ids": [ids[1]]})
    assert set(hidden) | {ids[1], ids[2]} <= set(out["ids"])
