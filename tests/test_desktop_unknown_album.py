from revamp_core import library


def test_unknown_album_card_returns_untagged_tracks(monkeypatch):
    tracks = [{"id": "a", "album": ""}, {"id": "b", "album": "Other"}]
    monkeypatch.setattr(library, "all_tracks", lambda: tracks)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    card = next(a for a in library.albums() if a["album"] == "Unknown Album")
    result = library.search(album=card["album"])
    assert [track["id"] for track in result["tracks"]] == ["a"]
