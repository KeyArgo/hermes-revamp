# Contributing to REVamp

Use Python 3 and work from a checkout. The core uses the standard library; do not
add a dependency unless the project adopts it. Keep changes readable, preserve
existing behavior, and add tests for behavior changes.

Applies to REVamp 1.0.0 · revised 2026-11-19.

## Running the tests

There are four suites. Run the ones your change touches, and the Python and Node
suites before opening a pull request.

### Python (server, library, playlists, radio, state, SSE, tools, security)

```bash
python3 -m pytest -q tests/ --deselect tests/test_nav_rail_e2e.py
```

`tests/test_nav_rail_e2e.py` drives a real browser and needs Chrome (below); the
deselect skips it for a fast run. FFmpeg is optional: with it, fixtures are real
sine-wave audio; without it, the scanner still indexes extension-valid files.
`-m network` adds live Radio Browser / ICY tests (off by default).

CI runs one Python process per test file (`tools/run_tests_per_file.py`) across
Linux, Windows and macOS, because watcher threads and servers a file starts must
not disturb a later file. To reproduce that locally:

```bash
python3 tools/run_tests_per_file.py --ignore tests/test_nav_rail_e2e.py
```

### Node (web page and desktop DOM contracts)

```bash
node --test tests/*.test.mjs
```

These run against a fake host DOM, no browser needed. They cover the status-bar
pills, the MilkDrop switch, the album/artist menu, the version chip, layout,
diagnostics and the client-log hook, among others.

### Browser end-to-end (needs Chrome)

```bash
CHROME_BIN=google-chrome python3 -m pytest -q tests/test_nav_rail_e2e.py
# the full browser set, as CI runs it:
CHROME_BIN=google-chrome python3 -m pytest -q tests/test_*e2e.py tests/test_hermes_radio.py
```

Set `CHROME_BIN` to a Chrome or Chromium executable. CI runs these in a separate
Ubuntu job so Chrome and `websocket-client` do not make the fast unit matrix flaky.

### The terminal plugin (Claude Code mod)

The mod's tests import `claude-code/testing` and run through the Claude Code CLI,
not `node --test`:

```bash
claude plugin validate claude-code/revamp
claude plugin test claude-code/revamp
```

They cover the now-playing band, the `now.json` fast path (no process per glance),
the `/revamp` command, transport labels and approval handling. Type-check the
declaration files with `tsc` against `claude-code/revamp/types/index.d.ts`.

## Screenshots

Capture release screenshots with:

```bash
nice node tools/screenshots.mjs
```

It boots the real server against a temporary data directory and generated
sine-wave audio, so it never touches a real music folder. Audio is muted
(`--mute-audio`). Set `CHROME_BIN` when Chrome is not on the default path, and
`REVAMP_CAPTURE_STAGE` to capture one group (`player`, `views`, `modes`, `extras`,
`scenes`, `tour`). The headless MilkDrop canvas renders idle on the pure-sine
fixture; the real-Desktop captures show it running.

## Commit and code style

- **No AI assistant or model names** in commit messages, code comments, source
  files, or test names. No `Co-Authored-By: <assistant>` trailers and no
  "written by <model>" notes. Describe the change and the finding, not who or what
  produced it. Refer to a host app by its product name only where it is the subject.
- Do not add tokens, library paths or private media metadata to fixtures,
  screenshots or issues.
- Match the surrounding style. The web half is plain ESM; the desktop half uses
  `jsx()` calls only and imports limited to the plugin SDK, React and its JSX
  runtime.

## Where to read next

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the processes and where state lives.
- [docs/EXTENDING.md](docs/EXTENDING.md) — the local API, sources and backends.
- [docs/SOURCES.md](docs/SOURCES.md) — the item shape and source route table.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — the log and health lines.
- [SECURITY.md](SECURITY.md) — the token boundary and private reporting.
