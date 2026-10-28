# Sources and playback backends

This explains how to plug a new music source (Plex, Jellyfin, Audiobookshelf...) or a new player
(focus sounds, Spotify) into Revamp. A *source* supplies items. A *backend* plays them. These are
separate: a Plex track is a Plex-source item played by the Webamp backend.

## 1. The queue item

The server, both players, saved state and playlists all use one item shape
(`revamp_core/items.py`, mirrored in `web/items.js`; keep the two in step, a test compares them):

```js
{
  id: "plex@4f1c...:track/5531",   // "<source instance>:<media ref>", stable
  source: "plex@4f1c...",          // the instance that owns it
  kind: "music",                   // music | audiobook | stream | focus ... set by the source, never guessed
  title, artist, album, duration, art,   // art: a Revamp path (/api/...) or an https URL
  playback: { backend: "webamp", url: "/api/sources/plex@4f1c.../stream/track%2F5531" },
  resumeKey: "plex@4f1c...:book/77",     // only with the "resume" capability; defaults to id
  capabilities: ["seek", "resume"],      // what this item allows (see 4)
  // optional, carried through untouched: parent, offset, chapters: [{start, title}], ...
}
```

- `normalize()` / `normalizeItem()` upgrade the old `{kind: "track"|"stream", url, track_id, book}`
  items wherever items come in: `state.json` on load, `PUT /api/state`, `/api/command load`,
  playlists, and the page. Producers that still send the old shape (revampctl, the Hermes tools, the
  views) keep working.
- Built-in ids: `local:track:<tid>` (library), `radio:station:<id>` / `radio:url:<hash>`,
  `url:<hash>` (a bare URL). The hash is FNV-1a 32 of the URL, the same on both sides.
- Items with backend `webamp` need `playback.url`. Paths starting with `/` get the page's token
  added by the player. **Never put an upstream URL or token in an item**: point it at your
  `/api/sources/<instance>/stream/<key>` route and fetch upstream on the server (see 2).
- A bare ref (`{id: "plex@x:track/1", title...}` with no `playback`) is resolved when it is loaded:
  `Source.resolve()` fills `playback` (by default with your stream route).
- `kind` is metadata. Do not infer it from duration or URL. (The only exception is upgrading old
  items without a `book` flag: longer than 20 minutes is still taken as a book, as before.)

## 2. Adding a source (server)

Subclass `revamp_core.sources.Source` and register an instance:

```python
from revamp_core import sources, secrets

class PlexSource(sources.Source):
    type = "plex"
    kinds = ("music", "audiobook")
    features = ("playlists_read", "chapters")   # extra hints for UIs
    owns_progress = True                        # Plex remembers places too

    def __init__(self, machine_id, base_url):
        self.id = f"plex@{machine_id}"          # instance id: [a-z0-9_-]+ optionally @<instance>
        self.name = "Plex"
        self.base = base_url

    def browse(self, path, params):  return {"total": n, "items": [...canonical items...]}
    def search(self, q, params):     return {"total": n, "items": [...]}
    def item(self, media_id):        return {...} or None      # media_id = the part after "<id>:"
    def enrich(self, item):          return item               # refresh a saved item (optional)
    def stream(self, key, request):
        tok = secrets.get(self.id).get("token")
        request.pipe(f"{self.base}/library/parts/{key}", headers={"X-Plex-Token": tok})
    def get_progress(self, key):     return (seconds, updated_at) or None
    def set_progress(self, key, position): ...   # push upstream; debounce + retry yourself
    def login_start(self, body):     return {"code": ..., "url": ..., "poll_after": 2}
    def login_poll(self):            return {"state": "pending" | "done" | "expired"}
    def route(self, method, path, params, body): ...  # anything else (settings, server list)

sources.register(PlexSource("4f1c", "https://plex.example.invalid:32400"))
```

Only override what the source does; the rest answers 404. Raise `sources.SourceError(status, msg)`
for a clean HTTP error. Register at startup (from your module's import, or in `ensure_server`).

Generic routes, no `server.py` edits needed (all need the token):

| Route | Calls |
|---|---|
| `GET /api/sources` | every instance's `describe()`: id, type, name, kinds, features |
| `GET /api/sources/<id>` | `describe()` |
| `GET /api/sources/<id>/browse[/<path>]?...` | `browse(path, params)` |
| `GET /api/sources/<id>/search?q=...` | `search(q, params)` |
| `GET /api/sources/<id>/item/<media ref>` | `item(media_ref)` (URL-encode `/` as `%2F`) |
| `GET /api/sources/<id>/stream/<key>` | `stream(key, request)` |
| `POST /api/sources/<id>/login/start`, `GET .../login/poll` | `login_start(body)`, `login_poll()` |
| anything else under `/api/sources/<id>/` | `route(method, path, params, body)` |

Streaming helpers on `request` (the HTTP handler):

- `request.send_file(path)`: a local file with Range (200/206/416).
- `request.pipe(url, headers)`: proxy upstream. Forwards the page's `Range`, keeps the upstream
  status and `Content-Type/Length/Range`, `Accept-Ranges`. Upstream 401/403/404/416 come back as
  JSON errors with that same code; anything else is 502. Your headers
  (tokens) never reach the browser.
- `request.headers` is there if you need more.

**Secrets** (tokens and refresh tokens) must be supplied through Hermes-approved
secret or OAuth facilities, never stored by REVamp or included in state, items,
or replies.
Per-instance settings that are not secret (url, enabled, "this library is books") can live in your
source's own `route()` endpoints and a settings file of your choosing.

**Desktop**: `dashboard/plugin_api.py` proxies every `/sources/*` and `/playback/*` call (GET, POST,
PUT, DELETE; JSON only; `..`, encoded slashes and `/stream/` refused). From `desktop/plugin.js`:
`rest('/sources/plex@4f1c/browse/albums')`. Audio is fetched by the player page, never through it.

## 3. Progress (resume)

- Saved under the item's `resumeKey`, not the library: `POST /api/resume {key, position}`
  (`position` null or 0 clears), `GET /api/resume?key=...` -> `{key, position}`.
  `POST /api/library/resume {track_id}` still works and saves under `local:track:<tid>`.
- The page saves every 10 s and at once on pause/stop/track change, for items with the `resume`
  capability, and seeks to the saved place when such an item starts.
- It is written to `state.json` (`resume`, `resume_at`) first, always. If the source has
  `owns_progress`, `set_progress()` is then called (exceptions are swallowed: never block playback)
  and on read `get_progress()` wins when its `updated_at` is newer.
- Old `state.json` files keyed by bare track ids are migrated on load.
- Books: give every file/chapter of a book the same `resumeKey` (the book), plus `parent`/`offset`
  if a book spans files. (The page currently seeks within the current file only; a multi-file book
  that needs "jump to file N at offset" is the Plex/books lane's to add in `maybeResume`.)

## 4. Playback backends and the controller

`revamp_core/playback.py`. Commands still go to `POST /api/command {action, ...}`.

- The active backend is the one the latest fresh player report names (`state.live.playback.backend`),
  else `webamp`. A `load` goes to `items[0].playback.backend`.
- Every command is checked against the current capabilities. If the backend or item can't do it the
  reply is `{"ok": false, "unsupported": true, "capabilities": [...]}` (HTTP 200) and nothing is sent.
  `load`, `skin` and `toast` are never gated.
- Capability names are the action names: `play pause toggle stop next prev seek volume shuffle repeat
  play_index eq`. A backend declares the ones it supports; an item can withhold `seek` (live streams
  do). Effective = backend caps minus withheld item caps (`effective_capabilities`).
- Browser backends (`remote = False`) get the command over SSE as before, with `cmd.backend` set.
  Reply: `{"ok": true, "accepted": delivered > 0, "applied": false, "delivered": n, "backend"}`;
  the page's next report confirms it.
- Remote backends (`remote = True`, e.g. Spotify) run `command(cmd)` on the server; reply carries
  `applied` from it. They report what they play with `store.report_playback({...})` at least every
  6 s while active (same fields as below).
- `GET /api/playback` -> `{active, playback, capabilities, backends}`; `state.live.playback` has
  `item_id, source, backend, kind, resume_key, capabilities, title, artist, position, duration,
  status (lowercase), updated, connected`. UIs grey out what is not in `capabilities`.

Register a backend: `playback.register_backend(playback.Backend("focus", ["play", "pause", "toggle",
"stop", "volume"]))` (or a `remote = True` subclass with `command()`).

### In the page (`web/player.js`)

- Heartbeat (`/api/heartbeat`, every 2 s and on play/pause/track change) keeps the old fields (`kind`
  is still `track|stream`, `url` is a station's own URL for radio) and adds `item_id, source,
  media_kind, backend, resume_key, capabilities`.
- Webamp's queue: every track we hand Webamp gets `#rq<n>` at the end of its URL and `entries` maps
  n -> item. Items are found by that id (never by URL or position), so duplicates stay distinct and
  a queue swap in progress can't detach metadata. `revampPlayerApi.items` is the queue in Webamp's
  order.
- Another backend in the page registers an executor:

```js
window.revampPlayerApi.registerBackend("focus", {
  load(items, { mode, play }) {},          // items whose playback.backend is "focus"
  command(cmd) { return true },            // true = handled (play/pause/stop/volume/...)
  status() { return { item, status: "PLAYING", position, duration, capabilities } },
})
```

`loadTracks()` hands a list whose first item targets another backend to that executor (pausing
Webamp) and makes it active; heartbeats then report its `status()`. Loading Webamp items makes Webamp
active again and sends the other executor `{action: "stop"}`. Mixed lists are split: only the first
item's backend gets its items.

## 5. Playlists

Revamp's `.m3u8` files keep each canonical item on a `#EXTREVAMP:<json>` line before its EXTINF
and location, so items from any source round-trip; other players ignore it. Exports leave those lines
out (locations: library file path, a station's own URL, else the playback URL). Old files without
them load as before (and come back canonical).

## 6. Testing a source

`tests/source_contract.py::check_source(client, instance)` runs browse -> item -> Range fetch of the
play URL (must be 206 and stay on `/api/`) -> save progress -> read it back. `tests/test_sources.py`
runs it for `local` and for a fake proxied source with a fake upstream server that needs a token
and honours Range; copy that pattern (a fake server per source, e.g. under `tests/fakes/`).
