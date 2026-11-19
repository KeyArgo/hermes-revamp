"""Album groups: an artist filter is an exact album artist, and an album's tracks
are exactly that card's (album, album artist) group.

The lists show a blank artist as "Unknown Artist"; asking for that row, or for
the card, must find those tracks instead of an empty list. A compilation track
that shares the album title but belongs to another album artist stays out.
"""

from __future__ import annotations

import pytest

from revamp_core import library

TRACKS = {
    "queen": {"id": "queen", "title": "Bohemian Rhapsody", "artist": "Queen",
              "album_artist": "Queen", "album": "A Night at the Opera", "path": "/m/1.mp3"},
    "qotsa": {"id": "qotsa", "title": "No One Knows", "artist": "Queens of the Stone Age",
              "album_artist": "Queens of the Stone Age", "album": "Songs for the Deaf",
              "path": "/m/2.mp3"},
    "adele": {"id": "adele", "title": "Someone Like You", "artist": "Adele",
              "album_artist": "Adele", "album": "Gold", "path": "/m/3.mp3"},
    "comp": {"id": "comp", "title": "Compilation cut", "artist": "Adele",
             "album_artist": "Various Artists", "album": "Gold", "path": "/m/4.mp3"},
    "blank": {"id": "blank", "title": "No artist", "artist": "", "album_artist": "",
              "album": "Mystery", "path": "/m/5.mp3"},
    "blank2": {"id": "blank2", "title": "Also no artist", "artist": None,
               "album_artist": None, "album": "Mystery", "path": "/m/6.mp3"},
}


@pytest.fixture()
def tracks(monkeypatch):
    monkeypatch.setattr(library, "all_tracks", lambda: list(TRACKS.values()))
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "kind_of", lambda *_args: "music")
    return TRACKS


def _ids(result):
    return sorted(t["id"] for t in result["tracks"])


def test_artist_albums_are_an_exact_album_artist_match(tracks):
    # "Queen" must not also list "Queens of the Stone Age" (a substring).
    albums = library.albums(artist="Queen")
    assert [a["album"] for a in albums] == ["A Night at the Opera"]
    assert albums[0]["artist"] == "Queen"
    # The same rule is case-insensitive and finds the album through its artist.
    assert [a["album"] for a in library.albums(artist="queen")] == ["A Night at the Opera"]


def test_a_single_letter_artist_does_not_list_everybody(tracks):
    # Every artist name contains "a": an exact match keeps only that artist's albums.
    assert [a["album"] for a in library.albums(artist="Adele")] == ["Gold"]
    assert [a["album"] for a in library.albums(artist="A")] == []


def test_album_tracks_are_exactly_that_cards_group(tracks):
    # Play on "Gold" by Adele must not queue the compilation track that only shares
    # the album title and happens to have Adele as its track artist.
    result = library.search(album="Gold", album_artist="Adele", kind="music")
    assert _ids(result) == ["adele"]
    assert result["total"] == 1
    # The compilation's own card still finds its track.
    assert _ids(library.search(album="Gold", album_artist="Various Artists")) == ["comp"]


def test_a_blank_artist_album_is_found_as_unknown_artist(tracks):
    cards = library.albums()
    card = next(a for a in cards if a["album"] == "Mystery")
    assert card["artist"] == "Unknown Artist" and card["tracks"] == 2
    # Opening the card: the display name the list shows is what the page sends.
    result = library.search(album="Mystery", album_artist="Unknown Artist", kind="music")
    assert _ids(result) == ["blank", "blank2"]
    # The "Unknown Artist" artist row finds the same tracks.
    assert _ids(library.search(artist="Unknown Artist", kind="music")) == ["blank", "blank2"]


def test_the_tracks_route_passes_album_artist_through(server, monkeypatch):
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return {"total": 0, "tracks": []}

    monkeypatch.setattr(library, "search", capture)
    code, _, _ = server.client.get("/api/library/tracks?album=Gold&album_artist=Adele")
    assert code == 200
    assert seen["album"] == "Gold"
    assert seen["album_artist"] == "Adele"
    assert seen["artist"] == ""  # the old parameter still works and stays optional
