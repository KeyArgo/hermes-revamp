"""A playlist made outside the Playlists page (Save queue, Music's Add to playlist) shows up there right away."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_playlists_view_reloads_on_change_event():
    src = (WEB / "views" / "playlists.js").read_text(encoding="utf-8")
    assert 'addEventListener("revamp:playlists-changed"' in src
    assert "refreshLive = { el: listEl" in src


def test_every_outside_writer_announces_the_change():
    for rel in ("player.js", "views/library.js"):
        src = (WEB / rel).read_text(encoding="utf-8")
        assert 'dispatchEvent(new Event("revamp:playlists-changed"))' in src, rel


def test_event_stream_forwards_playlist_changes_to_the_open_view():
    src = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'es.addEventListener("playlists"' in src
    assert 'new Event("revamp:playlists-changed")' in src
