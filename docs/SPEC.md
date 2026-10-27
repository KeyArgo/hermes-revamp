# Revamp — SPEC (source of truth for every build lane)

Revamp is a full-featured Winamp clone for 2026, shipped as a Hermes Agent plugin
(and later a Claude Code plugin). It is built on **Webamp 2.3.1** (MIT, vendored at
`web/vendor/webamp.butterchurn-bundle.min.mjs`, license in `web/vendor/WEBAMP-LICENSE.txt`),
which already provides the pixel-faithful Winamp 2 main window, 10-band EQ, playlist editor,
Milkdrop (butterchurn) visualizer, classic `.wsz` skins, shade/double-size modes and hotkeys.

Revamp adds what Webamp lacks: a **media library**, **saved playlists (M3U8/PLS)**,
**internet radio** (presets, Radio Browser directory, favorites, ICY now-playing),
**persistent state**, **resume for long tracks (audiobooks)**, **original skins**,
and **agent integration** (the agent can DJ; the UI reacts to agent activity; volume ducks
while the agent waits for approval).

Rules for all lanes:
- Python: stdlib only (3.11+). `mutagen` is OPTIONAL (try-import); fallback to `ffprobe`
  (subprocess, JSON output, 5 s timeout) then to filename parsing. No FastAPI in the core
  server (only `dashboard/plugin_api.py` uses FastAPI, which Hermes provides).
- Web: vanilla ES modules, no build step, no CDN, no external network from the page
  except our own server. No `alert/confirm/prompt` (the page runs in a sandboxed iframe
  without `allow-modals`) — use in-page modals.
- Only touch the files your lane owns (listed below). Do not edit SPEC.md.
- Keep code readable, commented where non-obvious. No placeholder/TODO stubs for spec'd features.

## Repo layout (repo root == Hermes plugin root)

```
revamp/
  plugin.yaml                 # lane A
  __init__.py                 # lane A — Hermes glue: register(ctx): tools + hooks
  revamp_core/                # lane A — standalone python package
    __init__.py  server.py  library.py  tags.py  playlists.py  radio.py  store.py  client.py  config.py
  dashboard/manifest.json     # lane A
  dashboard/plugin_api.py     # lane A
  tests/                      # lane A (pytest, stdlib-only fixtures; generate audio with ffmpeg if present, skip otherwise)
  web/index.html web/app.js web/app.css web/views/*.js   # lane B
  web/vendor/                 # vendored Webamp (do not edit)
  web/skins/*.wsz             # lane D output (generated)
  tools/make_skins.py         # lane D
  desktop/plugin.js           # lane C
  docs/ref/skinSprites.ts     # Webamp sprite map (MIT) — reference for lane D
```

## Config & data

- Data dir: `$REVAMP_HOME`, else `$HERMES_HOME/revamp`, else `~/.hermes/revamp`. Contains
  `token` (32 hex, mode 0600, created on first start), `state.json`, `library.json` (index cache),
  `playlists/*.m3u8`, `skins/*.wsz` (user skins), `radio_favorites.json`.
- Settings (`revamp_core/config.py`): read Hermes config `plugins.entries.revamp.*` when
  importable (`hermes_cli.config.load_config`/`cfg_get`), else env/CLI. Keys:
  `port` (default **8114**), `library_dirs` (default `["~/Music"]`), `duck_on_approval` (default true),
  `duck_level` (default 0.25).
- Standalone run: `python -m revamp_core.server --port 8114 --library ~/Music [--library DIR] [--data DIR]`.

## HTTP server (lane A, `revamp_core/server.py`)

`ThreadingHTTPServer` bound to **127.0.0.1** only, daemon threads. Started by
`ensure_server()` (idempotent; if the port is already served by a healthy Revamp, do nothing;
if a foreign app holds it, log a loud warning like pixel-office does).

Security: every `/api/*` route except `/api/health` requires the token, via header
`X-Revamp-Token` or query `?t=`. Wrong/missing → 401 JSON. `/api/*` and static routes send
`Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers: X-Revamp-Token, Content-Type`,
`Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS`; answer `OPTIONS` preflight 204.
`GET /` serves `web/index.html` with the token injected as
`<meta name="revamp-token" content="...">` and **no** ACAO header (so other origins can't read the token).
Static: `/web/...` → `web/`, `/vendor/...` → `web/vendor/`, `/skins/builtin/<f>` → `web/skins/`,
`/skins/user/<f>` → data `skins/`. Reject path traversal. Correct content types (`.mjs` → text/javascript, `.wsz` → application/octet-stream).
JSON responses: `application/json; charset=utf-8`. Errors: `{"error": "..."}` with proper status.

### Types
```
Track  = {id, root, path, title, artist, album, album_artist, track_no, disc_no, year, genre,
          duration, bitrate_kbps, sample_rate, channels, codec, size, mtime, has_art, url}
         id = first 16 hex of sha1(abs path); url = "/api/media/<id>"
Item   = {kind: "track"|"stream", title, artist, url, duration|null, track_id|null, station_id|null}
Station= {id, name, url, homepage, tags:[..], codec, bitrate, favicon, country, source: "preset"|"radiobrowser"|"custom"}
```
Item.url for a track is `/api/media/<id>`; for a stream it is the ORIGINAL stream URL
(the page converts it to the proxy URL).

### Routes
- `GET /api/health` → `{"app":"revamp","version":"1.0.0-alpha.3"}` (no token).
- Library (`library.py`, `tags.py`): scans `library_dirs` recursively (follow symlinks, guard loops)
  for `.mp3 .flac .ogg .oga .opus .m4a .m4b .aac .wav .webm`. Incremental: reuse cached entries
  whose (size, mtime) are unchanged. Scan runs in a background thread on start and on rescan.
  - `GET /api/library/tracks?q=&artist=&album=&limit=200&offset=0&sort=artist|album|title|added`
    → `{total, tracks:[Track]}`; `q` matches title/artist/album/path case-insensitively, all words must match.
  - `GET /api/library/artists` → `[{name, albums, tracks}]` sorted.
  - `GET /api/library/albums?artist=` → `[{album, artist, year, tracks, duration, art_track_id}]`.
  - `GET /api/library/status` → `{scanning, count, roots, last_scan}`; `POST /api/library/rescan` → `{started:true}`.
  - `GET /api/media/<id>` → file bytes, `Accept-Ranges: bytes`, full `Range` support (206, 416),
    Content-Type by extension (m4b → audio/mp4). Only ids in the index are served.
  - `GET /api/art/<id>` → embedded cover (mutagen) or `cover|folder|front.(jpg|jpeg|png)` in the track's dir; 404 otherwise.
- Playlists (`playlists.py`): files `playlists/<slug>.m3u8` (`#EXTM3U`, `#EXTINF:<sec>,<artist> - <title>`, then url).
  Track items are written as their absolute file path and mapped back to `/api/media/<id>` on read when indexed.
  - `GET /api/playlists` → `[{id, name, count, duration}]`; `GET /api/playlists/<id>` → `{id, name, items:[Item]}`
  - `POST /api/playlists {name, items}` → created object (slug unique); `PUT /api/playlists/<id> {name?, items?}`; `DELETE /api/playlists/<id>`
  - `POST /api/playlists/import {name, format:"m3u"|"pls", text}`; `GET /api/playlists/<id>/export?format=m3u8|pls` (attachment).
- Radio (`radio.py`):
  - `GET /api/radio/presets` → built-in stations: SomaFM Groove Salad `https://ice1.somafm.com/groovesalad-128-mp3`,
    SomaFM Drone Zone `https://ice1.somafm.com/dronezone-128-mp3`, SomaFM DEF CON Radio `https://ice1.somafm.com/defcon-128-mp3`,
    SomaFM Secret Agent `https://ice1.somafm.com/secretagent-128-mp3`, Nightride FM `https://stream.nightride.fm/nightride.mp3`,
    Chillsynth FM `https://stream.nightride.fm/chillsynth.mp3`, Radio Paradise Main Mix `https://stream.radioparadise.com/mp3-192`.
  - `GET /api/radio/search?q=&tag=&limit=30` → proxy to `https://all.api.radio-browser.info/json/stations/search`
    (`name`, `tag`, `hidebroken=true`, `order=clickcount`, `reverse=true`), UA `Revamp/0.1`, 8 s timeout,
    drop HLS (`.m3u8`, `hls` true) results, map to Station, cache per query 1 h. Network failure → 502 JSON.
  - `GET|POST|DELETE /api/radio/favorites` (POST body Station, DELETE `?id=`), persisted.
  - `GET /api/radio/stream?url=<enc>` → proxies the stream (http/https only; follow redirects; resolve `.pls`/`.m3u`
    playlist URLs to the first stream URL), streaming chunks with ACAO header and upstream Content-Type,
    NO `Icy-MetaData` request header. Needed because WebAudio gets silence from non-CORS streams.
    Close the upstream when the client disconnects.
  - `GET /api/radio/nowplaying?url=<enc>` → `{title}` parsed from ICY `StreamTitle` (request `Icy-MetaData: 1`,
    read `icy-metaint` bytes + one metadata block, 6 s timeout), cache 15 s; `{title:null}` if none.
- Skins: `GET /api/skins` → `[{id:"classic", name:"Classic (Winamp base 2.91)", url:null, builtin:true}]` +
  builtin `web/skins/*.wsz` (url `/skins/builtin/<f>`) + user skins (url `/skins/user/<f>`). Name = filename prettified.
- State (`store.py`, thread-safe, atomic writes):
  - `GET /api/state` → saved state merged with live: `{volume, balance, eq:{on, preamp, bands:[10]}, skin, playlist:[Item],
    current_index, shuffle, repeat, layout, resume:{track_id: seconds}, settings:{duck_on_approval, duck_level, ticker},
    live:{player_connected, now_playing:{title, artist, kind, url, position, duration, status}}}`.
    `player_connected` = a heartbeat arrived in the last 6 s.
  - `PUT /api/state {partial}` → shallow-merge top-level keys, persist (`live` ignored).
  - `POST /api/heartbeat {now_playing}` → update live.
- Commands & events:
  - `POST /api/command {action, ...}` → broadcast as SSE `command`; returns `{ok, delivered:<n clients>}`.
    Actions: `play pause toggle stop next prev` · `seek{seconds}` · `volume{percent}` · `shuffle{on}` · `repeat{on}` ·
    `load{items:[Item], mode:"replace"|"append", play:bool}` · `play_index{index}` · `eq{on?, preamp?, bands?}` ·
    `skin{id}` · `toast{text}` · `seek_by{seconds}` (relative; ignored for streams) ·
    `sleep{minutes | seconds | end_of_track:true}` (0 = off; fades the last ~20 s, pauses, restores volume) ·
    `queue_remove{index}` · `queue_move{from, to}` · `queue_clear`. Unknown action → 400.
  - Likes (local tracks): `GET /api/likes` → `{ids, tracks}` · `POST /api/likes {track_id, liked?:bool}` ·
    `DELETE /api/likes?id=` → `{ids}`; broadcast as SSE `likes {ids}`. Library search records carry `liked`.
  - Heartbeat `now_playing` also carries `index, track_id, shuffle, repeat, sleep:{mode, remaining}|null`.
  - `POST /api/agent_event {kind, tool?, preview?, ok?, session_id?}` (kinds: `tool_start tool_end session_start
    session_end approval_request approval_response`) → stamped with `ts`, kept in a ring buffer (200), broadcast as SSE `agent`.
  - `GET /api/agent_events` → ring buffer.
  - `GET /api/events` (SSE; token via `?t=`) → `event: command|agent|library|state|likes` + `data: <json>`; `: ping` every 15 s.
    Track clients; drop dead ones.

### Python client (`revamp_core/client.py`)
`RevampClient(base=None, token=None)` reading port/token from config/data dir; methods `get(path, **params)`,
`post(path, body)`, `put`, `delete`; raise `RevampUnavailable` when the server can't be reached. Used by Hermes tools
and (later) the Claude Code MCP server. Keep it stdlib (urllib), 5 s timeout.

## Hermes glue (lane A, root `__init__.py`, `plugin.yaml`)

`plugin.yaml`: `name: revamp`, `label: Revamp`, `kind: standalone`, `version: 1.0.0-alpha.3`, `author: Argo`,
description, `provides_tools` (4 below), `hooks` list (below).

`register(ctx)`: register tools (toolset `revamp`) with handler signature `handler(args: dict, **kwargs) -> str`
(JSON string), schema format `{"name","description","parameters":{type:object,properties,required}}`; register hooks;
call `ensure_server()` lazily (first tool call or hook, and once at register in a thread — never block register).
Tools (each returns JSON text; when no player page is connected, include
`"hint": "Open Revamp in Hermes Desktop (sidebar → Revamp) or http://127.0.0.1:<port>"`):
- `revamp_player` action ∈ status|play|pause|toggle|stop|next|previous|seek|volume|shuffle|repeat (+ seconds, percent, on). status returns live now_playing + volume/shuffle/repeat.
- `revamp_library` action ∈ search|play|enqueue|rescan|stats (+ query, artist, album, limit≤50). play/enqueue resolve matching tracks → `load` command.
- `revamp_playlist` action ∈ list|show|create|add|play|delete (+ name, query, items). `add` appends search results for `query`.
- `revamp_radio` action ∈ presets|search|play|favorites|add_favorite (+ query, station_id, url, name). play loads a stream Item.
Hooks: `pre_tool_call`→tool_start (preview: first of args command/path/query/url/pattern, ≤80 chars),
`post_tool_call`→tool_end (ok if no error), `on_session_start`, `on_session_end`, `pre_approval_request`, `post_approval_response`.
Hooks POST `/api/agent_event` fire-and-forget on a daemon thread, 0.5 s timeout, never raise.

`dashboard/manifest.json` = `{"name":"revamp","api":"plugin_api.py"}`; `dashboard/plugin_api.py` (FastAPI `router = APIRouter()`,
self-contained — do not import revamp_core; read token file + port the same way): `GET /info` → `{url:"http://127.0.0.1:<port>", token, ok}`
(ok = /api/health answers), `GET /state` and `POST /command` proxy to the server with the token (5 s timeout, 503 when down).

## Web app (lane B, `web/`)

Served at `/`. Token from the meta tag; all API calls send `X-Revamp-Token`; media/stream/SSE URLs carry `?t=`.
Import Webamp: `import Webamp from "/vendor/webamp.butterchurn-bundle.min.mjs"`.

Layout ("Winamp for 2026"): a dark, modern shell (own CSS custom properties on `:root`, light variant under
`prefers-color-scheme: light`, system-ui font, subtle glass panels, accent color) with
1. **Left sidebar** — nav: Library · Playlists · Radio · Skins · Agent; a now-playing mini card (art, title, artist, live/elapsed); collapsible.
2. **Stage** — the area Webamp renders into (`webamp.renderInto(stageEl)`); main + EQ + playlist windows stacked
   by default, Milkdrop window available. Webamp windows stay classic; everything around them is modern.
3. **Panel** — the active view (library table, playlists, radio, skins, agent log) to the right of the stage.
4. **Agent ticker** — a thin strip at the bottom: scrolling latest agent events (`⚙ terminal: pytest -x` / `✓` / `✗`),
   an activity meter that pulses while tools run, "Hermes is waiting for approval" banner on approval_request.
Responsive: `?embed=1` or width < 900 px → sidebar collapses to an icon rail, panel becomes a slide-over drawer;
width < 520 px → stage only + bottom tab bar. Never horizontal-scroll.

Webamp init: `new Webamp({ initialTracks (from saved playlist), availableSkins (from /api/skins, excluding classic url null),
initialSkin (saved, if not classic), enableHotkeys:true, enableMediaSession:true, windowLayout (saved or default),
handleAddUrlEvent (in-page modal: URL → stream Item), handleLoadListEvent (modal: choose saved playlist),
handleSaveListEvent (modal: name → POST /api/playlists), filePickers:[{contextMenuName:"Revamp Library…", filePicker:
opens library view and resolves with chosen tracks, requiresNetwork:false}] })`.
Item → Webamp Track: `{url: absolute(item.url + ?t), metaData:{title, artist}, duration}`; streams use
`/api/radio/stream?url=<enc>&t=`. Keep a parallel array mapping Webamp playlist entries → Items (for persistence).
Use only the public API where it exists (play/pause/stop/next/prev/seekToTime/setVolume/toggleShuffle/toggleRepeat/
setTracksToPlay/appendTracks/setCurrentTrack/getMediaStatus/getPlaylistTracks/onTrackDidChange/setSkinFromUrl).
For volume read, EQ get/set, position and marquee title, use `webamp.store` (redux: `getState()`, `dispatch`,
action types seen in the bundle: `SET_VOLUME`, `SET_BAND_VALUE {band, value}`, `SET_EQ_ON`, `SET_EQ_OFF`, `SET_MEDIA_TAGS`,
`SET_BALANCE`) — every store access wrapped in try/catch with a graceful no-op; inspect `getState()` shape at runtime
(log it once under `?debug=1`).

Behaviour:
- Restore saved state (volume, shuffle, repeat, EQ, skin, playlist, current index) on load; persist changes debounced (1 s) via PUT /api/state.
- Heartbeat POST /api/heartbeat every 2 s with now_playing.
- SSE `/api/events`: apply `command` events through the Webamp API; `agent` events feed the ticker + Agent view;
  reconnect with backoff; if SSE fails, poll `/api/agent_events` every 5 s.
- **Ducking:** on approval_request (if settings.duck_on_approval) fade volume to duck_level over 400 ms; restore on approval_response or after 120 s.
- **Resume:** tracks with duration > 20 min save position every 10 s into state.resume[track_id]; on starting such a track with a saved
  position > 30 s (and < duration − 30), seek there and toast "Resumed at mm:ss". Clear when finished.
- **Radio now-playing:** while a stream plays, poll `/api/radio/nowplaying` every 15 s; show in mini card + ticker; dispatch SET_MEDIA_TAGS best effort.
- Library view: search (debounced 200 ms, `/` focuses it), tabs Tracks / Artists / Albums (album grid with `/api/art/<id>`), table
  columns # · Title · Artist · Album · Time, paging 200 + "Load more", row double-click/Enter = play now (replace queue from that row's
  result set), buttons Play / Enqueue on selection (shift/ctrl multi-select), scanning indicator + Rescan button, empty state explaining library_dirs.
- Playlists view: list with counts/durations; open → items; Play, Append, Rename, Delete (in-page confirm), "Save current queue",
  Import (file input .m3u/.m3u8/.pls), Export (download links).
- Radio view: Presets, Favorites, Search (Radio Browser) result lists; each row: play, ★ favorite toggle; "Add stream URL".
- Skins view: card grid (Classic + builtin + user); click applies (`setSkinFromUrl`, or reload page with classic for Classic) and saves; note
  that `.wsz` files dropped on the player load directly and that user skins go in `<data>/skins/`.
- Agent view: event log (time, kind, tool, preview, ok) + settings toggles (duck on approval, duck level slider, ticker on/off) saved to state.settings.
- Toasts for errors (network, playback). Keyboard accessible, visible focus, aria labels. Respect prefers-reduced-motion (ticker static).
Split code into `app.js` (bootstrap, api client, player bridge, SSE) and `views/library.js playlists.js radio.js skins.js agent.js`, `modal.js`.

## Hermes Desktop half (lane C, `desktop/plugin.js`)

Plain ESM, only imports `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`; `jsx()` calls, no JSX syntax; theme vars only
(no hardcoded colors); timers/listeners via `ctx.setInterval`/`ctx.addEventListener`/`ctx.onDispose`. Default export
`{ id: 'revamp', name: 'Revamp', register(ctx) }`.
- Fetch connection info with `ctx.rest('/info')` (React Query `useQuery`, refetch every 30 s). If it errors →
  `EmptyState`: "Enable the Revamp Python plugin (plugins.enabled in config.yaml) and restart Hermes" .
- **Pane** `revamp-player` (area `panes`, title "Revamp", `data:{placement:'right', width:'520px'}`): `SandboxedFrame`
  `src = info.url + '/?embed=1'` (token is injected server-side), `sandbox="allow-scripts allow-forms allow-downloads"`, full size.
  Show a note that closing the pane stops playback.
- **Status bar chip** (`statusBar.right`): `♪ <title — artist>` (truncate, marquee scroll on hover unless reduced motion) +
  prev / play-pause / next icon buttons → `ctx.rest('/command', {method:'POST', body:{action}})`; state via `ctx.rest('/state')`
  polled every 3 s by React Query; hidden when no player connected.
- **Sidebar nav** row + **route** `/revamp`: a page with connection status, now-playing, transport buttons, a button to open the
  pane (`ctx.runAction` if a suitable view action exists, otherwise instructions), and the standalone URL.
- **Palette commands** + **keybinds**: Revamp: Play/Pause, Next, Previous, Stop, Open Player.
- On track change, `ctx.pet.say('♪ ' + title, {id:'revamp-np', ttlMs: 5000})` if the pet is visible (max once per 30 s).
- Transcript directive `nowplaying` (`::nowplaying{}`): a compact card with the current track + transport buttons.
- i18n: register `en` strings via `ctx.i18n.register` and use `usePluginI18n`.
Read `~/.hermes/hermes-agent/website/docs/developer-guide/desktop-plugin-sdk.md` and the bundled radio plugin
`~/.hermes/hermes-agent/apps/desktop/src/plugins/radio/plugin.js` for exact SDK exports before writing.

## Original skins (lane D, `tools/make_skins.py` → `web/skins/*.wsz`)

Pillow script that DRAWS ORIGINAL artwork from scratch (do not read or recolor any Nullsoft/Webamp skin bitmaps) for
every sprite listed in `docs/ref/skinSprites.ts` (use it for file names, coordinates and sizes; compute each BMP's canvas
size as the max extent of its sprites, rounded to the standard sizes: main 275×116, titlebar 344×87, cbuttons 136×36,
posbar 307×10, volume 68×433, balance 47×433 (or 68), monoster 58×24, playpaus 42×9, shufrep 92×85, numbers 99×13,
nums_ex 108×13, text 155×18, eqmain 275×315, eq_ex 275×82, pledit 280×186, gen 194×109, genex 256×78 (verify against the map).
Write 24-bit BMPs, plus `viscolor.txt` (24 lines `r,g,b, // comment`), `pledit.txt` (`[Text]` Normal/Current/NormalBG/SelectedBG/Font),
`region.txt` omitted. Zip each as `<Name>.wsz` (files at zip root). Font sheets (`text.bmp`, `numbers.bmp`, `nums_ex.bmp`) must use
legible 5×6 / 9×13 glyphs drawn from an embedded bitmap font table you author, matching FONT_LOOKUP positions.
Skins (each with a coherent palette, beveled buttons with distinct pressed states, readable LCD, seek/volume/balance sliders with thumbs):
- **Revamp Midnight** — deep navy/charcoal, cyan LCD, magenta accents (the default "2026" look).
- **Revamp Synthwave** — purple→pink gradients, sunset-orange LCD, grid lines on the main window.
- **Revamp Paper** — light warm-grey, ink-black text, single red accent (good light mode).
- **Revamp Terminal** — black, phosphor-green LCD, scanline texture.
Also emit `web/skins/previews/<Name>.png` (main window 275×116 composited) for the Skins view. Script is deterministic and
runnable as `python3 tools/make_skins.py`; commit the generated `.wsz` + previews.
