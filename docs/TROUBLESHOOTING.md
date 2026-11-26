# Troubleshooting REVamp

Start with the symptom table. If it does not cover your case, read the log section
below: REVamp writes a private log that names most failures, and one health line a
minute that shows a slow leak before it becomes a crash.

Applies to REVamp 1.0.0 · revised 2026-11-19.

## Symptom → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| No sound | The player is paused, muted, or the wrong output device is selected in the OS. Radio needs the station to be reachable. | Press play. Check the volume slider and the OS output device. For radio, see "Radio not loading" below. |
| The player is missing from the status bar | Hermes is in Simple mode, which hides the status bar at each start. | Switch to Advanced from the command palette. `Ctrl+Shift+S` (`Cmd+Shift+S` on Mac) shows the bar until the next restart. |
| The mini player says offline | The page lost its connection to the server (a restart, or a new key). | Choose **Reconnect**. The server keeps the queue and position. |
| High CPU or GPU use | The MilkDrop visualizer is drawing. | Turn it off: **Settings → REVamp → MilkDrop visualizer → Off**, or the **Turn off** pill on the visualizer. Off really closes the renderer. Terminal sessions no longer poll a process each glance; they read `now.json` (see below). |
| "REVamp restarted with a new key" | The server behind the page was replaced (an update or a restart), so the page's token is stale. | Click **Reconnect** once. The music keeps playing; the controls work again after the page reloads. This is shown once, not on every click. |
| Library is empty | The scanned folder has no audio, or the scan has not finished, or the folder is wrong. | Check **Settings → REVamp → Library** (or the `library_dirs` Hermes setting, or `REVAMP_LIBRARY_DIRS`). The default folder is `~/Music`. Press **Rescan**. A failed scan leaves a warning in the log. |
| Radio not loading | Radio Browser, the station, or its logo host is unreachable, or a stream URL is dead. | Radio needs the public hosts reachable (local playback does not). Try another station. A persistent Radio Browser failure is remembered for a minute; the log names the failing host. |
| Audiobook chapters missing | `ffprobe` (FFmpeg) is not installed. | Install FFmpeg. Basic tags and durations are read without it; chapters need `ffprobe`. |
| Pop-out window will not open | No Chrome/Chromium family browser was found. | Install one, or set `REVAMP_FLOAT_BROWSER` to its executable path. |
| A file will not scan | The file is unreadable or its tags are corrupt. | Set `REVAMP_LOG_LEVEL=DEBUG` and rescan; each unreadable file leaves a debug line naming it. |

## Where the log lives

```
<REVAMP_HOME>/logs/revamp.log
```

The data directory is `$REVAMP_HOME`, else `$HERMES_HOME/revamp`, else
`~/.hermes/revamp`. So the usual path is `~/.hermes/revamp/logs/revamp.log`.

- The file is owner-only (`0600`); the `logs` folder is `0700`. What you listen to
  stays private.
- It rotates at **1 MB** per file and keeps **three** backups: `revamp.log.1`,
  `revamp.log.2`, `revamp.log.3`. The oldest is dropped.
- Setup never raises and never touches Hermes's own logging. A broken log cannot
  stop the player.

Each line is `TIMESTAMP LEVEL LOGGER MESSAGE`, for example:

```
2026-11-19 04:41:00,123 INFO revamp.server revamp: server start, serving at http://127.0.0.1:8114 (v1.0.0-dev.321 · 647e5e0e · 2026-11-19)
```

## Start and stop lines

| Line | Meaning |
|---|---|
| `revamp: server start, serving at http://127.0.0.1:PORT (BUILD)` | The server bound the port and is serving. `BUILD` is the running version stamp. |
| `revamp: server stop on port PORT (interrupted)` | A standalone server (`python -m revamp_core.server`) was stopped with Ctrl+C. |
| `revamp: server stop on port PORT (upgrade hand-over)` | The server stepped aside so a newer REVamp could take the port. |
| `revamp: took over port PORT after its owner went away` | This process claimed a port whose previous owner exited. |
| `revamp: VERSION installed under running OLD; restarting` | A newer REVamp was installed under the running service; it restarts itself when quiet (not while music plays or a window is open). |
| `revamp: cannot bind 127.0.0.1:PORT (REASON)` | The port could not be bound. Another profile's REVamp may hold it; set `REVAMP_PORT`. |

## The once-a-minute health line

Every 60 seconds the sampler writes one line:

```
revamp: health rss_mb=123.4 peak_rss_mb=150.2 threads=12 sse_clients=2 gc=[700, 10, 10] uptime_s=3600.0
```

| Field | Meaning |
|---|---|
| `rss_mb` | Resident memory, in MB, from `getrusage`: the peak so far, which only grows, so a leak still shows as growth. |
| `peak_rss_mb` | The highest RSS seen so far (the same `getrusage` figure). |
| `threads` | Active Python threads in the server process. |
| `sse_clients` | Pages currently listening to the server-sent event stream (`/api/events`). |
| `gc` | Python garbage-collector counts per generation `[gen0, gen1, gen2]`. |
| `uptime_s` | Seconds since the server process started. |

A field is `None` when the platform cannot provide it; the rest of the line still
writes. The sampler is one daemon thread and costs almost nothing.

## The two warnings

The sampler warns when a sample crosses a threshold. These are the lines to look
for when something feels wrong:

| Warning line | Threshold | What it suggests |
|---|---|---|
| `revamp: RSS grew N MB in S s (A -> B MB)` | More than **100 MB** of RSS growth within a **10-minute** sliding window. | A possible memory leak. Compare successive health lines. |
| `revamp: N threads open (limit 200)` | More than **200** threads. | A thread leak (something spawning workers without joining them). |

Set `REVAMP_TRACEMALLOC=1` before starting the server and a memory warning also
lists the top 10 allocation sites:

```
revamp: top allocation sites: 12.3 MB at revamp_core/library.py:210; 4.1 MB at ...
```

This adds allocation tracking (a small overhead) so the warning names the code
holding the memory, not just the total.

## Other lines worth knowing

| Line | Level | Meaning |
|---|---|---|
| `library scan failed` | WARNING | A background library scan raised. The traceback follows. |
| `could not read tags ... FILE` | DEBUG | One file's tags could not be read. Shown only with `REVAMP_LOG_LEVEL=DEBUG`. |
| `revamp: could not write now.json; readers fall back to the server` | WARNING | The terminal now-playing file could not be written (e.g. a full disk). Logged once, not per beat. |
| `page error: MESSAGE [PLACE] \| stack: STACK` | WARNING | A browser-side failure (`window.onerror` / unhandled rejection) forwarded from the page. URL queries and tokens are stripped; at most 20 a minute are logged. |
| `revamp: handler error METHOD PATH` | ERROR | An API request raised. The traceback follows; the URL's token is redacted. |

## Verbose logging

- `REVAMP_LOG_LEVEL=DEBUG` — per-file scan problems and other detail. Default is `INFO`.
- `REVAMP_TRACEMALLOC=1` — add the top allocation sites to memory warnings.

Set these in the environment before the server starts (for a plugin load, before
Hermes starts; for a standalone server, before `python -m revamp_core.server`).

## The diagnostics endpoint

`GET /api/diagnostics` (token required) returns the latest health sample and the
log path, so a bug report can include them without finding the file by hand:

```json
{"sample": {"rss_mb": 123.4, "peak_rss_mb": 150.2, "threads": 12, "sse_clients": 2, "gc_counts": [700, 10, 10], "uptime_s": 3600.0}, "log_file": "/home/you/.hermes/revamp/logs/revamp.log"}
```

Before the sampler's first tick it returns a fresh sample instead of `null`.

## Send a useful bug report

1. Click the **version chip** under the REVamp name to copy the build line
   (`v1.0.0-dev.N · short sha · date`). Paste it into the report.
2. Attach `~/.hermes/revamp/logs/revamp.log` (or `<REVAMP_HOME>/logs/revamp.log`).
   Remove library paths, tokens and personal media names first.
3. Include the output of `GET /api/diagnostics` if you can reach it.
4. For a slow leak, reproduce with `REVAMP_LOG_LEVEL=DEBUG` and, if memory grows,
   `REVAMP_TRACEMALLOC=1`, then attach the warnings and allocation sites.
5. Say what you did, what you expected, and what happened. The player place
   (mini, Frame, pop-out, desktop window, full screen), the skin and the theme
   help narrow it down.

The in-player bug report button prefills a GitHub issue with the version and
environment. Nothing is sent automatically.
