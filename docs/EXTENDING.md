# Extending REVamp

REVamp’s player server is local-only and deliberately small: it is a stdlib HTTP server on `127.0.0.1`. This guide describes the supported seams for an extension such as an agent that picks music.

## Local API and events

`GET /api/health` is the only unauthenticated API endpoint. All other `/api/*` routes require `X-Revamp-Token: <token>` (or `?t=` for media/page URLs). The token is `<REVAMP_HOME>/token`, or `<HERMES_HOME>/revamp/token`, or `~/.hermes/revamp/token`. Do not pass it to a model, browser extension, or remote service.

Useful endpoints include `GET /api/state`, `GET /api/playback`, `GET /api/library/tracks?q=`, `GET/POST/PUT/DELETE /api/playlists`, `GET /api/radio/presets`, `POST /api/command`, and `POST /api/agent_event`. `POST /api/command` accepts playback actions including `play`, `pause`, `next`, `prev`, `seek`, `volume`, `shuffle`, `repeat`, and `load`. Use `GET /api/playback` to discover the active backend and its capabilities first.

`GET /api/events` is authenticated server-sent events. It carries `command` events for page players and state-related updates; clients must reconnect and re-read `/api/state` after a disconnect. Agent activity is persisted separately and readable at `GET /api/agent_events`; post it with `POST /api/agent_event` using a small object such as `{"kind":"tool_start","tool":"search","source":"codex"}`.

## revampctl and MCP

`bin/revampctl` reads the local token and exposes `status`, `state`, transport commands, `search`, `queue`, `radio`, and `event`. Run `revampctl mcp` for its stdio JSON-RPC MCP server. It exposes five tools: `revamp_now_playing`, `revamp_control`, `revamp_volume`, `revamp_play_music`, and `revamp_play_radio`. This is the right boundary for an AI music-picker: its process gets no token and asks only for the intended operation.

## Sources

Sources provide catalog items; playback backends execute them. Implement `revamp_core.sources.Source`, give each instance a stable id (for example `service@account`), and register it with `sources.register()`. Implement only the methods you support: `browse`, `search`, `item`, `stream`, login methods, or `route`. The generic authenticated routes under `/api/sources/<id>/...` dispatch to those methods, so a source normally needs no new `server.py` route.

Use canonical queue items from `revamp_core/items.py`: stable `id`, `source`, `kind`, metadata, `playback`, optional `resumeKey`, and capabilities. For remote audio, serve it through `/api/sources/<id>/stream/<key>` so upstream URLs and credentials never reach the browser. `docs/SOURCES.md` has the complete item shape and source route table.

## Backends

The built-in `webamp` backend receives commands over SSE. `focus` follows the same browser-backed pattern. A remote backend such as Spotify subclasses `playback.Backend`, sets `remote = True`, implements `command()`, registers itself with `playback.register_backend()`, and reports its current playback through `store.report_playback()`. Declare only capabilities it actually supports; `/api/command` gates unsupported operations.

Use Hermes-approved secret or OAuth facilities for extension credentials; never put
them in queue items, REVamp state, or browser responses. Add tests for source
contracts and avoid adding broad unauthenticated endpoints.
