"""Regression coverage for confirmed 2026-10-25 web bug-hunt findings."""

from __future__ import annotations

from pathlib import Path

from revamp_core import library


ROOT = Path(__file__).parents[1]


def source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_failed_playlist_save_does_not_mutate_editor_data_before_success():
    text = source("web/views/playlists.js")
    assert text.index("await api(`/api/playlists/${data.id}`, \"PUT\", { items });") < text.index("data.items = items;")


def test_native_webamp_settings_are_persisted_from_the_store():
    text = source("web/player.js")
    assert "function persistNativeSettings(st)" in text
    assert "persistNativeSettings(st);" in text
    assert "balance:" in text
    assert "patch.eq" in text


def test_approval_ducking_does_not_persist_the_temporary_volume():
    text = source("web/player.js")
    assert "animateVolume(duck.savedVol, clamp(levelPct), 400, false);" in text
    assert "animateVolume(clamp(currentVolume()), duck.savedVol, 400, false);" in text


def test_media_session_commands_use_the_active_backend_router():
    text = source("web/player.js")
    assert 'set("pause", () => handleCommand({ action: "pause" }));' in text
    assert 'set("play", () => handleCommand({ action: "play" }));' in text


def test_focus_rejects_end_of_track_sleep_timer():
    text = source("web/player.js")
    assert 'info.item.kind === "focus"' in text
    assert "This source has no track end" in text


def test_title_bar_close_stops_the_active_backend():
    text = source("web/player.js")
    close_start = text.index("webamp.onWillClose")
    close_end = text.index("webamp.onMinimize", close_start)
    assert 'handleCommand({ action: "stop" });' in text[close_start:close_end]


def test_album_search_matches_exact_album_and_artist(monkeypatch):
    text = source("revamp_core/library.py")
    assert 'a == (t.get("artist") or "").casefold()' in text
    assert 'al == (t.get("album") or "").casefold()' in text
    tracks = {
        "a": {"id": "a", "artist": "Artist A", "album": "Live", "title": "A"},
        "b": {"id": "b", "artist": "Artist B", "album": "Live", "title": "B"},
        "c": {"id": "c", "artist": "Artist A", "album": "Live at Leeds", "title": "C"},
    }
    monkeypatch.setattr(library, "all_tracks", lambda: list(tracks.values()))
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "kind_of", lambda *_args: "music")
    result = library.search(artist="Artist A", album="Live", kind="music")
    assert [track["id"] for track in result["tracks"]] == ["a"]


def test_album_detail_keeps_server_total_for_load_more():
    text = source("web/views/library.js")
    assert 'limit: "200", offset: "0"' in text
    assert "total = res.total ?? tracks.length;" in text


def test_radio_lists_page_in_with_an_intersection_observer_sentinel():
    text = source("web/views/radio.js")
    assert "new IntersectionObserver" in text
    assert "radio-sentinel" in text
    assert "rawOffset += PAGE;" in text     # server-side paging past the old 1000 cap


def test_cancelled_station_name_prompt_aborts_add_stream():
    text = source("web/views/radio.js")
    assert "if (name === null) return;" in text


def test_like_button_keydown_cannot_activate_its_track_row():
    text = source("web/views/library.js")
    assert 'if (e.key === "Enter" && e.target === tr) activate("play");' in text
    assert 'b.addEventListener("keydown", (e) => e.stopPropagation());' in text


def test_radio_action_button_keydown_cannot_replay_its_row():
    text = source("web/views/radio.js")
    assert 'if (e.key === "Enter" && e.target === row) play.click();' in text


def test_modal_dialog_is_not_inside_an_aria_hidden_backdrop():
    text = source("web/views/modal.js")
    assert "backdrop.setAttribute('aria-hidden', 'true');" not in text


def test_artist_rows_are_keyboard_activatable():
    text = source("web/views/library.js")
    artist_start = text.index("function renderArtists()")
    artist_end = text.index("function renderAlbums()", artist_start)
    artists = text[artist_start:artist_end]
    assert "tr.tabIndex = 0;" in artists
    assert 'e.key === "Enter" || e.key === " "' in artists


def test_playlist_action_buttons_have_explicit_accessible_names():
    text = source("web/views/playlists.js")
    assert 'b.setAttribute("aria-label", name);' in text
    assert '"Play playlist"' in text
    assert '"Rename playlist"' in text
