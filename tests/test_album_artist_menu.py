"""Albums and artists in the Hermes mini player: the same row menu, acting on their tracks.

The renderer is plain JSX loaded uncompiled, so these checks read the source (like
test_plugin_row_menu.py) and the calls the album/artist rows make are asserted in
tests/album_artist_menu.test.mjs for the web page.
"""

from pathlib import Path

PLUGIN = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")


def _between(start, end):
    a = PLUGIN.index(start)
    return PLUGIN[a:PLUGIN.index(end, a)]


def test_row_menu_works_on_a_batch_and_offers_the_playlist_submenu():
    row = _between("function Row(", "\nfunction ")
    # One menu implementation: the same Popover menu for a track, an album or an artist.
    assert "items }" in row.split(")", 1)[0]
    assert "typeof items === 'function'" in row
    assert "command('load', { items: list, mode: 'next' })" in row
    assert "command('load', { items: list, mode: 'append' })" in row
    assert "t('addToPlaylist')" in row
    assert "rest(path, { method: 'PUT', body: { items: [...(cur?.items || []), ...list] } })" in row
    assert "rest('/playlists', { method: 'POST', body: { name: wanted, items: list } })" in row
    assert "t('newPlaylist')" in row
    assert "role: 'group'" in row  # the submenu is a labelled group, not a second menu system
    assert "onKeyDown: onMenuKeyDown" in row
    assert "e.key === 'Escape'" in row


def test_album_and_artist_lists_are_rows_with_the_same_menu():
    albums = _between("function AlbumList(", "function ArtistList(")
    artists = _between("function ArtistList(", "// After the folders change")
    for block, name, path in ((albums, "a.album", "/library/albums"), (artists, "a.name", "/library/artists")):
        assert path in block
        assert "jsx(Row, {" in block
        assert "items: () =>" in block
        assert "onPlay: () => play(" in block
    assert "albumItems(a, version)" in albums
    assert "artistItems(a.name, version)" in artists


def test_album_and_artist_tracks_are_fetched_in_disc_track_order():
    helper = _between("async function allTrackItems(", "const albumItems")
    assert "sort: 'album'" in helper
    assert "limit: '200'" in helper
    assert "offset: String(offset)" in helper
    assert "kind: 'music'" in PLUGIN[PLUGIN.index("const albumItems"):PLUGIN.index("const foldName")]


def test_music_tab_has_a_tracks_albums_artists_switch():
    library = _between("function LibraryList(", "const BooksList")
    assert "revamp-subtabs" in library
    assert "{ id: 'albums', label: t('albums') }" in library
    assert "{ id: 'artists', label: t('artists') }" in library
    assert "jsx(AlbumList, { text: query, np, onPending })" in library
    assert "jsx(ArtistList, { text: query, np, onPending })" in library


def test_new_menu_labels_are_translated():
    for key in ("albums", "artists", "addToPlaylist", "newPlaylist", "addedToPlaylist", "createdPlaylist"):
        assert f"{key}:" in PLUGIN, key


def test_unknown_artist_row_matches_only_uncredited_tracks(tmp_path, monkeypatch):
    from revamp_core import library, store

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_INDEX", {
        "anon": {"id": "anon"}, "credited": {"id": "credited", "artist": "A"}})
    row = next(r for r in library.artists() if r["name"] == "Unknown Artist")
    result = library.search(artist=row["name"])
    assert result["total"] == row["tracks"] == 1
    assert [t["id"] for t in result["tracks"]] == ["anon"]
