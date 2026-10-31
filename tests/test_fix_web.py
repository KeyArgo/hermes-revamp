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


def test_approval_ducking_cancels_stale_fades_and_keeps_a_user_volume_change():
    text = source("web/player.js")
    assert "let volumeAnimation = 0;" in text
    assert "const animation = ++volumeAnimation;" in text
    assert "if (animation != volumeAnimation) return;" in text
    volume_case = text[text.index('case "volume":'):text.index('case "shuffle":')]
    assert "duck.savedVol = clamp(cmd.percent);" in volume_case
    assert "cancelVolumeAnimation();" in volume_case
    sleep_tick = text[text.index("function sleepTick()"):text.index("function stopSleep()")]
    assert "if (!duck.active)" in sleep_tick
    stop_start = text.index("function stopSleep()")
    stop_sleep = text[stop_start:text.index("api.setSleep", stop_start)]
    assert "else if (sleepFrom != null) try { webamp.setVolume(sleepFrom)" in stop_sleep


def test_a_volume_change_during_the_sleep_fade_is_kept():
    # The fade recomputed every tick from the level it started at, so a user raising
    # the volume in the last minute was overwritten a second later.
    text = source("web/player.js")
    volume_case = text[text.index('case "volume":'):text.index('case "shuffle":')]
    assert "if (sleepFrom != null) sleepFrom = clamp(cmd.percent);" in volume_case


def test_control_mode_focus_asks_the_live_player_to_switch_backends():
    text = source("web/app.js")
    control_api = text[text.index("function installControlPlayerApi()"):text.index("// ---------------------------------------------------------------- boot")]
    assert "focusStart: () => cmd({ action: \"focus_start\" })" in control_api
    assert "window.revampFocusActivate = window.revampPlayerApi.focusStart" in control_api


def test_full_page_control_queue_can_be_cleared_with_confirmation():
    text = source("web/app.js")
    start = text.index("function mountControlStage()")
    control = text[start:text.index("async function boot()", start)]
    assert 'clear.textContent = "Clear queue"' in control
    assert 'confirmDialog("Clear the whole queue?"' in control  # in-page: Hermes blocks window.confirm
    assert 'apiPost("/api/command", { action: "queue_clear" })' in control


def test_full_page_queue_starts_compact_so_library_views_remain_readable():
    text = source("web/app.js")
    start = text.index("function mountControlStage()")
    control = text[start:text.index("async function boot()", start)]
    assert "let queueExpanded = false" in control
    assert 'toggle.textContent = queueExpanded ? "Hide queue" : `Show queue (${list.length})`' in control
    assert "if (!queueExpanded) return" in control


def test_full_player_has_a_confirmed_clear_queue_control():
    extras = source("web/views/npextras.js")
    assert 'clearQueueBtn.textContent = "Clear queue"' in extras
    assert 'confirmDialog("Clear the whole queue?"' in extras  # in-page: Hermes blocks window.confirm
    assert 'viewApi("/api/command", "POST", { action: "queue_clear" })' in extras


def test_mini_player_has_an_explicit_two_click_clear_queue_control():
    text = source("desktop/plugin.js")
    bar = text[text.index("function RevampBar()"):text.index("function PaneHeaderButtons()")]
    assert "const [clearArmed, setClearArmed] = useState(false)" in bar
    assert "command('queue_clear')" in bar
    assert "clearArmed ? t('clearConfirm') : t('clearQueue')" in bar


def test_radio_view_cleans_its_global_listener_before_a_view_swap():
    app = source("web/app.js")
    radio = source("web/views/radio.js")
    set_view = app[app.index("export function setView("):app.index("window.revampSetView")]
    assert 'window.dispatchEvent(new Event("revamp:view-changing"));' in set_view
    assert "const onWindowScroll = () => closeStationMenu();" in radio
    assert 'window.addEventListener("revamp:view-changing", onViewChanging)' in radio
    assert 'window.removeEventListener("scroll", onWindowScroll, true)' in radio


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
    assert "rawOffset = Number.isFinite(page.next_offset)" in text and ": rawOffset + PAGE;" in text  # server-side paging past the old 1000 cap


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


def test_sleep_fade_and_approval_dip_keep_the_real_volume():
    # Sleep fade starting under a dip used to fade from the dipped level; sleep ending
    # under a dip left the un-dip restoring the half-faded level for good.
    text = source("web/player.js")
    sleep_tick = text[text.index("function sleepTick()"):text.index("function stopSleep()")]
    assert "duck.active && duck.savedVol != null ? duck.savedVol : currentVolume()" in sleep_tick
    stop_start = text.index("function stopSleep()")
    stop_sleep = text[stop_start:text.index("api.setSleep", stop_start)]
    assert "if (duck.active && sleepFrom != null) duck.savedVol = sleepFrom;" in stop_sleep


def test_library_load_more_watches_the_panel_that_scrolls():
    # The list lives in #panel-body, not inside .stage: with .stage as root the marker
    # never intersected and only the button loaded more tracks.
    text = source("web/views/library.js")
    assert 'root: listEl.closest(".panel-body")' in text
    assert 'root: document.querySelector(".stage")' not in text


def test_diagnostics_load_on_their_own():
    # Imported only from app.js, a failure elsewhere in app.js's module graph meant the
    # failure recorder never ran.
    html = source("web/index.html")
    assert html.index('src="/web/diagnostics.js"') < html.index('src="/web/app.js"')


def test_unduck_during_a_sleep_fade_does_not_climb_past_the_fade():
    text = source("web/player.js")
    unduck = text[text.index("function unduck("):text.index("function currentVolume()")]
    assert "duck.savedVol != null && sleepFrom == null" in unduck


def test_radio_reads_past_a_page_of_only_broken_stations():
    text = source("web/views/radio.js")
    assert "if (!done && !found.length) return loadNext();" in text
    assert "emptyPages > 4" in text


def test_library_lists_ignore_answers_for_a_view_the_user_left():
    # Opening an album, then switching tab before it loaded, painted the album over the
    # new tab; the artists/albums loads had no ticket at all.
    text = source("web/views/library.js")
    for fn in ("async function loadArtists()", "async function loadAlbums(", "async function renderAlbumTracks()"):
        body = text[text.index(fn):text.index("\n  }\n", text.index(fn))]
        assert "const generation = ++tracksGeneration;" in body, fn
        assert "if (generation !== tracksGeneration) return;" in body, fn


def test_library_failed_load_more_keeps_rows_and_scan_keeps_open_album():
    text = source("web/views/library.js")
    assert "if (requestOffset > 0) {" in text and "couldn't load more" in text
    assert "if (changed && currentAlbum) { /* an open album stays as it is */ }" in text
    assert "currentAlbum = null; // any tab leaves an open album" in text


def test_error_messages_are_shown_as_text():
    app = source("web/app.js")
    assert "Boot failed: ${String(e.message || e)}</div>" not in app
    assert 'box.append(`Boot failed:' in app
    agent = source("web/views/agent.js")
    assert "Cannot load events: ${e.message}</div>" not in agent


def test_radio_play_never_falls_back_to_the_first_station():
    text = source("web/views/radio.js")
    assert "Math.max(0, list.indexOf(st))" not in text
    assert "if (at < 0) window.revampPlayerApi.playItemsAt(0, [stationItem(st)]);" in text


def test_playlist_opens_paint_in_click_order():
    text = source("web/views/playlists.js")
    body = text[text.index("async function openPl("):]
    assert "const seq = ++openSeq;" in body[:400] and "if (seq !== openSeq) return;" in body[:600]


def test_mini_player_queue_edits_survive_a_failed_one():
    text = source("desktop/plugin.js")
    edit = text[text.index("  function edit(change) {"):text.index("  const move = (item, direction)")]
    assert ".catch(() => { heldQueue = null; refreshLive() })" in edit


def test_a_failed_list_page_can_be_fetched_again():
    text = source("desktop/plugin.js")
    assert "seen.current.delete(tag) // a failed page can be asked for again" in text


def test_playlist_play_from_loads_before_it_plays():
    text = source("web/views/playlists.js")
    body = text[text.index("async function playFrom("):text.index("async function append(")]
    assert "if (p.playItemsAt) return p.playItemsAt(i, items);" in body
    assert "await p.loadTracks(" in body
