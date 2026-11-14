# VERIFY

Each row is a claim about REVamp, the command that checks it, and the result of running that
command. Numbers are pasted from a terminal, never typed from memory.

Measured on Windows 11 Pro, Python 3.12.10, Node 26, without FFmpeg installed and without
Developer Mode (so tests that need real audio files or symlinks skip; CI runs them on Linux, macOS
and Windows). The suite is offline: no model, no network.

| # | Claim | Command | Result |
|---|---|---|---|
| 1 | The Python suite passes, one process per test file (as CI runs it) | `python tools/run_tests_per_file.py --ignore tests/test_control_mode_play_e2e.py --ignore tests/test_nav_rail_e2e.py --ignore tests/test_hermes_radio.py` | **89/89 files, 907 passed, 0 failed**, 2026-11-14, `release/1.0.0` |
| 2 | The browser-side logic passes | `for t in tests/*.test.mjs; do node --test "$t"; done` | **24/24 files pass** |
| 2b | Shuffle deals every song once per pass, Previous retraces, the audio is the song shown, a pass that plays out stops at its end | `python -m pytest tests/test_shuffle_e2e.py -q` (real player, headless Chrome or Edge) and `node --test tests/shuffle.test.mjs` | passed (38 s); 9/9 deck tests |
| 3 | Tags and durations are read without FFmpeg (ID3v2.3 latin-1 and UTF-16, ID3v2.4 UTF-8) | `python -m pytest tests/test_tags_without_ffmpeg.py -q` | passed, on a machine with no FFmpeg |
| 4 | Every `/api/*` route except `/api/health` needs the token | `python -m pytest tests/test_server_security.py tests/test_security_hardening.py -q` | passed |
| 5 | Programs REVamp starts (ffprobe, ps, the pop-out browser) never receive Hermes's keys | `python -m pytest tests/test_child_env.py -q` | passed; the test also fails any new subprocess call without `env=` |
| 6 | A long playlist name works on Windows (file names are capped; the name is kept whole) | `python -m pytest tests/test_small_bugs.py -q` | passed |
| 7 | The version chip reads the running build, in the suite's format (`v1.0.0-dev.N · date · commit`) | `python -m pytest tests/test_version_chip.py -q` | passed |
| 8 | The "Works well with REVamp" card uses only the plugin SDK and copies real install commands | `python -m pytest tests/test_suite_card.py -q` | passed |
| 9 | No undefined names or unused code in the plugin's Python | `python -m ruff check --select F,E9,PLE revamp_core dashboard __init__.py` | `All checks passed!` |
| 10 | CI runs Linux, macOS and Windows (Python 3.11), Windows on 3.13, and a browser job | `.github/workflows/tests.yml` | five jobs; all green on the 1.0.0 merge to `main` (PR #5) |

## What this does not prove

- **No live Hermes Desktop run.** The Desktop half is checked by tests that load `desktop/plugin.js`
  against a stand-in SDK, not inside the real app.
- **No real stations.** Radio tests use recorded or fake responses; a station that is down today is
  not caught here.
- **No large-library timing.** Nothing here measures scan or search speed on a big collection.
