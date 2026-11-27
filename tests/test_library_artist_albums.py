"""An artist row and the albums it opens agree.

The Artists list counts albums by a track's own artist. Clicking the row lists
that artist's albums. A track on a compilation ("Various Artists") and a track
with no album tag both used to be counted on the row but missing (or uncounted)
once it was opened.
"""

from __future__ import annotations

import pytest

from revamp_core import library

TRACKS = [
    {"id": "dp", "title": "One More Time", "artist": "Daft Punk",
     "album_artist": "Various Artists", "album": "Hits", "path": "/m/1.mp3"},
    {"id": "moby", "title": "Porcelain", "artist": "Moby", "album": "", "path": "/m/2.mp3"},
    {"id": "own", "title": "Own record", "artist": "Moby", "album_artist": "Moby",
     "album": "Play", "path": "/m/3.mp3"},
]


@pytest.fixture()
def tracks(monkeypatch):
    monkeypatch.setattr(library, "all_tracks", lambda: [dict(t) for t in TRACKS])
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "kind_of", lambda *_args: "music")


def _row(name):
    return next(r for r in library.artists() if r["name"] == name)


def test_an_artist_only_on_a_compilation_opens_that_compilation(tracks):
    cards = library.albums(artist="Daft Punk")
    assert [(a["album"], a["artist"]) for a in cards] == [("Hits", "Various Artists")]
    assert len(_row("Daft Punk")["albums"]) == len(cards)


def test_an_untagged_album_is_counted_like_the_card_it_opens(tracks):
    cards = library.albums(artist="Moby")
    assert sorted(a["album"] for a in cards) == ["Play", "Unknown Album"]
    assert len(_row("Moby")["albums"]) == len(cards)
