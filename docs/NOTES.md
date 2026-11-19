# Revamp: engineering notes

## Webamp 2.3.1 quirks we work around

1. **`setCurrentTrack(index)` takes a track id.** The docs say "zero-based index", but the implementation
   dispatches the value as a track id. With ids 3, 4, 5, `setCurrentTrack(2)` selects a track that
   doesn't exist. Fix: `setTrackIndex()` in `web/player.js` maps index → `state.playlist.trackOrder[index]`.
2. **`getMediaStatus()` has no playing index.** Read `state.playlist.currentTrack` and look it up in
   `trackOrder` instead. Without this, every heartbeat reported track 0.
3. **`getPlaylistTracks()` entries carry `title`/`artist` at the top level, not under `metaData`.** It's also
   unsafe to derive a title from a track URL: ours carry the access token.
4. **Booting with a non-classic `initialSkin` froze the page inside the Revamp shell.** Bare Webamp with the
   same options was fine, and the renderer was blocked rather than busy, so a CDP `Debugger.pause` timed
   out. Switching skins after render works, so `player.js` applies the saved skin with `setSkinFromUrl()`
   after `renderInto()`. The root cause is still open; a candidate for an upstream issue.
5. **`renderWhenReady()` floats the windows over the page centre.** `renderInto(node)` keeps them inside the
   stage, which the narrow/embed layout needs.

## Playing inside Hermes Desktop

- `desktop/plugin.js` creates **one** sandboxed iframe of the player when the server answers. It parks
  the iframe in an off-screen dock and moves it between the `/revamp` page, the Frame pane and the
  floating Winamp overlay with `Element.moveBefore()`, which keeps an iframe running (Electron 40 /
  Chromium has it). Older Chromium falls back to `appendChild` (a reload); state lives on the server,
  so playback resumes.
- The **Frame** is a keep-alive Hermes pane (`PANES_AREA`) that adopts the running iframe, so the player
  can sit beside the chat on the right or left. Hiding it (the ×, or `Ctrl+Alt+F` / `Cmd+Option+F`)
  returns the iframe to the dock and the music keeps playing. A plain pane that disabled the plugin when
  closed would stop the music, which is why the player is not one.
- The page talks to its host over postMessage (`revamp:mode` full|winamp in; `revamp:bbox`, `revamp:ready`
  out). Winamp mode hides the modern shell. The overlay clips the frame to the classic windows'
  bounding box, so the rest of Hermes stays clickable.
- The headless harness (stub SDK via import map) checked: dock → Frame → pop-out → drag → pop-in, the
  overlay sized to the windows, the UI behind it clickable, and playback continuous (position kept
  advancing, 0 reloads). It has since been run in the real Desktop app; the
  `assets/screenshots/hermes-*.png` captures were taken there.

## The status bar (replacing the bundled Radio)

- The widget mirrors Hermes's bundled Radio plugin (`apps/desktop/src/plugins/radio/`): the same layout,
  the same braille waveform, the same order in the status bar. Its picker adds Library and Playlists tabs.
- **Waveform:** the audio lives in the sandboxed player page, so the page computes the trace from
  Webamp's analyser (`webamp.media.getAnalyser()`) and posts 72 samples at 12 fps (`revamp:wave`), only
  while playing.
- **Data:** the picker calls `dashboard/plugin_api.py` routes (`/library/tracks`, `/radio/*`,
  `/playlists*`), which proxy to the server with the token. One `/state` poll (1.5 s) feeds every surface.
- **Click lag:** the page used to report state only every 2 s, so a poll could flip a just-clicked ⏯
  back for a moment. The page now reports at once on play/pause/track changes, and the widget holds
  the clicked state until the server agrees (4 s cap).
- **One engine per app:** a Web Lock (`revamp:engine`) picks the window that runs the player. The
  others wait in the lock queue and take over when it closes; their controls go through the server.
- **Coexistence with Radio:** Radio stops itself when anyone announces `{type: 'play'}` on its
  `hermes:radio:playback` BroadcastChannel. Revamp announces there when it starts playing (once per
  5 s) and pauses when Radio announces.
- Headless checks (real React harness with an SDK stand-in): 21 widget checks (library search and play,
  highlight, waveform, pause/resume, volume, pin/unpin, Radio Browser search, playlists, Radio
  coexistence both ways, second window, takeover) plus the pop-out run, repeated six times clean.

## Process model (Hermes)

- `register()` starts the HTTP server on a daemon thread. Every Hermes process (CLI, gateway, `hermes serve`,
  cron) loads the plugin. The first to bind the port serves it; the others find a healthy server and do
  nothing.
- The long-lived Desktop backend (`hermes serve`) normally owns it. If a short-lived CLI owns it instead,
  the player loses its server when that process exits. The page then reconnects (SSE backoff), and the
  next tool call or hook in any process restarts the server.
- The data dir is `$REVAMP_HOME`, else `$HERMES_HOME/revamp`, else `~/.hermes/revamp`. It holds `token`
  (0600), `state.json`, `library.json`, `now.json` (0600, the terminal fast path), `playlists/`, `skins/`,
  `radio_favorites.json`, `lyrics-cache.json` and `logs/revamp.log` (0600, rotating). Backups live outside
  it, under `~/.local/share/revamp/backups/`.

## Security review notes

- Static routes resolve strictly inside their own folder. An earlier draft allowed the parent folder, which
  would have exposed `<data>/token` via `/skins/user/..%2Ftoken`, readable cross-origin because of
  `ACAO: *`. Tests cover the traversal cases.
- `GET /` fills the `revamp-token` meta tag and sends **no** CORS header. Every other route needs the token.
- The Desktop backend's `/info` doesn't return the token: the pane loads the page, and the page gets the
  token server-side.

## Testing

Full commands for every suite are in [CONTRIBUTING.md](../CONTRIBUTING.md). In brief:

- `python3 -m pytest -q tests/ --deselect tests/test_nav_rail_e2e.py` covers the server, library,
  playlists, radio, state, SSE, tools, traversal, the client-log endpoint and regressions. `-m network`
  adds live Radio Browser / ICY tests. CI runs one process per file across Linux, Windows and macOS.
- `node --test tests/*.test.mjs` covers the web page and desktop DOM contracts (status-bar pills, the
  MilkDrop switch, the album/artist menu, the version chip, layout, diagnostics and the client-log hook).
- Browser end-to-end (`tests/test_*e2e.py`, `tests/test_hermes_radio.py`) needs Chrome (`CHROME_BIN`).
  Coverage includes: library double-click plays the right row and next advances; agent commands reach the
  page; the ticker shows agent events; ducking scales the current volume and restores it; skin switching;
  radio plays through the proxy with ICY titles; every view renders without console errors; the
  narrow/embed (520 px) and wide layouts.
- `hermes plugins validate` and `hermes plugins doctor` both pass.
- Terminal plugin mod: `claude plugin validate claude-code/revamp`, `tsc`, and
  `claude plugin test claude-code/revamp` (13 tests, including the `now.json` no-process fast path).

## Build history

The core was drafted in four short passes, then reviewed and integrated. The Desktop half needed a second pass after two earlier attempts stalled. The skins were four small, check-gated tasks.
