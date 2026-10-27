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
  the iframe in an off-screen dock and moves it to the `/revamp` page or the floating Winamp overlay with
  `Element.moveBefore()`, which keeps an iframe running (Electron 40 / Chromium has it). Older Chromium
  falls back to `appendChild` (a reload); state lives on the server, so playback resumes.
- The page talks to its host over postMessage (`revamp:mode` full|winamp in; `revamp:bbox`, `revamp:ready`
  out). Winamp mode hides the modern shell. The overlay clips the frame to the classic windows'
  bounding box, so the rest of Hermes stays clickable.
- No side pane: closing a plugin's only pane disables the plugin, which would stop the music.
- The headless harness (stub SDK via import map) checked: dock → pop-out → drag → pop-in, the overlay sized
  to the windows, the UI behind it clickable, and playback continuous (position kept advancing, 0 reloads).
  This was not yet run inside the real Desktop app.

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
  (0600), `state.json`, `library.json`, `playlists/`, `skins/` and `radio_favorites.json`.

## Security review notes

- Static routes resolve strictly inside their own folder. An earlier draft allowed the parent folder, which
  would have exposed `<data>/token` via `/skins/user/..%2Ftoken`, readable cross-origin because of
  `ACAO: *`. Tests cover the traversal cases.
- `GET /` fills the `revamp-token` meta tag and sends **no** CORS header. Every other route needs the token.
- The Desktop backend's `/info` doesn't return the token: the pane loads the page, and the page gets the
  token server-side.

## Testing

- `python3 -m pytest -q tests` covers the server, library, playlists, radio, state, SSE, tools, traversal and
  regressions. `-m network` adds live Radio Browser / ICY tests.
- Browser end-to-end (manual harness, headless Chrome via puppeteer-core) covered:
  - library double-click plays the right row, and next advances
  - agent commands reach the page
  - the ticker shows agent events
  - ducking scales the current volume and restores it
  - skin switching works
  - radio plays through the proxy with ICY titles
  - every view renders without console errors
  - the narrow/embed (520 px) layout and the wide layout
- `hermes plugins validate` and `hermes plugins doctor` both pass.
- Claude Code mod: `claude plugin validate`, `tsc`, and `claude plugin test` (2 tests).

## Build history

Drafted by four opencode lanes running GLM-5.3-Flash on Spark A (DeepSeek's opencode key had expired). Claude
reviewed and fixed the integration bugs, and wrote `desktop/plugin.js` after two GLM attempts stalled. The
skins got a second pass as four small, check-gated tasks (`lanes/skin/`).
