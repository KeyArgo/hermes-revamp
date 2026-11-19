# REVamp architecture

One player state on a loopback server feeds every surface and the agent. This page
maps the processes, what each owns, and where the state lives.

Applies to REVamp 1.0.0 · revised 2026-11-19.

## The shape

```
                 Hermes processes (CLI · gateway · serve · cron)
                 each loads the plugin; the first to bind the port serves
                                       │
                                       ▼
     ┌──────────────────────────────────────────────────────────────┐
     │  revamp_core/server.py  ·  127.0.0.1:8114 (loopback only)       │
     │  token-gated /api/*  ·  SSE /api/events  ·  static web/         │
     │  the single source of truth: queue, volume, position,           │
     │  library index, playlists, likes, settings, skins               │
     └──────────────────────────────────────────────────────────────┘
         ▲               ▲                  ▲                 ▲
         │ HTTP + SSE    │ HTTP via proxy   │ reads now.json  │ stdio MCP
         │               │                  │ (no process)    │ (reads token)
   ┌───────────┐  ┌───────────────┐  ┌──────────────┐  ┌────────────────┐
   │ web page  │  │ Hermes Desktop│  │ terminal     │  │ revampctl mcp  │
   │ Webamp in │  │ half          │  │ plugin       │  │ other agent    │
   │ a sandbox │  │ desktop/      │  │ now-playing  │  │ apps; 5 tools  │
   │ iframe or │  │ plugin.js +   │  │ band         │  │                │
   │ a browser │  │ dashboard/    │  │ claude-code/ │  │                │
   │ tab/pop-out│  │ plugin_api.py │  │ revamp       │  │                │
   └───────────┘  └───────────────┘  └──────────────┘  └────────────────┘
```

## Which process owns what

- **The server (`revamp_core/server.py`)** owns the player state and the library.
  It is a stdlib `http.server` bound to `127.0.0.1` only (default port 8114,
  `$REVAMP_PORT` to change). Every Hermes process loads the plugin; the first to
  bind the port serves it, and the others find a healthy server and do nothing.
  The long-lived Desktop backend (`hermes serve`) normally owns it. A REVamp only
  takes over an address it can prove is its own, and holds a lease while it runs.
  Background daemon threads: the HTTP listener, the once-a-minute health sampler,
  the update watcher, the backup thread, and library scans.

- **The web page (`web/`)** is the player UI: Webamp (the classic player), the
  library/radio/books views, themes and the MilkDrop visualizer (Butterchurn). It
  runs in a sandboxed iframe inside Hermes Desktop, or in a browser tab, or in the
  pop-out window. It holds no authoritative state: it reads `/api/state`, sends
  commands to `/api/command`, and follows the SSE stream. One engine per app: a
  Web Lock (`revamp:engine`) picks the window that runs the player; the others
  wait and take over when it closes.

- **The Hermes Desktop half (`desktop/plugin.js` + `dashboard/plugin_api.py`)**
  owns the host integration: the status-bar pills and place menu, the sidebar
  entry, the settings page, the iframe dock (it moves one iframe between the page
  and the floating overlay with `Element.moveBefore()` so playback never restarts),
  and the pop-out window. `dashboard/plugin_api.py` is the Desktop backend's REST
  door; it proxies to the server with the token, so the page never hands the token
  to the host.

- **The terminal plugin (`claude-code/revamp`)** owns the now-playing band in a
  terminal agent app. It reads `now.json` (below) instead of starting a process per
  glance, and feeds agent activity to the ticker only while a player is open.

- **`revampctl mcp` (`bin/revampctl`)** is a stdio JSON-RPC MCP server for other
  agent apps. It reads the local token itself and exposes five tools
  (`revamp_now_playing`, `revamp_control`, `revamp_volume`, `revamp_play_music`,
  `revamp_play_radio`). The agent's process gets no token; it asks only for the
  intended operation.

## Where state lives

The data directory is `$REVAMP_HOME`, else `$HERMES_HOME/revamp`, else
`~/.hermes/revamp`:

| Path | Contents |
|---|---|
| `token` | The per-install API token (`0600`). |
| `state.json` | Player state: queue, current index, volume, position, settings, skin, likes, agent events. |
| `library.json` | The scanned library index (tags, durations). |
| `now.json` | A few bytes of now-playing for terminal readers (`0600`). |
| `playlists/` | Saved playlists. |
| `skins/` | Installed skins. |
| `radio_favorites.json` | Favourite stations. |
| `lyrics-cache.json` | Cached LRCLIB lyrics. |
| `logs/revamp.log` | The private rotating log (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)). |

Backups live outside the data directory, under `~/.local/share/revamp/backups/`
(or `$REVAMP_BACKUP_DIR`), written privately every 10 minutes.

## now.json: the terminal fast path

While a player reports, the server writes `now.json` (on a change, else at most
every 5 seconds). It holds `connected`, `title`, `artist`, `status` and `updated`.
A terminal session stats the file every 5 seconds and reads it only when it
changed: no process, no request, nothing at all while REVamp sits unused. A file
older than 15 seconds means no player. A server from before `now.json` is asked
with `revampctl state`, at most once a minute. This replaced a per-session Python
poll every 4 seconds that used about two thirds of a CPU core with a dozen sessions
open.

## The agent boundary

Four Hermes tools (`revamp_player`, `revamp_library`, `revamp_playlist`,
`revamp_radio`) let the agent DJ through the same server. Hooks feed the activity
ticker and the optional approval chime/dip. Other agent apps use `revampctl mcp`.
In every case the token stays on disk and is read by the server-side or CLI code,
never handed to a model.

## More detail

- The local API, sources and backends: [EXTENDING.md](EXTENDING.md).
- The item shape and source route table: [SOURCES.md](SOURCES.md).
- Webamp quirks and the Desktop iframe model: [NOTES.md](NOTES.md).
- Logs, health lines and warnings: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
